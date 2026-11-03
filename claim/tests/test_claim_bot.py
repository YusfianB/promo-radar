"""Uji bot klaim terhadap situs tiruan lokal. Tidak menyentuh situs asli."""
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claim_bot as cb  # noqa: E402

HITS = {"klaim": 0, "evil": 0}

PAGES = {
    "/ok": '<h1>Promo AI</h1><button onclick="fetch(\'/klaim\').then(()=>{document.getElementById(\'s\').textContent=\'Berhasil diklaim\'})">Klaim sekarang</button><p id="s"></p>',
    "/captcha": '<h1>Promo</h1><iframe src="https://www.google.com/recaptcha/api2/anchor"></iframe><button>Klaim sekarang</button>',
    "/bayar": '<h1>Promo</h1><input name="cardnumber" autocomplete="cc-number"><button>Klaim sekarang</button>',
    "/verifikasi": "<h1>Promo</h1><p>Student verification required. Upload your ID.</p><button>Klaim sekarang</button>",
    "/tanpa-tombol": "<h1>Promo</h1><p>Tidak ada tombol di sini.</p>",
}


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/klaim":
            HITS["klaim"] += 1
            body = "ok"
        elif self.path in PAGES:
            body = "<!doctype html><meta charset=utf-8>" + PAGES[self.path]
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


class Uji(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def siapkan(self, d):
        resep = Path(d, "recipes")
        resep.mkdir()
        (resep / "mock.json").write_text(json.dumps({
            "nama": "Mock", "domain": ["127.0.0.1"],
            "langkah": [{"aksi": "klik", "teks": "klaim"}, {"aksi": "tunggu_teks", "teks": "berhasil", "detik": 5}],
            "berhasil_jika_ada_teks": ["berhasil diklaim"],
        }))
        base = f"http://127.0.0.1:{self.port}"
        promos = {"promos": {
            "a": {"id": "a", "merchant": "M", "judul": "ok", "url": base + "/ok", "nilai": 5},
            "b": {"id": "b", "merchant": "M", "judul": "captcha", "url": base + "/captcha", "nilai": 4},
            "c": {"id": "c", "merchant": "M", "judul": "bayar", "url": base + "/bayar", "nilai": 3},
            "d": {"id": "d", "merchant": "M", "judul": "verifikasi", "url": base + "/verifikasi", "nilai": 3},
            "e": {"id": "e", "merchant": "M", "judul": "tanpa tombol", "url": base + "/tanpa-tombol", "nilai": 2},
            "f": {"id": "f", "merchant": "Jahat", "judul": "domain asing", "url": "http://evil.example/ok", "nilai": 5},
            "g": {"id": "g", "merchant": "Jahat", "judul": "bukan https", "url": "javascript:alert(1)", "nilai": 5},
        }}
        (Path(d) / "promos.json").write_text(json.dumps(promos))
        return str(resep), str(Path(d) / "promos.json")

    def jalankan(self, d, *ekstra):
        resep, sumber = self.siapkan(d) if not Path(d, "recipes").exists() else (str(Path(d, "recipes")), str(Path(d, "promos.json")))
        with mock.patch.dict("os.environ", {"PROMO_RADAR_HOME": d + "/home"}), mock.patch.object(cb, "HOME", Path(d) / "home"):
            cb.main(["klaim", "--resep-dir", resep, "--sumber", sumber, "--headless", "--jeda", "0", "0", *ekstra])

    def test_mode_uji_tidak_mengklik_dan_tidak_menulis_log(self):
        HITS["klaim"] = 0
        with tempfile.TemporaryDirectory() as d:
            self.jalankan(d, "--maks", "10")
            self.assertEqual(HITS["klaim"], 0)
            self.assertFalse(Path(d, "home/klaim_log.json").exists())

    def test_klaim_nyata_dengan_semua_pengaman(self):
        HITS["klaim"] = 0
        with tempfile.TemporaryDirectory() as d:
            self.jalankan(d, "--jalan", "--maks", "10")
            log = json.loads(Path(d, "home/klaim_log.json").read_text())
            self.assertEqual(log["a"]["status"], "berhasil")
            self.assertEqual(log["b"]["status"], "dilewati")
            self.assertIn("CAPTCHA", log["b"]["catatan"])
            self.assertEqual(log["c"]["status"], "dilewati")
            self.assertIn("kartu", log["c"]["catatan"])
            self.assertEqual(log["d"]["status"], "dilewati")
            self.assertEqual(log["e"]["status"], "gagal")
            self.assertNotIn("f", log)  # domain tanpa resep tidak dikunjungi
            self.assertNotIn("g", log)  # bukan https
            self.assertEqual(HITS["klaim"], 1)  # hanya promo "a" yang benar-benar diklik

            # eksekusi kedua: tidak mengulang promo yang sudah tercatat
            self.jalankan(d, "--jalan", "--maks", "10")
            self.assertEqual(HITS["klaim"], 1)

    def test_batas_maks(self):
        with tempfile.TemporaryDirectory() as d:
            self.jalankan(d, "--jalan", "--maks", "2")
            log = json.loads(Path(d, "home/klaim_log.json").read_text())
            self.assertEqual(len(log), 2)

    def test_host_aman(self):
        self.assertEqual(cb.host_aman("https://a.com/x"), "a.com")
        self.assertIsNone(cb.host_aman("http://a.com/x"))
        self.assertIsNone(cb.host_aman("javascript:alert(1)"))
        self.assertEqual(cb.host_aman("http://localhost:8000/x"), "localhost")


if __name__ == "__main__":
    unittest.main(verbosity=2)
