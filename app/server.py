#!/usr/bin/env python3
"""Stdlib-only backend for the "watermarks-remover Manual" web UI.

Serves the static frontend and exposes a small JSON API that shells out to the
upstream Layer A CLI scripts (inspect_file.py / clean_file.py). No third-party
dependencies, no `cgi` module (removed in Python 3.13) - input and output are
plain JSON with base64 for file payloads.

See API_CONTRACT.md for the frozen request/response shapes.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = (APP_DIR / "static").resolve()

_repo_env = os.environ.get("WATERMARKS_REPO")
REPO_DIR = (
    Path(_repo_env).expanduser().resolve()
    if _repo_env
    else (APP_DIR.parent / "watermarks-remover").resolve()
)
INSPECT_SCRIPT = REPO_DIR / "service" / "scripts" / "inspect_file.py"
CLEAN_SCRIPT = REPO_DIR / "service" / "scripts" / "clean_file.py"

VERSION = "1.0.0"
SERVICE_NAME = "watermarks-web"

MAX_INPUT_BYTES = 25 * 1024 * 1024  # 25 MiB raw input limit
# 25 MiB raw -> at most ~34 MiB base64, plus JSON envelope slack.
MAX_BODY_BYTES = (MAX_INPUT_BYTES * 4) // 3 + (1 << 20)
SUBPROCESS_TIMEOUT = 60

DEFAULT_OPTIONS = {
    "nfkc": False,
    "normalize_spaces": True,
    "strip_emoji_glue": False,
    "strip_bidi": False,
    "aggressive_homoglyphs": False,
}

# Report keys that can carry absolute host paths; never leak these.
_PATH_REPORT_KEYS = ("path", "input", "output")

_MIME_OVERRIDES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}


class ApiError(Exception):
    """Error that maps directly onto a JSON error response."""

    def __init__(self, status: int, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def sanitize_name(name: object, fallback: str = "pasted.txt") -> str:
    """Reduce a client-supplied filename to a safe basename."""
    if not isinstance(name, str) or not name.strip():
        return fallback
    cleaned = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = cleaned.replace("\x00", "")
    cleaned = re.sub(r"[\r\n\t]", "_", cleaned).strip()
    if cleaned in ("", ".", ".."):
        return fallback
    return cleaned[:255]


def cleaned_name(name: str, suffix: str = ".cleaned") -> str:
    """mirror upstream common.cleaned_path(): file.ext -> file.cleaned.ext."""
    p = Path(name)
    if p.suffix:
        return f"{p.stem}{suffix}{p.suffix}"
    return f"{name}{suffix}"


def normalize_options(raw: object) -> dict:
    opts = dict(DEFAULT_OPTIONS)
    if isinstance(raw, dict):
        for key in DEFAULT_OPTIONS:
            if key in raw:
                opts[key] = bool(raw[key])
    return opts


def sanitize_report(report: object):
    """Drop keys that can carry absolute host paths; pass the rest through."""
    if not isinstance(report, dict):
        return report
    return {k: v for k, v in report.items() if k not in _PATH_REPORT_KEYS}


def parse_json_stdout(raw: bytes | str | None) -> dict:
    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    raw = raw.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def run_cli(script: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        cwd=str(REPO_DIR),
        timeout=SUBPROCESS_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# clean_file flag capability detection
#
# The frozen contract maps options onto clean_file flags that this checkout of
# upstream does not all define (--no-normalize-spaces / --strip-emoji-glue /
# --strip-bidi live on clean_text.py, not clean_file.py). Passing an
# unsupported flag makes argparse exit 2, which we would misreport as 422
# "unrecognized format". Detect what the script actually accepts once and drop
# only the unsupported extras; the contract mapping is used verbatim when the
# flags exist.
# ---------------------------------------------------------------------------

_clean_flag_cache: set[str] | None = None
_clean_flag_lock = threading.Lock()


def clean_supported_flags() -> set[str]:
    global _clean_flag_cache
    if _clean_flag_cache is None:
        with _clean_flag_lock:
            if _clean_flag_cache is None:
                try:
                    proc = subprocess.run(
                        [sys.executable, str(CLEAN_SCRIPT), "--help"],
                        capture_output=True,
                        cwd=str(REPO_DIR),
                        timeout=30,
                    )
                    text = (proc.stdout or b"").decode("utf-8", "replace") + (
                        proc.stderr or b""
                    ).decode("utf-8", "replace")
                except Exception:
                    text = ""
                _clean_flag_cache = set(re.findall(r"--[a-z][a-z0-9-]+", text))
    return _clean_flag_cache


def build_clean_flags(options: dict) -> list[str]:
    flags: list[str] = []
    if options["nfkc"]:
        flags.append("--nfkc")
    if not options["normalize_spaces"]:
        flags.append("--no-normalize-spaces")
    if options["strip_emoji_glue"]:
        flags.append("--strip-emoji-glue")
    if options["strip_bidi"]:
        flags.append("--strip-bidi")
    if options["aggressive_homoglyphs"]:
        flags.append("--aggressive-homoglyphs")
    supported = clean_supported_flags()
    if supported:
        flags = [f for f in flags if f in supported]
    return flags


def build_inspect_flags(options: dict) -> list[str]:
    flags: list[str] = []
    if options["aggressive_homoglyphs"]:
        flags.append("--aggressive")
    return flags


# ---------------------------------------------------------------------------
# Request parsing / derives
# ---------------------------------------------------------------------------


def parse_input(body: dict):
    """Return (mode, name, payload_bytes, is_text, text_or_None)."""
    options = normalize_options(body.get("options"))

    if isinstance(body.get("file_base64"), str):
        encoded = body["file_base64"].strip()
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(400, "File base64 tidak valid")
        if len(data) > MAX_INPUT_BYTES:
            raise ApiError(413, "Ukuran masukan melebihi 25 MiB")
        name = sanitize_name(body.get("filename"), "upload.bin")
        return "file", name, data, False, None, options

    if isinstance(body.get("text"), str):
        text = body["text"]
        data = text.encode("utf-8", "surrogateescape")
        if len(data) > MAX_INPUT_BYTES:
            raise ApiError(413, "Ukuran masukan melebihi 25 MiB")
        supplied = body.get("filename")
        name = sanitize_name(supplied, "pasted.txt") if supplied else "pasted.txt"
        return "text", name, data, True, text, options

    raise ApiError(
        400,
        "Permintaan tidak lengkap: sertakan 'text' atau 'filename' + 'file_base64'",
    )


def derive_suspicious(kind: str, report: dict) -> bool:
    if kind == "text":
        try:
            return int(report.get("suspicious_total", 0)) > 0
        except (TypeError, ValueError):
            return False
    return bool(
        report.get("has_c2pa")
        or report.get("has_ai_metadata")
        or (report.get("layer_a_total") or 0)
    )


def inspect_summary(kind: str, report: dict) -> str:
    if kind == "text":
        try:
            count = int(report.get("suspicious_total", 0))
        except (TypeError, ValueError):
            count = 0
    else:
        findings = report.get("findings")
        count = len(findings) if isinstance(findings, list) else 0
    if count > 0:
        return f"{count} tanda ditemukan"
    return "Tidak ada tanda yang ditemukan"


def text_clean_summary(report: dict) -> str:
    stats = report.get("stats") if isinstance(report.get("stats"), dict) else {}
    try:
        removed = int(stats.get("removed_count", 0))
    except (TypeError, ValueError):
        removed = 0
    try:
        replaced = int(stats.get("replaced_count", 0))
    except (TypeError, ValueError):
        replaced = 0
    if removed == 0 and replaced == 0:
        return "Tidak ada tanda yang ditemukan"
    return f"{removed} karakter dihapus, {replaced} spasi diganti"


def guess_mime(download_name: str, fallback: str = "application/octet-stream") -> str:
    import mimetypes

    ctype, _ = mimetypes.guess_type(download_name)
    return ctype or fallback


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE_NAME}/{VERSION}"

    # -- low level responses -------------------------------------------------

    def _send_bytes(self, status: int, ctype: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if payload:
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_json(self, status: int, obj: dict) -> None:
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", payload)

    def _send_error_json(self, status: int, message: str, extra: dict | None = None) -> None:
        body = {"ok": False, "error": message, "kind": "unknown"}
        if extra:
            body.update(extra)
        self._send_json(status, body)

    def _send_plain(self, status: int, message: str) -> None:
        self._send_bytes(status, "text/plain; charset=utf-8", message.encode("utf-8"))

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib hook
        sys.stderr.write(
            "%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args)
        )

    # -- GET -----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        path = urlparse(self.path).path
        if path == "/":
            self._serve_static("index.html")
            return
        if path == "/favicon.ico":
            self._serve_static("favicon.ico")
            return
        if path.startswith("/assets/"):
            self._serve_static(path[len("/assets/"):])
            return
        if path == "/api/health":
            repo_ok = bool(INSPECT_SCRIPT.is_file() and CLEAN_SCRIPT.is_file())
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": SERVICE_NAME,
                    "version": VERSION,
                    "repo": repo_ok,
                    "python": platform.python_version(),
                },
            )
            return
        self._send_error_json(404, "Tidak ditemukan")

    def _serve_static(self, rel: str) -> None:
        rel = unquote(rel).lstrip("/")
        if not rel or "\x00" in rel or ".." in rel.split("/"):
            self._send_error_json(404, "Tidak ditemukan")
            return
        base = STATIC_DIR
        target = (base / rel).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            self._send_error_json(404, "Tidak ditemukan")
            return
        if not target.is_file():
            self._send_error_json(404, "Tidak ditemukan")
            return
        ctype = _MIME_OVERRIDES.get(target.suffix.lower())
        if ctype is None:
            import mimetypes

            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        try:
            payload = target.read_bytes()
        except OSError:
            self._send_error_json(500, "Gagal membaca berkas")
            return
        self._send_bytes(200, ctype, payload)

    # -- POST ----------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        path = urlparse(self.path).path
        if path not in ("/api/inspect", "/api/clean"):
            self._send_error_json(404, "Tidak ditemukan")
            return
        try:
            body = self._read_json_body()
            if path == "/api/inspect":
                self._handle_inspect(body)
            else:
                self._handle_clean(body)
        except ApiError as exc:
            self._send_error_json(exc.status, exc.message, exc.extra)
        except subprocess.TimeoutExpired:
            self._send_error_json(500, "Waktu proses habis")
        except Exception:  # pragma: no cover - defensive
            traceback.print_exc()
            self._send_error_json(500, "Terjadi kesalahan internal")

    def _read_json_body(self) -> dict:
        length = self.headers.get("Content-Length")
        if not length:
            raw = b""
        else:
            try:
                n = int(length)
            except (TypeError, ValueError):
                raise ApiError(400, "Content-Length tidak valid")
            if n > MAX_BODY_BYTES:
                raise ApiError(413, "Ukuran masukan melebihi 25 MiB")
            raw = self.rfile.read(n) if n > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "JSON tidak valid")
        if not isinstance(body, dict):
            raise ApiError(400, "JSON tidak valid")
        return body

    # -- endpoint logic ------------------------------------------------------

    def _handle_inspect(self, body: dict) -> None:
        mode, name, data, is_text, _text, options = parse_input(body)
        tmp_dir = tempfile.mkdtemp(prefix="wmr-")
        try:
            in_path = Path(tmp_dir) / name
            in_path.write_bytes(data)
            try:
                proc = run_cli(
                    INSPECT_SCRIPT, [str(in_path), "--json", *build_inspect_flags(options)]
                )
            except subprocess.TimeoutExpired:
                raise ApiError(500, "Waktu proses habis")
            if proc.returncode not in (0, 1):
                raise ApiError(500, "Gagal memeriksa berkas")
            report = sanitize_report(parse_json_stdout(proc.stdout)) or {}
            kind = report.get("kind") or "unknown"
            self._send_json(
                200,
                {
                    "ok": True,
                    "kind": kind,
                    "name": name,
                    "suspicious": derive_suspicious(kind, report),
                    "summary": inspect_summary(kind, report),
                    "report": report,
                },
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _handle_clean(self, body: dict) -> None:
        mode, name, data, is_text, _text, options = parse_input(body)
        inspect_flags = build_inspect_flags(options)
        clean_flags = build_clean_flags(options)
        out_name = cleaned_name(name)
        download_name = out_name

        tmp_dir = tempfile.mkdtemp(prefix="wmr-")
        try:
            in_path = Path(tmp_dir) / name
            out_path = Path(tmp_dir) / out_name
            in_path.write_bytes(data)

            # 1) before-report (fail-soft -> null)
            report_before = None
            try:
                iproc = run_cli(INSPECT_SCRIPT, [str(in_path), "--json", *inspect_flags])
                if iproc.returncode in (0, 1):
                    report_before = sanitize_report(parse_json_stdout(iproc.stdout))
            except Exception:
                report_before = None

            # 2) clean
            try:
                cproc = run_cli(
                    CLEAN_SCRIPT, [str(in_path), "-o", str(out_path), "--json", *clean_flags]
                )
            except subprocess.TimeoutExpired:
                raise ApiError(500, "Waktu proses habis")

            # 3) unrecognized format -> 422
            if cproc.returncode == 2:
                raise ApiError(
                    422,
                    "Format berkas tidak dikenali",
                    extra={"report_before": report_before},
                )
            if cproc.returncode not in (0, 1):
                raise ApiError(500, "Gagal membersihkan berkas")

            report = sanitize_report(parse_json_stdout(cproc.stdout)) or {}

            # 4) read cleaned output (fall back to original bytes if absent)
            if out_path.is_file():
                out_bytes = out_path.read_bytes()
            else:
                out_bytes = data

            kind = report.get("kind") or (
                report_before.get("kind") if isinstance(report_before, dict) else None
            ) or "unknown"
            changed = bool(report.get("changed", True))

            if mode == "text":
                cleaned = out_bytes.decode("utf-8", "replace")
                summary = text_clean_summary(report)
                resp = {
                    "ok": True,
                    "kind": kind,
                    "name": name,
                    "download_name": download_name,
                    "mime": guess_mime(download_name, "text/plain"),
                    "changed": changed,
                    "cleaned": cleaned,
                    "cleaned_base64": None,
                    "content_length": len(out_bytes),
                    "summary": summary,
                    "report_before": report_before,
                    "report": report,
                }
            else:
                summary = "Metadata dibersihkan" if changed else "Tidak ada yang perlu diubah"
                resp = {
                    "ok": True,
                    "kind": kind,
                    "name": name,
                    "download_name": download_name,
                    "mime": guess_mime(download_name),
                    "changed": changed,
                    "cleaned": None,
                    "cleaned_base64": base64.b64encode(out_bytes).decode("ascii"),
                    "content_length": len(out_bytes),
                    "summary": summary,
                    "report_before": report_before,
                    "report": report,
                }
            self._send_json(200, resp)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def parse_bind(argv: list[str]) -> tuple[str, int]:
    host = os.environ.get("HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("PORT", "8770"))
    except ValueError:
        port = 8770
    args = argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif arg == "--port" and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg.startswith("--host="):
            host = arg.split("=", 1)[1]
            i += 1
        elif arg.startswith("--port="):
            try:
                port = int(arg.split("=", 1)[1])
            except ValueError:
                pass
            i += 1
        else:
            i += 1
    return host, port


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    host, port = parse_bind(argv)
    httpd = Server((host, port), Handler)
    print(
        f"{SERVICE_NAME} {VERSION} on http://{host}:{port}  (repo={REPO_DIR}, python={platform.python_version()})",
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
