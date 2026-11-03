#!/usr/bin/env python3
"""Promo Radar: cari promo terbaru lewat Meta Model API (web_search bawaan),
simpan ke data/promos.json, bangun dashboard docs/index.html, kirim ringkasan ke Telegram.

Rahasia dibaca dari environment variable, tidak pernah ditulis ke file:
  MODEL_API_KEY       wajib   API key Meta Model API
  TELEGRAM_BOT_TOKEN  opsional
  TELEGRAM_CHAT_ID    opsional
  DASHBOARD_URL       opsional, link dashboard yang disertakan di pesan Telegram
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

WIB = timezone(timedelta(hours=7))
ROOT = Path(__file__).resolve().parent
BASE_URL = os.environ.get("META_API_BASE", "https://api.meta.ai/v1").rstrip("/")

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]

# Berapa hari promo yang tidak muncul lagi dipertahankan sebelum dibuang
BASI_DENGAN_TANGGAL = 10
BASI_TANPA_TANGGAL = 5


# ---------------------------------------------------------------- util

def hari_ini():
    return datetime.now(WIB).date()


def tgl_indo(d):
    return f"{d.day} {BULAN[d.month - 1]}"


def parse_tanggal(s):
    if not isinstance(s, str):
        return None
    m = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def bersih(s, maks):
    if s is None:
        return ""
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s[:maks]


def buat_id(promo):
    dasar = norm(promo["merchant"]) + "|" + norm(promo["judul"])[:80]
    return hashlib.sha1(dasar.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- API

def bersihkan_key(mentah):
    """Ambil API key murni dari isi secret, walau tertempel dengan JSON, tanda kutip, atau baris baru."""
    mentah = (mentah or "").strip()
    if not mentah:
        return ""
    if mentah.startswith("{"):
        try:
            obj = json.loads(mentah)
            if isinstance(obj, dict) and obj.get("apikey"):
                mentah = str(obj["apikey"])
        except json.JSONDecodeError:
            pass
    m = re.search(r"LLM\|\d+\|[A-Za-z0-9_\-]+", mentah)
    if m:
        return m.group(0)
    return re.sub(r"[\s\"']+", "", mentah)


def ambil_teks(data):
    teks = data.get("output_text")
    if isinstance(teks, str) and teks.strip():
        return teks
    bagian = []
    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            for c in item.get("content", []) or []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    bagian.append(c["text"])
    return "\n".join(bagian)


def panggil_model(cfg, api_key, prompt):
    """Satu panggilan Responses API dengan web_search. Return (teks, usage dict)."""
    model_list = [cfg["model"]] + list(cfg.get("model_cadangan", []))
    kesalahan = []
    for model in model_list:
        body = {
            "model": model,
            "input": prompt,
            "tools": [{"type": "web_search"}],
        }
        if cfg.get("reasoning_effort"):
            body["reasoning"] = {"effort": cfg["reasoning_effort"]}
        for percobaan in range(3):
            try:
                r = requests.post(
                    f"{BASE_URL}/responses",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=body,
                    timeout=300,
                )
            except requests.RequestException as e:
                kesalahan.append(f"{model}: jaringan {type(e).__name__}")
                time.sleep(3 * (percobaan + 1))
                continue
            if r.status_code == 200:
                data = r.json()
                return ambil_teks(data), data.get("usage", {}) or {}
            if r.status_code in (401, 403):
                raise SystemExit(f"API key ditolak (HTTP {r.status_code}). Periksa secret MODEL_API_KEY.")
            if r.status_code == 400 and "reasoning" in r.text.lower() and "reasoning" in body:
                body.pop("reasoning")
                continue
            if r.status_code in (404, 400):
                kesalahan.append(f"{model}: HTTP {r.status_code} {r.text[:200]}")
                break  # coba model cadangan
            kesalahan.append(f"{model}: HTTP {r.status_code}")
            time.sleep(5 * (percobaan + 1))
    raise RuntimeError(" | ".join(dict.fromkeys(kesalahan)) or "gagal memanggil model")


def buat_prompt(kat, tanggal, maks):
    global_ = kat["wilayah"].lower().startswith("global")
    lokasi = "di seluruh dunia yang bisa dipakai pengguna dari Indonesia" if global_ else "di Indonesia"
    catatan = ("\n- " + kat["catatan"]) if kat.get("catatan") else ""
    return f"""Hari ini {HARI[tanggal.weekday()]}, {tanggal.isoformat()} (zona waktu WIB, Indonesia).

Tugas: cari di web promo, diskon, cashback, atau voucher yang SEDANG BERLAKU hari ini {lokasi}.
Kategori: {kat['nama']}
Cakupan: {kat['fokus']}
Wilayah: {kat['wilayah']}

Aturan:
- Gunakan pencarian web. Jangan mengarang. Hanya masukkan promo yang kamu temukan di halaman sumber.
- Hanya promo yang masih berlaku per {tanggal.isoformat()}. Buang yang sudah lewat atau belum mulai.
- Utamakan sumber resmi (situs/akun brand, bank, e-wallet) lalu media promo tepercaya.
- Pilih {maks} promo bernilai paling besar atau paling banyak dipakai orang. Hindari promo yang sama ditulis dua kali.
- "berakhir" berformat YYYY-MM-DD. Jika sumber tidak menyebut tanggal berakhir, isi null.
- "nilai" angka 1-5: seberapa menguntungkan bagi pembeli umum (5 = sangat besar dan mudah dipakai).
- "url" harus alamat halaman sumber promo yang kamu baca.{catatan}

Balas HANYA dengan JSON array, tanpa teks lain, tanpa markdown. Setiap elemen:
{{"merchant": "...", "judul": "...", "diskon": "ringkas, mis. 'Diskon 50%' atau 'Cashback Rp25.000'", "deskripsi": "satu kalimat", "kode": "kode voucher atau null", "syarat": "syarat utama singkat atau null", "mulai": "YYYY-MM-DD atau null", "berakhir": "YYYY-MM-DD atau null", "url": "https://...", "nilai": 1-5}}

Jika tidak menemukan promo yang valid, balas [] saja."""


# ---------------------------------------------------------------- parsing

def ekstrak_objek(teks):
    teks = re.sub(r"```(?:json)?", "", teks or "")
    awal, akhir = teks.find("["), teks.rfind("]")
    if awal != -1 and akhir > awal:
        try:
            hasil = json.loads(teks[awal:akhir + 1])
            if isinstance(hasil, list):
                return [x for x in hasil if isinstance(x, dict)]
        except json.JSONDecodeError:
            pass
    # cadangan: ambil objek satu per satu dari teks yang terpotong/rusak
    dec, i, hasil = json.JSONDecoder(), 0, []
    while True:
        j = teks.find("{", i)
        if j == -1:
            break
        try:
            obj, end = dec.raw_decode(teks[j:])
            if isinstance(obj, dict):
                hasil.append(obj)
            i = j + end
        except json.JSONDecodeError:
            i = j + 1
    return hasil


def rapikan(raw, kat, tanggal):
    """Validasi satu objek mentah. Return dict bersih atau None."""
    merchant = bersih(raw.get("merchant"), 60)
    judul = bersih(raw.get("judul"), 140)
    url = bersih(raw.get("url"), 500)
    if not merchant or not judul:
        return None
    if not re.match(r"^https?://", url, re.I):
        url = ""
    berakhir = parse_tanggal(raw.get("berakhir"))
    mulai = parse_tanggal(raw.get("mulai"))
    if berakhir and berakhir < tanggal:
        return None
    if mulai and mulai > tanggal:
        return None
    try:
        nilai = max(1, min(5, int(round(float(raw.get("nilai", 3))))))
    except (TypeError, ValueError):
        nilai = 3
    kode = bersih(raw.get("kode"), 40)
    if kode.lower() in ("null", "none", "-", "tidak ada", "n/a"):
        kode = ""
    promo = {
        "kategori": kat["nama"],
        "merchant": merchant,
        "judul": judul,
        "diskon": bersih(raw.get("diskon"), 80),
        "deskripsi": bersih(raw.get("deskripsi"), 300),
        "kode": kode,
        "syarat": bersih(raw.get("syarat"), 300),
        "mulai": mulai.isoformat() if mulai else None,
        "berakhir": berakhir.isoformat() if berakhir else None,
        "url": url,
        "nilai": nilai,
    }
    promo["id"] = buat_id(promo)
    return promo


def cari_kategori(cfg, api_key, kat, tanggal):
    teks, usage = panggil_model(cfg, api_key, buat_prompt(kat, tanggal, cfg["max_promo_per_kategori"]))
    hasil, terlihat = [], set()
    for raw in ekstrak_objek(teks):
        p = rapikan(raw, kat, tanggal)
        if not p:
            continue
        kunci = (p["id"], )
        kunci2 = (p["url"], norm(p["merchant"])) if p["url"] else None
        if kunci in terlihat or (kunci2 and kunci2 in terlihat):
            continue
        terlihat.add(kunci)
        if kunci2:
            terlihat.add(kunci2)
        hasil.append(p)
    return hasil, usage


# ---------------------------------------------------------------- penyimpanan

def muat_store(path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("promos", {})
        except (json.JSONDecodeError, OSError):
            print("Peringatan: data lama rusak, mulai dari kosong.", file=sys.stderr)
    return {}


def gabung(store, baru_per_kategori, kategori_gagal, tanggal):
    """Gabungkan hasil hari ini ke riwayat. Return (store baru, jumlah promo baru)."""
    hari = tanggal.isoformat()
    jumlah_baru = 0
    id_global = {}
    for lst in baru_per_kategori.values():
        for p in lst:
            if p["id"] in id_global:
                continue  # promo sama muncul di dua kategori: simpan yang pertama
            id_global[p["id"]] = p
    for pid, p in id_global.items():
        lama = store.get(pid)
        if lama:
            p["pertama_terlihat"] = lama.get("pertama_terlihat", hari)
        else:
            p["pertama_terlihat"] = hari
            jumlah_baru += 1
        p["terakhir_terlihat"] = hari
        store[pid] = p

    hasil = {}
    for pid, p in store.items():
        berakhir = parse_tanggal(p.get("berakhir"))
        if berakhir and berakhir < tanggal:
            continue
        if p.get("kategori") not in kategori_gagal:
            terakhir = parse_tanggal(p.get("terakhir_terlihat")) or tanggal
            batas = BASI_DENGAN_TANGGAL if berakhir else BASI_TANPA_TANGGAL
            if (tanggal - terakhir).days > batas:
                continue
        hasil[pid] = p
    return hasil, jumlah_baru


# ---------------------------------------------------------------- dashboard

def bangun_dashboard(promos, tanggal, jam_str):
    tpl = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
    data = json.dumps(list(promos.values()), ensure_ascii=False).replace("</", "<\\/")
    return tpl.replace("__DATA__", data).replace("__UPDATED__", html.escape(jam_str))


# ---------------------------------------------------------------- telegram

def sisa_hari(p, tanggal):
    b = parse_tanggal(p.get("berakhir"))
    return (b - tanggal).days if b else None


def susun_pesan(promos, jumlah_baru, tanggal, dashboard_url, kategori_gagal):
    semua = list(promos.values())
    hari = tanggal.isoformat()
    baru = [p for p in semua if p.get("pertama_terlihat") == hari]
    urut = lambda p: (-p["nilai"], p["merchant"])
    pilihan = sorted(baru, key=urut)[:8] or sorted(semua, key=urut)[:8]
    judul_pilihan = "Promo baru pilihan" if baru else "Promo terbaik saat ini"
    segera = sorted(
        [p for p in semua if sisa_hari(p, tanggal) is not None and sisa_hari(p, tanggal) <= 1],
        key=urut,
    )[:4]

    def baris(p, nomor):
        e = html.escape
        sisa = sisa_hari(p, tanggal)
        info = []
        if p["diskon"]:
            info.append(e(p["diskon"]))
        if p["berakhir"]:
            info.append("s/d " + tgl_indo(parse_tanggal(p["berakhir"])))
        if p["kode"]:
            info.append("kode <code>" + e(p["kode"]) + "</code>")
        teks = f"{nomor}. <b>{e(p['merchant'])}</b>: {e(p['judul'])}"
        if info:
            teks += "\n    " + " · ".join(info)
        if p["url"]:
            teks += f'\n    <a href="{e(p["url"], quote=True)}">Lihat promo</a>'
        return teks

    kepala = (
        f"<b>Promo Radar</b> · {HARI[tanggal.weekday()]}, {tgl_indo(tanggal)}\n"
        f"{len(semua)} promo aktif · {jumlah_baru} baru hari ini"
    )
    bagian = [kepala]
    if segera:
        bagian.append("<b>Segera berakhir</b>\n" + "\n".join(baris(p, "•") for p in segera))
    if pilihan:
        bagian.append(f"<b>{judul_pilihan}</b>\n" + "\n".join(baris(p, i + 1) for i, p in enumerate(pilihan)))
    if kategori_gagal:
        bagian.append("Kategori gagal dicek: " + html.escape(", ".join(sorted(kategori_gagal))))
    if dashboard_url:
        bagian.append(f'<a href="{html.escape(dashboard_url, quote=True)}">Buka dashboard lengkap</a>')

    # potong per bagian agar tidak memutus tag HTML, batas Telegram 4096 karakter
    pesan = ""
    for b in bagian:
        if len(pesan) + len(b) + 2 > 3900:
            if b is bagian[-1] and dashboard_url:
                pesan += "\n\n" + b
            continue
        pesan += ("\n\n" if pesan else "") + b
    return pesan


def kirim_telegram(pesan):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram dilewati (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diisi).")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": pesan, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"Telegram gagal: {type(e).__name__}", file=sys.stderr)
        return False
    if r.status_code != 200:
        print(f"Telegram gagal: HTTP {r.status_code} {r.json().get('description', '') if r.headers.get('content-type', '').startswith('application/json') else ''}", file=sys.stderr)
        return False
    print("Telegram terkirim.")
    return True


# ---------------------------------------------------------------- main

def hitung_biaya(cfg, usage_list):
    h = cfg["harga_usd"]
    tin = sum(u.get("input_tokens", 0) or 0 for u in usage_list)
    tout = sum(u.get("output_tokens", 0) or 0 for u in usage_list)
    cari = sum(((u.get("tool_usage") or {}).get("web_search", 0) or 0) for u in usage_list)
    biaya = tin / 1e6 * h["input_per_juta_token"] + tout / 1e6 * h["output_per_juta_token"] + cari * h["per_web_search"]
    return tin, tout, cari, biaya


def main(argv=None):
    ap = argparse.ArgumentParser(description="Promo Radar")
    ap.add_argument("--kategori", help="jalankan hanya kategori yang namanya mengandung teks ini")
    ap.add_argument("--tanpa-telegram", action="store_true")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--docs-dir", default=str(ROOT / "docs"))
    args = ap.parse_args(argv)

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    api_key = bersihkan_key(os.environ.get("MODEL_API_KEY", ""))
    if not api_key:
        raise SystemExit("MODEL_API_KEY belum diisi.")
    print(f"API key terbaca: {len(api_key)} karakter, diawali '{api_key[:4]}...'")

    tanggal = hari_ini()
    kategori = [k for k in cfg["kategori"] if k.get("aktif", True)]
    if args.kategori:
        kategori = [k for k in kategori if args.kategori.lower() in k["nama"].lower()]
    if not kategori:
        raise SystemExit("Tidak ada kategori yang cocok.")

    print(f"Mencari promo {tanggal.isoformat()} untuk {len(kategori)} kategori ...")
    hasil, gagal, usages = {}, set(), []
    with ThreadPoolExecutor(max_workers=max(1, int(cfg.get("paralel", 4)))) as ex:
        futures = {ex.submit(cari_kategori, cfg, api_key, k, tanggal): k for k in kategori}
        for f in as_completed(futures):
            k = futures[f]
            try:
                lst, usage = f.result()
                hasil[k["nama"]] = lst
                usages.append(usage)
                print(f"  ok   {k['nama']}: {len(lst)} promo")
            except SystemExit:
                raise
            except Exception as e:
                gagal.add(k["nama"])
                print(f"  GAGAL {k['nama']}: {e}", file=sys.stderr)

    if not hasil:
        raise SystemExit("Semua kategori gagal. Data lama tidak diubah.")

    data_dir, docs_dir = Path(args.data_dir), Path(args.docs_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    store = muat_store(data_dir / "promos.json")
    promos, jumlah_baru = gabung(store, hasil, gagal, tanggal)

    sekarang = datetime.now(WIB)
    jam_str = f"{HARI[sekarang.weekday()]}, {tgl_indo(sekarang.date())} {sekarang:%H:%M} WIB"
    (data_dir / "promos.json").write_text(
        json.dumps({"diperbarui": sekarang.isoformat(), "promos": promos}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (docs_dir / "index.html").write_text(bangun_dashboard(promos, tanggal, jam_str), encoding="utf-8")

    tin, tout, cari, biaya = hitung_biaya(cfg, usages)
    print(f"Selesai: {len(promos)} promo aktif, {jumlah_baru} baru. "
          f"Token {tin:,} masuk / {tout:,} keluar, {cari} pencarian, perkiraan biaya ${biaya:.3f}.")

    if not args.tanpa_telegram:
        pesan = susun_pesan(promos, jumlah_baru, tanggal, os.environ.get("DASHBOARD_URL", ""), gagal)
        kirim_telegram(pesan)


if __name__ == "__main__":
    main()
