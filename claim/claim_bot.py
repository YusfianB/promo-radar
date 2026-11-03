#!/usr/bin/env python3
"""Bot klaim promo (untuk belajar). Berjalan LOKAL di komputer Anda, memakai akun Anda sendiri.

Cara kerja: baca daftar promo dari Promo Radar, cocokkan dengan "resep" per situs (folder recipes/),
buka halaman promo di browser dengan profil login Anda, klik tombol klaim sesuai resep, catat hasilnya.

Batas pengaman yang sengaja dipasang (jangan dilepas):
  - Hanya mengunjungi domain yang ada di resep. URL promo dari hasil pencarian AI tidak dipercaya begitu saja.
  - Berhenti dan minta Anda ambil alih bila ada CAPTCHA, kolom kartu/pembayaran, OTP, atau verifikasi identitas.
    Bot ini TIDAK menyelesaikan CAPTCHA dan tidak mengisi data pembayaran atau password.
  - Satu promo hanya dicoba satu kali (dicatat di klaim_log.json). Satu akun saja, tanpa akun ganda.
  - Default-nya mode uji (--dry-run): hanya melihat halaman dan melapor, tidak mengklik. Pakai --jalan untuk benar-benar klaim.
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get("PROMO_RADAR_HOME", Path.home() / ".promo-radar"))
SUMBER_DEFAULT = os.environ.get(
    "PROMOS_URL", "https://raw.githubusercontent.com/YusfianB/promo-radar/main/data/promos.json"
)

SELEKTOR_PENGHALANG = {
    'iframe[src*="recaptcha"]': "CAPTCHA (reCAPTCHA)",
    'iframe[src*="hcaptcha"]': "CAPTCHA (hCaptcha)",
    'iframe[src*="challenges.cloudflare.com"]': "CAPTCHA (Cloudflare)",
    'input[autocomplete="cc-number"]': "kolom kartu pembayaran",
    'input[name*="card" i]': "kolom kartu pembayaran",
    'input[autocomplete="one-time-code"]': "kode OTP",
}
POLA_TEKS_PENGHALANG = re.compile(
    r"(verify you are human|verifikasi (pelajar|mahasiswa|identitas)|student verification|"
    r"verify (your )?(student|identity)|masukkan kode (otp|verifikasi)|enter the (verification )?code|"
    r"upload (your )?(id|student)|unggah (kartu|ktm|ktp))",
    re.I,
)


# ---------------------------------------------------------------- util

def sekarang():
    return datetime.now().isoformat(timespec="seconds")


def muat_resep(folder):
    resep = []
    for f in sorted(Path(folder).glob("*.json")):
        if f.name.startswith("_"):
            continue
        r = json.loads(f.read_text(encoding="utf-8"))
        r["_file"] = f.name
        resep.append(r)
    return resep


def muat_promos(sumber):
    if re.match(r"^https?://", sumber):
        r = requests.get(sumber, timeout=30)
        r.raise_for_status()
        data = r.json()
    else:
        data = json.loads(Path(sumber).read_text(encoding="utf-8"))
    promos = data.get("promos", data)
    return list(promos.values()) if isinstance(promos, dict) else list(promos)


def host_aman(url):
    """Hanya https, kecuali localhost (untuk uji)."""
    u = urlparse(url or "")
    if u.scheme == "https" and u.hostname:
        return u.hostname.lower()
    if u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"):
        return u.hostname
    return None


def cari_resep(url, semua):
    host = host_aman(url)
    if not host:
        return None
    for r in semua:
        for d in r["domain"] if isinstance(r["domain"], list) else [r["domain"]]:
            d = d.lower()
            if host == d or host.endswith("." + d):
                return r
    return None


def muat_log(path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def simpan_log(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def kirim_telegram(teks):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": teks[:3900], "disable_web_page_preview": True},
            timeout=30,
        )
    except requests.RequestException:
        pass


# ---------------------------------------------------------------- browser

def deteksi_penghalang(page):
    for sel, nama in SELEKTOR_PENGHALANG.items():
        try:
            if page.locator(sel).count():
                return nama
        except Exception:
            pass
    try:
        teks = page.inner_text("body", timeout=3000)
    except Exception:
        teks = ""
    m = POLA_TEKS_PENGHALANG.search(teks)
    return f"verifikasi manual ('{m.group(0)}')" if m else None


def cari_tombol(page, teks):
    pola = re.compile(teks, re.I)
    for loc in (page.get_by_role("button", name=pola), page.get_by_role("link", name=pola)):
        try:
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:
            pass
    return None


def proses_promo(page, promo, resep, jalan):
    """Return (status, catatan). status: berhasil | dilewati | tidak_pasti | gagal | uji_ok"""
    page.goto(promo["url"], wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)

    alasan = deteksi_penghalang(page)
    if alasan:
        return "dilewati", f"butuh Anda: {alasan}"

    langkah = resep.get("langkah", [])
    for i, st in enumerate(langkah, 1):
        aksi = st.get("aksi")
        if aksi == "klik":
            tombol = cari_tombol(page, st["teks"]) if st.get("teks") else None
            if tombol is None and st.get("selector"):
                loc = page.locator(st["selector"])
                tombol = loc.first if loc.count() else None
            if tombol is None:
                return "gagal", f"langkah {i}: tombol '{st.get('teks') or st.get('selector')}' tidak ditemukan"
            if not jalan:
                continue  # mode uji: cukup pastikan tombol ada
            tombol.click(timeout=10000)
            page.wait_for_timeout(1500)
            alasan = deteksi_penghalang(page)
            if alasan:
                return "dilewati", f"berhenti setelah langkah {i}, butuh Anda: {alasan}"
        elif aksi == "tunggu_teks":
            if not jalan:
                continue
            try:
                page.get_by_text(re.compile(st["teks"], re.I)).first.wait_for(timeout=int(st.get("detik", 10)) * 1000)
            except Exception:
                return "gagal", f"langkah {i}: teks '{st['teks']}' tidak muncul"
        elif aksi == "tunggu_detik":
            if jalan:
                page.wait_for_timeout(min(int(st.get("detik", 2)), 10) * 1000)
        else:
            return "gagal", f"langkah {i}: aksi '{aksi}' tidak dikenal"

    if not jalan:
        return "uji_ok", "halaman terbuka, tidak ada penghalang, semua tombol resep ditemukan (tidak diklik)"

    try:
        isi = page.inner_text("body", timeout=5000)
    except Exception:
        isi = ""
    for kata in resep.get("berhasil_jika_ada_teks", []):
        if re.search(kata, isi, re.I):
            return "berhasil", f"teks '{kata}' terlihat"
    return "tidak_pasti", "langkah selesai tapi tanda berhasil tidak terlihat. Cek screenshot."


# ---------------------------------------------------------------- perintah

def perintah_login(args, semua):
    r = next((x for x in semua if args.situs.lower() in x["nama"].lower() or args.situs.lower() in str(x["domain"]).lower()), None)
    if not r:
        raise SystemExit(f"Resep '{args.situs}' tidak ditemukan di {args.resep_dir}")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(HOME / "profil"), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(r.get("login_url") or f"https://{r['domain'] if isinstance(r['domain'], str) else r['domain'][0]}")
        input(f"Login ke {r['nama']} secara manual di jendela browser (termasuk 2FA). Setelah masuk, tekan Enter di sini ... ")
        ctx.close()
    print("Sesi tersimpan di profil lokal Anda.")


def perintah_klaim(args, semua):
    HOME.mkdir(parents=True, exist_ok=True)
    log_path = HOME / "klaim_log.json"
    log = muat_log(log_path)
    promos = muat_promos(args.sumber)

    kandidat, abaikan = [], 0
    for pr in promos:
        r = cari_resep(pr.get("url"), semua)
        if not r:
            abaikan += 1
            continue
        if pr["id"] in log:
            continue
        kandidat.append((pr, r))
    kandidat.sort(key=lambda x: -int(x[0].get("nilai", 3)))
    kandidat = kandidat[: args.maks]
    print(f"{len(promos)} promo dibaca, {abaikan} tanpa resep (tidak dikunjungi), {len(kandidat)} akan diproses. "
          f"Mode: {'KLAIM NYATA' if args.jalan else 'UJI (tidak mengklik)'}")
    if not kandidat:
        return

    (HOME / "screenshot").mkdir(parents=True, exist_ok=True)
    laporan = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(HOME / "profil"), headless=args.headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for n, (pr, r) in enumerate(kandidat):
            if n:
                time.sleep(random.uniform(*args.jeda))
            try:
                status, catatan = proses_promo(page, pr, r, args.jalan)
            except Exception as e:
                status, catatan = "gagal", f"{type(e).__name__}"
            shot = HOME / "screenshot" / f"{pr['id']}_{status}.png"
            try:
                page.screenshot(path=str(shot))
            except Exception:
                pass
            print(f"  [{status}] {pr['merchant']}: {pr['judul'][:60]} -> {catatan}")
            laporan.append(f"[{status}] {pr['merchant']}: {pr['judul'][:60]}\n   {catatan}\n   {pr.get('url','')}")
            if args.jalan:
                log[pr["id"]] = {"status": status, "catatan": catatan, "waktu": sekarang(), "url": pr["url"]}
                simpan_log(log_path, log)
        ctx.close()

    if args.jalan and laporan:
        kirim_telegram("Bot klaim promo\n\n" + "\n\n".join(laporan))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bot klaim promo (lokal, untuk belajar)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nama in ("login", "klaim"):
        s = sub.add_parser(nama)
        s.add_argument("--resep-dir", default=str(ROOT / "recipes"))
    sub.choices["login"].add_argument("situs", help="nama atau domain resep, contoh: github")
    k = sub.choices["klaim"]
    k.add_argument("--sumber", default=SUMBER_DEFAULT, help="URL atau path promos.json")
    k.add_argument("--jalan", action="store_true", help="klaim sungguhan. Tanpa ini hanya uji")
    k.add_argument("--headless", action="store_true", help="sembunyikan jendela browser")
    k.add_argument("--maks", type=int, default=5, help="maksimal promo per eksekusi")
    k.add_argument("--jeda", type=float, nargs=2, default=(5, 15), metavar=("MIN", "MAKS"))
    args = ap.parse_args(argv)
    semua = muat_resep(args.resep_dir)
    if not semua:
        raise SystemExit("Belum ada resep di folder recipes/.")
    (perintah_login if args.cmd == "login" else perintah_klaim)(args, semua)


if __name__ == "__main__":
    main()
