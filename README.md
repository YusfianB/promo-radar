# Promo Radar

Setiap pagi (06:00 WIB) alat ini mencari promo terbaru di web lewat Meta Model API (fitur `web_search` bawaan), menyaring promo yang sudah kedaluwarsa, memperbarui dashboard, lalu mengirim ringkasan ke Telegram. Semua berjalan gratis di GitHub Actions, komputer Anda tidak perlu menyala.

## Penting sebelum mulai: amankan API key

API key Anda sempat ditulis di chat. Anggap key itu sudah terbuka:

1. Buat key baru di dashboard Meta Model API, lalu hapus (revoke) key lama.
2. Simpan key baru **hanya** di GitHub Secrets (langkah 3). Jangan tulis di file, kode, atau chat.

## Pasang dalam 6 langkah (sekitar 15 menit)

### 1. Buat repository GitHub
- Buka github.com, klik **New repository**, beri nama misalnya `promo-radar`.
- Pilih **Public**. Pilihan ini perlu karena GitHub Pages gratis hanya untuk repo publik. Aman, sebab API key disimpan di Secrets dan tidak ikut terlihat. Yang publik hanya daftar promo.
- Upload seluruh isi folder ini (termasuk folder `.github`, `templates`, `tests`) ke repo tersebut.

### 2. Buat bot Telegram
- Di Telegram, cari **@BotFather**, kirim `/newbot`, ikuti petunjuknya. Anda akan mendapat **token** (bentuknya `123456:ABC...`).
- Buka chat dengan bot baru Anda, kirim pesan apa saja (misalnya "halo").
- Buka `https://api.telegram.org/bot<TOKEN>/getUpdates` di browser (ganti `<TOKEN>` dengan token Anda). Cari `"chat":{"id":123456789` dan catat angkanya. Itu **chat id** Anda.

### 3. Isi Secrets
Di repo: **Settings → Secrets and variables → Actions → New repository secret**. Buat tiga secret:

| Nama | Isi |
|---|---|
| `MODEL_API_KEY` | API key Meta Model API (yang baru) |
| `TELEGRAM_BOT_TOKEN` | token dari BotFather |
| `TELEGRAM_CHAT_ID` | chat id dari langkah 2 |

### 4. Aktifkan dashboard (GitHub Pages)
**Settings → Pages → Build and deployment**: Source = *Deploy from a branch*, Branch = `main`, folder = `/docs`, lalu Save. Setelah beberapa menit, alamat dashboard muncul di halaman yang sama (bentuknya `https://NAMA-ANDA.github.io/promo-radar/`).

### 5. (Opsional) Sertakan link dashboard di pesan Telegram
**Settings → Secrets and variables → Actions → tab Variables → New repository variable**: nama `DASHBOARD_URL`, isi alamat dashboard dari langkah 4.

### 6. Jalankan pertama kali
Buka tab **Actions → Promo Radar → Run workflow**. Untuk uji murah, isi kolom *kategori* dengan `Travel` agar hanya satu kategori yang dicari. Jika sukses, pesan masuk ke Telegram dan dashboard terisi. Setelah itu jalan otomatis tiap hari.

Jika langkah ini gagal, buka run yang gagal di tab Actions dan baca barisnya. Pesan seperti `API key ditolak` atau `HTTP 404` menunjukkan masalah di key atau nama model.

## Biaya

Meta Model API bayar per pemakaian: US$1,25 per juta token masuk, US$4,25 per juta token keluar, ditambah sekitar US$0,008 per pencarian web. Dengan 14 kategori, perkiraan kasar **US$0,5 sampai 1 per hari** (sekitar US$15 sampai 30 per bulan). Ini hanya perkiraan: biaya sebenarnya dicetak di akhir setiap run (baris `perkiraan biaya`) sehingga Anda bisa memeriksanya setelah beberapa hari.

Cara menekan biaya, semuanya di `config.json`:
- Ubah `"aktif": true` menjadi `false` untuk kategori yang tidak Anda butuhkan.
- Kurangi `max_promo_per_kategori` (default 12).
- Jalankan tiap 2 hari: ubah `cron` di `.github/workflows/promo.yml` menjadi `"0 23 */2 * *"`.

## Mengubah isi pencarian

Buka `config.json`:
- **Menambah kategori**: salin salah satu blok, ubah `nama`, `fokus` (merek atau jenis promo yang dicari), dan `wilayah`.
- **Ganti model**: ubah `model` (misalnya `muse-spark-1.3`). `model_cadangan` dipakai otomatis bila model utama ditolak.
- Tingkat penalaran `reasoning_effort`: `low` paling hemat, `medium` atau `high` lebih teliti tetapi lebih mahal dan lambat.

## Batasan yang perlu diketahui

- Alat ini hanya membaca halaman web publik. Voucher yang hanya muncul di dalam aplikasi (Shopee, GoFood, Grab) atau perlu login tidak akan terdeteksi.
- Hasil berasal dari model AI yang membaca web. Selalu buka link sumber dan cek syarat serta tanggal sebelum berbelanja. Promo bisa saja salah baca atau sudah berubah.
- Jadwal GitHub bisa terlambat beberapa menit. Jika repo tidak aktif lama, GitHub dapat menonaktifkan jadwal otomatis; tinggal klik **Enable** di tab Actions.
- Meta Model API masih tahap pratinjau publik dan dapat berubah.

## Uji lokal (opsional)

```bash
pip install requests
python -m unittest discover -s tests -v          # uji dengan data tiruan, tanpa API key
export MODEL_API_KEY=...                          # key Anda
python promo_radar.py --kategori Travel --tanpa-telegram
```
