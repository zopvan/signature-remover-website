# Watermarks Remover — Web UI (Manual)

Antarmuka web lokal untuk membersihkan **karakter watermark AI yang tak terlihat**
(Layer A: Unicode invisible, spasi eksotis, bidi, tag chars) dari teks atau file
yang Anda miliki. Ini alur **manual**: tempel teks / unggah file, lalu dapatkan
hasil bersihnya.

> Ini hanya Layer A (deterministik). Watermark statistik (token-sampling, Layer B)
> tidak dihapus di sini.

## Prasyarat

- Python 3.10+ (mesin ini 3.13). Tanpa dependensi pihak ketiga.
- Repo upstream sudah ada di `../watermarks-remover` (sudah di-clone).
- (Opsional, untuk file PDF/gambar tertentu) `exiftool`, `qpdf`, `c2patool` bila
  ingin hasil maksimal; tanpa itu file tetap diproses sebisanya.

## Menjalankan

```bash
cd /home/zet/Projects/punyafajar/app
./start.sh
# atau:
python3 server.py
# ganti host/port:
python3 server.py --host 127.0.0.1 --port 8770
```

Lalu buka **http://127.0.0.1:8770** di browser.

- `HOST` / `PORT` bisa lewat env.
- `WATERMARKS_REPO` menunjuk lokasi repo upstream (default `../watermarks-remover`).

## Cara pakai

### Tab "Teks"
1. Tempel teks dari web chat (Ctrl/Cmd+V) ke kotak besar.
2. (Opsional) atur opsi di bawah.
3. Klik **Bersihkan**.
4. Lihat hasil: teks bersih, ringkasan Sebelum/Sesudah, dan tanda yang ditemukan.
   Gunakan **Salin** atau **Unduh .txt**.

### Tab "File"
1. Seret file ke dropzone atau klik untuk memilih (png, jpg, pdf, docx, xlsx,
   pptx, epub, odt, html, md, mp4, wav, mp3, dll).
2. Klik **Bersihkan**.
3. Lihat laporan (jenis file, berubah/tidak, apa yang ditemukan).
4. Klik **Unduh hasil** untuk file yang sudah dibersihkan (mis. `foto.cleaned.png`).

## Opsi

| Opsi | Default | Arti |
|---|---|---|
| NFKC | nonaktif | Normalisasi Unicode NFKC setelah pembersihan. |
| Normalisasi spasi | aktif | Ubah spasi eksotis (mis. non-breaking space) ke spasi biasa. Matikan untuk bahasa seperti Prancis yang butuh spasi khusus. |
| Setel juga perekat emoji | nonaktif | Agresif: juga buang invisible "load-bearing" (perekat emoji, joiner, tag). |
| Buang penanda arah (bidi) | nonaktif | Juga buang tanda arah RTL/LTR yang sah. |
| Homoglyph agresif | nonaktif | Petakan huruf Kiril/fullwidth Latin yang menyerupai Latin ke ASCII. |

## Verifikasi

```bash
cd /home/zet/Projects/punyafajar
./verify.sh
```

## HTTP API (ringkas)

- `GET  /api/health`
- `POST /api/inspect` — `{ "text": "..." }` atau `{ "filename": "...", "file_base64": "..." }`
- `POST /api/clean`   — bentuk sama, mengembalikan `cleaned` (teks) atau `cleaned_base64` (file)

Detail lengkap: `../API_CONTRACT.md`.

## Batasan

- Hanya membersihkan teks/berkas di aplikasi ini; tidak menyentuh transcript chat.
- Teks yang hanya ada di jendela chat asli tidak bisa dibersihkan otomatis —
  itulah mengapa alurnya manual (salin → bersihkan → pakai).
- Format tak dikenal ditolak (HTTP 422), bukan ditebak.
