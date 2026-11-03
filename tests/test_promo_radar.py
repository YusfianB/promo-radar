import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import promo_radar as pr  # noqa: E402

HARI = date(2026, 11, 4)


def respons(promos):
    return {
        "output": [
            {"type": "web_search_call"},
            {"type": "message", "content": [{"type": "output_text", "text": "```json\n" + json.dumps(promos) + "\n```"}]},
        ],
        "usage": {"input_tokens": 20000, "output_tokens": 1500, "tool_usage": {"web_search": 3}},
    }


def promo(**kw):
    p = {"merchant": "KFC", "judul": "Paket Hemat 5", "diskon": "Diskon 30%", "deskripsi": "Paket hemat.",
         "kode": None, "syarat": "Dine in", "mulai": "2026-11-01", "berakhir": "2026-11-10",
         "url": "https://kfcku.com/promo", "nilai": 4}
    p.update(kw)
    return p


class Parsing(unittest.TestCase):
    def test_ekstrak_dari_markdown(self):
        t = '```json\n[{"judul":"a"},{"judul":"b"}]\n```'
        self.assertEqual(len(pr.ekstrak_objek(t)), 2)

    def test_ekstrak_json_terpotong(self):
        t = '[{"merchant":"A","judul":"x"},{"merchant":"B","judul":"y"},{"merchant":"C","jud'
        self.assertEqual([o["merchant"] for o in pr.ekstrak_objek(t)], ["A", "B"])

    def test_ekstrak_sampah(self):
        self.assertEqual(pr.ekstrak_objek("maaf tidak ada"), [])

    def test_ambil_teks(self):
        self.assertIn("KFC", pr.ambil_teks(respons([promo()])))
        self.assertEqual(pr.ambil_teks({"output_text": "halo"}), "halo")

    def test_rapikan_buang_kedaluwarsa_dan_belum_mulai(self):
        kat = {"nama": "T"}
        self.assertIsNone(pr.rapikan(promo(berakhir="2026-11-03"), kat, HARI))
        self.assertIsNone(pr.rapikan(promo(mulai="2026-11-09"), kat, HARI))
        self.assertIsNotNone(pr.rapikan(promo(berakhir="2026-11-04"), kat, HARI))

    def test_rapikan_url_dan_kode_dan_nilai(self):
        kat = {"nama": "T"}
        p = pr.rapikan(promo(url="javascript:alert(1)", kode="null", nilai="9", berakhir=None), kat, HARI)
        self.assertEqual(p["url"], "")
        self.assertEqual(p["kode"], "")
        self.assertEqual(p["nilai"], 5)
        self.assertIsNone(p["berakhir"])

    def test_rapikan_wajib_merchant_judul(self):
        self.assertIsNone(pr.rapikan(promo(merchant=""), {"nama": "T"}, HARI))


class Prompt(unittest.TestCase):
    def test_prompt_lokal_dan_global(self):
        lokal = pr.buat_prompt({"nama": "T", "fokus": "x", "wilayah": "Surabaya"}, HARI, 5)
        self.assertIn("di Indonesia", lokal)
        glob = pr.buat_prompt({"nama": "AI", "fokus": "x", "wilayah": "Global (bisa)", "catatan": "Hanya resmi."}, HARI, 5)
        self.assertIn("di seluruh dunia", glob)
        self.assertIn("- Hanya resmi.", glob)
        self.assertNotIn("{", glob.split("Balas HANYA")[0])


class KunciAPI(unittest.TestCase):
    def test_bersihkan_key(self):
        k = "LLM|123456|abcDEF_-xyz"
        self.assertEqual(pr.bersihkan_key(k), k)
        self.assertEqual(pr.bersihkan_key(" \n" + k + "\n"), k)
        self.assertEqual(pr.bersihkan_key('"' + k + '"'), k)
        self.assertEqual(pr.bersihkan_key('{\n  "email": "a@b.c",\n  "apikey": "' + k + '"\n}'), k)
        self.assertEqual(pr.bersihkan_key('apikey: ' + k + '\nemail: a@b.c'), k)
        self.assertEqual(pr.bersihkan_key("abc def\nghi"), "abcdefghi")
        self.assertEqual(pr.bersihkan_key(""), "")


class Gabung(unittest.TestCase):
    def olah(self, lst, kat="A"):
        out = []
        for r in lst:
            p = pr.rapikan(r, {"nama": kat}, HARI)
            if p:
                out.append(p)
        return out

    def test_dedupe_lintas_kategori_dan_baru(self):
        a = self.olah([promo()], "A")
        b = self.olah([promo(judul="paket  hemat 5!")], "B")
        store, baru = pr.gabung({}, {"A": a, "B": b}, set(), HARI)
        self.assertEqual(len(store), 1)
        self.assertEqual(baru, 1)

    def test_pertama_terlihat_dipertahankan(self):
        a = self.olah([promo()])
        store, _ = pr.gabung({}, {"A": a}, set(), date(2026, 11, 2))
        a2 = self.olah([promo()])
        store, baru = pr.gabung(store, {"A": a2}, set(), HARI)
        self.assertEqual(baru, 0)
        self.assertEqual(list(store.values())[0]["pertama_terlihat"], "2026-11-02")

    def test_kedaluwarsa_dan_basi_dibuang(self):
        lama = self.olah([promo(merchant="X", berakhir="2026-11-20"), promo(merchant="Y", berakhir=None)])
        for p in lama:
            p["terakhir_terlihat"] = "2026-10-01"
        store = {p["id"]: p for p in lama}
        baru = self.olah([promo(merchant="Z")])
        hasil, _ = pr.gabung(store, {"A": baru}, set(), HARI)
        self.assertEqual({p["merchant"] for p in hasil.values()}, {"Z"})

    def test_kategori_gagal_tidak_dianggap_basi(self):
        lama = self.olah([promo(merchant="X", berakhir=None)], "Gagal")
        lama[0]["terakhir_terlihat"] = "2026-10-01"
        store = {lama[0]["id"]: lama[0]}
        hasil, _ = pr.gabung(store, {"A": self.olah([promo(merchant="Z")])}, {"Gagal"}, HARI)
        self.assertEqual(len(hasil), 2)


class Pesan(unittest.TestCase):
    def test_pesan_telegram_aman(self):
        p = pr.rapikan(promo(judul="Diskon <b>& untung", kode="HEMAT<1>", berakhir="2026-11-05"), {"nama": "A"}, HARI)
        p["pertama_terlihat"] = HARI.isoformat()
        pesan = pr.susun_pesan({p["id"]: p}, 1, HARI, "https://x.github.io/promo/", {"Travel"})
        self.assertIn("&lt;b&gt;", pesan)
        self.assertIn("HEMAT&lt;1&gt;", pesan)
        self.assertIn("Segera berakhir", pesan)
        self.assertIn("Kategori gagal dicek: Travel", pesan)
        self.assertLess(len(pesan), 4096)

    def test_pesan_dipotong_tetap_pendek(self):
        semua = {}
        for i in range(200):
            p = pr.rapikan(promo(merchant=f"M{i}", judul="J" * 120, berakhir="2026-11-05"), {"nama": "A"}, HARI)
            p["pertama_terlihat"] = HARI.isoformat()
            semua[p["id"]] = p
        pesan = pr.susun_pesan(semua, 200, HARI, "https://x.github.io/promo/", set())
        self.assertLess(len(pesan), 4096)
        self.assertIn("Buka dashboard lengkap", pesan)


class Utuh(unittest.TestCase):
    def jalankan(self, fake):
        with tempfile.TemporaryDirectory() as d:
            env = {"MODEL_API_KEY": "kunci-palsu"}
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(pr, "hari_ini", return_value=HARI), \
                 mock.patch.object(pr.requests, "post", side_effect=fake) as post:
                pr.main(["--data-dir", d + "/data", "--docs-dir", d + "/docs", "--tanpa-telegram"])
            data = json.loads(Path(d, "data/promos.json").read_text())
            page = Path(d, "docs/index.html").read_text()
            return data, page, post

    def test_alur_penuh_dengan_satu_kategori_gagal(self):
        class R:
            def __init__(s, code, body):
                s.status_code, s._b, s.text, s.headers = code, body, json.dumps(body), {}
            def json(s):
                return s._b

        def fake(url, headers=None, json=None, timeout=None):
            self.assertTrue(url.endswith("/responses"))
            self.assertEqual(json["tools"], [{"type": "web_search"}])
            self.assertEqual(headers["Authorization"], "Bearer kunci-palsu")
            if "Travel" in json["input"]:
                return R(400, {"error": "model tidak ada"})
            nama = "KFC" if "Makanan" in json["input"] else "Shopee"
            return R(200, respons([promo(merchant=nama, judul=nama + " promo"), promo(merchant="Basi", berakhir="2026-01-01")]))

        with mock.patch.object(pr.time, "sleep"):
            data, page, post = self.jalankan(fake)
        merchants = {p["merchant"] for p in data["promos"].values()}
        self.assertIn("KFC", merchants)
        self.assertIn("Shopee", merchants)
        self.assertNotIn("Basi", merchants)
        self.assertIn("const DATA = [", page)
        self.assertNotIn("__DATA__", page)
        self.assertNotIn("__UPDATED__", page)
        self.assertNotIn("kunci-palsu", page)


if __name__ == "__main__":
    unittest.main(verbosity=2)
