# eyai_signature — Pembersih Tanda Watermark / AI

Aplikasi web lokal untuk membersihkan **karakter watermark AI** dari teks atau
file yang kamu miliki:

- **Layer A (deterministik)** — menghapus karakter tak terlihat: zero-width,
  spasi eksotis, penanda arah (bidi), tag chars, homoglyph (opsional).
- **Tanda khas AI (app-side)** — menghapus tanda tipografis yang terlihat dan
  khas tulisan AI: tanda pisah panjang (`—`, `–`), mengubah kutip keriting
  (`" " ' '`) jadi lurus, serta membuang emoji & emoticon.

Alurnya **manual**: tempel teks / unggah file → bersihkan → salin / unduh hasil.
Tidak menyentuh transkrip chat asli, dan tidak menghapus watermark statistik
(token-sampling / Layer B).

---

## Prasyarat

- **Python 3.10+** (dites di 3.13/3.14). Tanpa dependensi pihak ketiga.
- Repo upstream **watermarks-remover** tersedia di `./watermarks-remover`
  (sudah disertakan). Bisa diarahkan lain lewat env `WATERMARKS_REPO`.
- (Opsional, untuk hasil file tertentu lebih maksimal) `exiftool`, `qpdf`,
  `c2patool` di PATH.

---

## Struktur

```
eyai_signature/
├── API_CONTRACT.md        # kontrak request/response (frozen)
├── verify.sh              # uji end-to-end (20 pemeriksaan)
├── README.md              # file ini
├── app/
│   ├── server.py          # backend stdlib + UI statis
│   ├── start.sh           # launcher praktis
│   ├── README.md          # catatan detail app
│   └── static/            # index.html, styles.css, app.js
└── watermarks-remover/    # repo upstream (jangan diubah)
```

---

## Menjalankan

```bash
cd app
./start.sh
# atau:
python3 server.py

# ganti host/port:
python3 server.py --host 127.0.0.1 --port 8770
```

Lalu buka **http://127.0.0.1:8770** di browser.

- Host/port bisa lewat env: `HOST`, `PORT`.
- Lokasi repo upstream: env `WATERMARKS_REPO` (default `../watermarks-remover`).

---

## Cara pakai

### Tab "Teks"
1. Tempel teks dari web chat (Ctrl/Cmd+V) ke kotak besar.
2. (Opsional) atur opsi di panel **Opsi pembersihan**.
3. Klik **Bersihkan**.
4. Lihat hasil: teks bersih, ringkasan Sebelum/Sesudah, dan tanda yang ditemukan.
   Gunakan **Salin** atau **Unduh .txt**.

### Tab "File"
1. Seret file ke dropzone atau klik untuk memilih (png, jpg, pdf, docx, xlsx,
   pptx, epub, odt, html, md, mp4, wav, mp3, dll).
2. Klik **Bersihkan**.
3. Lihat laporan (jenis file, berubah/tidak, apa yang ditemukan).
4. Klik **Unduh hasil** untuk file yang sudah dibersihkan (mis. `foto.cleaned.png`).

Tombol **Periksa penanda** menjalankan pemeriksaan saja (tanpa mengubah apa pun).

---

## Opsi pembersihan

Berlaku untuk kedua tab.

| Opsi | Default | Arti |
|---|---|---|
| **Buang tanda khas AI** | aktif | Hapus tanda pisah panjang (`—`, `–`, `―`, `−`), ubah kutip keriting (`" " ' '`) jadi lurus, buang emoji & emoticon. Strip keyboard `-` **tidak** disentuh. |
| Rapikan spasi samar | aktif | Ganti spasi Unicode tak lazim (mis. non-breaking space) ke spasi biasa. |
| Normalisasi NFKC | nonaktif | Normalisasi Unicode NFKC setelah pembersihan. |
| Buang perekat emoji | nonaktif | Agresif: juga buang invisible "load-bearing" (perekat emoji, joiner, tag). |
| Buang penanda arah teks | nonaktif | Juga buang tanda arah RTL/LTR yang sah. |
| Homoglyph agresif | nonaktif | Petakan huruf Kiril/fullwidth Latin yang menyerupai Latin ke ASCII. |

Perilaku **"Buang tanda khas AI"**:
- Dash dihapus tanpa pengganti. Antar kata dijaga **satu spasi** supaya tidak
  menempel: `well—known` → `well known`, `A — B` → `A B`.
- Kutip keriting → kutip lurus: `"Halo"` → `"Halo"`, `it's` → `it's`.
- Emoji (termasuk rangkaian ZWJ/skin-tone) dan emoticon ASCII (`:)`, `:D`, `;-)`,
  `<3`, `xD`, `^_^`, …) dibuang; spasi ganda sisa dirapikan.
- Berlaku untuk **teks paste** dan **berkas teks** (.txt/.md). Berkas biner
  (docx/pdf/gambar/av) tidak diubah isinya.
- Bisa dimatikan: `"strip_ai_tells": false` (via UI atau API).

---

## Verifikasi

```bash
cd /home/zet/Projects/eyai_signature
./verify.sh
```

Menjalankan server sementara di port `8770` (env `PORT`) dan mengecek 20 hal:
health, UI + asset, inspect/clean teks, hapus U+200B/U+00AD, clean file PNG,
format tak dikenal → HTTP 422, pembersihan tanda AI (dash/kutip/emoji), opsi
opt-out, hyphen keyboard aman, dan `node --check app.js`.

---

## HTTP API (ringkas)

- `GET  /api/health`
- `POST /api/inspect` — `{ "text": "..." }` atau `{ "filename": "...", "file_base64": "..." }`
- `POST /api/clean`   — bentuk sama, mengembalikan `cleaned` (teks) atau
  `cleaned_base64` (file)

Opsi dikirim sebagai objek `options`. Detail lengkap + bentuk respons:
[`API_CONTRACT.md`](./API_CONTRACT.md).

---

## Batasan

- Hanya membersihkan teks/berkas di aplikasi ini; tidak menyentuh transkrip chat asli.
- Teks yang hanya ada di jendela chat asli tidak bisa dibersihkan otomatis —
  itulah mengapa alurnya manual (salin → bersihkan → pakai).
- Watermark **statistik** (token-sampling / Layer B) tidak dihapus di sini.
- Format tak dikenal ditolak (HTTP 422), bukan ditebak.
- Batas masukan: 25 MiB per permintaan; subprocess timeout 60 detik.

---

## Troubleshooting

- **Port sudah dipakai** (`Address already in use`): jalankan dengan port lain,
  mis. `python3 app/server.py --port 8771`, atau hentikan proses lama:
  `pkill -f app/server.py`.
- **Ganti lokasi repo upstream**: `WATERMARKS_REPO=/path/ke/watermarks-remover python3 server.py`.
- **Berkas tidak berubah**: mungkin memang tidak ada penanda; coba aktifkan opsi
  agresif (NFKC / perekat emoji / bidi / homoglyph).
