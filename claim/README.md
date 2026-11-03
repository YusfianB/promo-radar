# Bot klaim promo (lokal, untuk belajar)

Bot ini membaca daftar promo dari Promo Radar, membuka halaman promo di browser **di komputer Anda** memakai akun Anda sendiri, lalu mengklik tombol klaim sesuai "resep" per situs.

Bot ini sengaja **tidak** berjalan di GitHub, karena klaim butuh sesi login Anda dan menyimpan sesi itu di cloud berisiko.

## Batas pengaman (jangan dilepas)
- Hanya mengunjungi domain yang ada di folder `recipes/`. Link promo dari hasil pencarian AI tidak dipercaya begitu saja.
- Berhenti dan memberi tahu Anda bila menemukan CAPTCHA, kolom kartu, OTP, atau verifikasi identitas/pelajar. Bot tidak menyelesaikannya.
- Tidak mengisi password atau data pembayaran, tidak membuat akun, dan satu promo hanya dicoba sekali.
- Default-nya mode uji: hanya melihat halaman, tidak mengklik.

Anda yang bertanggung jawab atas aturan setiap layanan. Sebagian layanan melarang otomatisasi dan bisa memblokir akun. Pakai hanya pada akun Anda sendiri.

## Pasang (Windows)
```bash
pip install playwright requests
playwright install chromium
```

## Pakai
1. **Buat resep** untuk layanan yang ingin diklaim: salin `recipes/_template.json` menjadi `recipes/nama-layanan.json`, isi `domain`, teks tombol, dan tanda berhasil. Ini bagian belajarnya: tiap situs punya tombol dan tahapan berbeda, jadi tiap situs butuh resep sendiri.
2. **Login sekali** secara manual (termasuk 2FA):
   ```bash
   python claim/claim_bot.py login nama-layanan
   ```
3. **Uji dulu** (tidak mengklik apa pun):
   ```bash
   python claim/claim_bot.py klaim
   ```
4. **Klaim sungguhan** setelah hasil uji bagus:
   ```bash
   python claim/claim_bot.py klaim --jalan --maks 3
   ```

Hasil dicatat di `~/.promo-radar/klaim_log.json` (hapus entri promo bila ingin mencoba lagi), screenshot di `~/.promo-radar/screenshot/`. Bila `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID` diisi di environment, ringkasan dikirim ke Telegram.

Status hasil: `berhasil`, `dilewati` (butuh Anda), `tidak_pasti` (cek screenshot), `gagal` (resep perlu diperbaiki).

## Otomatis tiap hari (opsional)
Di Windows: Task Scheduler, buat tugas harian yang menjalankan `python claim/claim_bot.py klaim --jalan --headless --maks 3`. Komputer harus menyala.
