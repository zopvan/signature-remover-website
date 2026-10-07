# API Contract — Watermarks Remover Web UI (Manual)

FROZEN CONTRACT. Backend and frontend are built against this. Do not change
response shapes without updating both sides.

## Layout

```
/home/zet/Projects/punyafajar/
  API_CONTRACT.md              <- this file
  watermarks-remover/          <- cloned upstream repo (do NOT modify)
  app/
    server.py                  <- backend (stdlib only, Python 3.10+)
    start.sh                   <- convenience launcher
    README.md                  <- usage (written last by orchestrator)
    static/
      index.html
      styles.css
      app.js
```

- Backend default bind: `http://127.0.0.1:8770` (env `HOST`, `PORT`).
- Upstream repo path: env `WATERMARKS_REPO`, default `<app>/../watermarks-remover`.
- Python is 3.13 here: **`cgi` module does not exist**. Do not use it.
- No third-party dependencies. stdlib only. No build step. No CDN (offline use).
- Frontend is served by the same server (same origin), so no CORS needed.

## What "manual" means

User pastes text copied from a web chat, OR uploads a file. The server runs the
upstream **Layer A** pipeline (`clean_file.py`) and reports + returns the result.
No LLM / Layer B is involved.

Backend shells out to (with `sys.executable`):

- `service/scripts/inspect_file.py <path> --json`
- `service/scripts/clean_file.py <path> -o <out> --json`

Repo relative to `WATERMARKS_REPO`.

## Transport rules

- Input and output are always `Content-Type: application/json; charset=utf-8`.
- Files are sent as **base64 in JSON** (no multipart — keeps Python 3.13 safe):

```jsonc
// text input
{ "text": "Hello\u200bWorld", "options": { ... } }

// file input
{ "filename": "photo.png", "file_base64": "iVBORw0...", "options": { ... } }
```

- `options` (all optional, booleans with these defaults):

```json
{
  "nfkc": false,
  "normalize_spaces": true,
  "strip_emoji_glue": false,
  "strip_bidi": false,
  "aggressive_homoglyphs": false
}
```

Option -> CLI flag mapping:

| option (true) | inspect_file flag | clean_file flag |
|---|---|---|
| nfkc | (none) | `--nfkc` |
| normalize_spaces = false | (none) | `--no-normalize-spaces` |
| strip_emoji_glue | (none) | `--strip-emoji-glue` |
| strip_bidi | (none) | `--strip-bidi` |
| aggressive_homoglyphs | `--aggressive` | `--aggressive-homoglyphs` |

- Limits: raw input max **25 MiB** (for base64, decode first then check). Subprocess
  timeout 60s. Temp work dir via `tempfile.mkdtemp()`, always cleaned up.
- Never return absolute host paths or subprocess stderr verbatim (return a short,
  safe message).

## Endpoints

### `GET /` -> serve `static/index.html`
### `GET /assets/<file>` -> serve `static/<file>` (css/js; correct Content-Type)
### `GET /api/health` ->
```json
{ "ok": true, "service": "watermarks-web", "version": "1.0.0",
  "repo": true, "python": "3.13.13" }
```

### `POST /api/inspect`
Body = text input or file input. Runs `inspect_file.py --json` (+ `--aggressive`
when `aggressive_homoglyphs`).
```json
{
  "ok": true,
  "kind": "text",
  "name": "pasted.txt",
  "suspicious": true,
  "summary": "3 tanda ditemukan",
  "report": { }
}
```
- `kind`: `text` | `image` | `container` | `av` | `unknown`.
- `suspicious`: bool (default false). For text derive from `suspicious_total > 0`
  of the raw report.
- `summary`: short human string (Indonesian) safe to display.
- `report`: the raw `inspect_file.py --json` object, passed through (may be `{}`).

### `POST /api/clean`
Body = text input or file input. Steps:
1. run `inspect_file.py --json` first (before-report; fail-soft -> `report_before: null`).
2. run `clean_file.py <in> -o <out> --json` with flags from options.
3. read the cleaned output file.

Response for **text input**:
```json
{
  "ok": true,
  "kind": "text",
  "name": "pasted.txt",
  "download_name": "draft.cleaned.txt",
  "mime": "text/plain",
  "changed": true,
  "cleaned": "HelloWorld",
  "cleaned_base64": null,
  "content_length": 10,
  "summary": "2 karakter dihapus, 1 spasi diganti",
  "report_before": { },
  "report": { }
}
```
Response for **file input**:
```json
{
  "ok": true,
  "kind": "container",
  "name": "notes.docx",
  "download_name": "notes.cleaned.docx",
  "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "changed": true,
  "cleaned": null,
  "cleaned_base64": "UEsDBBQ...",
  "content_length": 12345,
  "summary": "Metadata dibersihkan",
  "report_before": { },
  "report": { }
}
```
- `changed`: from the raw clean report `changed` (default true if unknown).
- `summary`: Indonesian, derived from report if possible:
  - text: `"{removed_count} karakter dihapus, {replaced_count} spasi diganti"`;
    if both 0 -> `"Tidak ada tanda yang ditemukan"`.
  - file: `"Metadata dibersihkan"` when `changed` else `"Tidak ada yang perlu diubah"`.
- `mime`: guess from `download_name` (`mimetypes.guess_type`), fallback
  `application/octet-stream`.

### Errors (all endpoints)
```json
{ "ok": false, "error": "pesan singkat aman", "kind": "unknown" }
```
- HTTP 400 bad JSON / missing `text`+`filename` / invalid base64.
- HTTP 413 input too large.
- HTTP 422 unrecognized format for `/clean` (upstream `clean_file.py` exit 2) —
  still include `report_before` if inspect succeeded and `"kind":"unknown"`.
- HTTP 500 unexpected.

## Frontend requirements (designer)

- Vanilla HTML/CSS/JS only; no frameworks, no CDN, no build.
- Indonesian UI copy, concise and grounded (no marketing fluff).
- Two modes: **Teks** (paste) and **File** (upload/drag-drop).
- Teks tab: large textarea, options checkboxes, "Bersihkan" button; result panel
  with cleaned text, "Salin", "Unduh .txt", detected-marks summary, "Sebelum /
  Sesudah" stats, collapsible raw report.
- File tab: dropzone + file picker, options, "Bersihkan"; result shows kind,
  changed/unchanged, what was found before, download button for the cleaned file
  (decode base64 in JS), collapsible raw report.
- Handle all error shapes from the contract with a visible inline error state.
- Show a loading/busy state during requests.
- Responsive (mobile -> desktop), accessible (labels, focus states, keyboard for
  dropzone), dark-friendly modern look.
- `app.js` calls relative URLs: `/api/health`, `/api/inspect`, `/api/clean`.
- For text input send `{text, options}`; for file input send `{filename, file_base64, options}`.

## Verification (orchestrator, after both lanes land)

1. `python3 app/server.py` starts, `GET /api/health` -> ok.
2. `GET /` returns index.html; assets load.
3. `POST /api/inspect` text with U+200B -> `suspicious: true`, hits present.
4. `POST /api/clean` text -> `cleaned` has no U+200B; stats counts correct.
5. `POST /api/clean` file (PNG) -> `cleaned_base64` decodes; `changed` reported.
6. `POST /api/clean` unknown bytes -> HTTP 422 with `{ok:false}` (no crash).
7. `node --check app/static/app.js`.
