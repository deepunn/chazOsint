"""CHAZ OSINT - Altyapı testleri (ayar, önbellek, HTTP, DB, pipeline).

Çalıştırma (proje kökünden):
    python -m unittest tests.test_infrastructure -v
"""

import os
import sqlite3
import tempfile
import unittest

from PIL import Image

from config.settings import Settings, get_settings
from utils.cache import DiskCache
from collectors.http_client import HttpClient
from database.storage import DatabaseManager
from core.pipeline import OSINTPipeline


class _FakeResp:
    def __init__(self, code, text, url):
        self.status_code, self.text, self.url = code, text, url


# --- Ayarlar ----------------------------------------------------------------

class TestSettings(unittest.TestCase):
    def test_loads_config_files(self):
        s = get_settings()
        self.assertTrue(s.get("app_name"))
        self.assertGreaterEqual(len(s.user_agents), 1)     # user_agents.txt
        self.assertIn("twitter", s.regex_signatures)        # regex_signatures.json
        self.assertGreater(s.timeout, 0)
        self.assertIn("v", s.describe())

    def test_overrides_win(self):
        s = Settings(overrides={"timeout": 99})
        self.assertEqual(s.timeout, 99)

    def test_unknown_attribute_raises(self):
        with self.assertRaises(AttributeError):
            _ = get_settings().kesinlikle_yok


# --- Önbellek ---------------------------------------------------------------

class TestDiskCache(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "cache.json")

    def test_set_get(self):
        c = DiskCache(path=self.path, ttl=60)
        c.set("k", {"a": 1})
        self.assertEqual(c.get("k"), {"a": 1})

    def test_ttl_expiry(self):
        c = DiskCache(path=self.path, ttl=-1)   # her kayıt anında süresi geçer
        c.set("k", "v")
        self.assertIsNone(c.get("k"))

    def test_disabled(self):
        c = DiskCache(path=self.path, enabled=False)
        c.set("k", "v")
        self.assertIsNone(c.get("k"))

    def test_clear_and_stats(self):
        c = DiskCache(path=self.path, ttl=60)
        c.set("a", 1)
        c.set("b", 2)
        self.assertEqual(c.stats()["entries"], 2)
        c.clear()
        self.assertEqual(c.stats()["entries"], 0)


# --- HTTP istemcisi ---------------------------------------------------------

class _FlakySession:
    """İlk çağrıda 500, sonra 200 döner."""

    def __init__(self):
        self.calls = 0

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        self.calls += 1
        return _FakeResp(500 if self.calls == 1 else 200, "govde", url)


class _CountingSession:
    def __init__(self):
        self.calls = 0

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        self.calls += 1
        return _FakeResp(200, "icerik", url)


class TestHttpClient(unittest.TestCase):
    def test_retries_then_succeeds(self):
        s = Settings(overrides={"retries": 2, "backoff": 0})
        session = _FlakySession()
        client = HttpClient(settings=s, session=session,
                            cache=DiskCache(enabled=False))
        resp = client.get("https://example.com")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(session.calls, 2)

    def test_cache_avoids_second_request(self):
        s = Settings(overrides={"cache_ttl": 60, "cache_enabled": True})
        session = _CountingSession()
        cache = DiskCache(path=os.path.join(tempfile.mkdtemp(), "c.json"), ttl=60)
        client = HttpClient(settings=s, session=session, cache=cache)
        client.get("https://example.com/x")
        second = client.get("https://example.com/x")
        self.assertEqual(session.calls, 1)       # ikinci istek önbellekten
        self.assertTrue(second.from_cache)


# --- Veritabanı -------------------------------------------------------------

class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "db.sqlite")

    def test_scan_history_roundtrip(self):
        db = DatabaseManager(self.path)
        db.save_scan("Ahmet Yılmaz", "rapor metni", confidence=87.5, verified_count=3)
        rows = db.recent_scans()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target"], "Ahmet Yılmaz")
        self.assertAlmostEqual(rows[0]["confidence"], 87.5)
        record = db.get_scan(rows[0]["id"])
        self.assertEqual(record["report"], "rapor metni")

    def test_fingerprint_roundtrip(self):
        db = DatabaseManager(self.path)
        db.save_fingerprint("abc123", "Ahmet Yılmaz", "/x/y.jpg")
        matches = db.find_fingerprint("abc123")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["target"], "Ahmet Yılmaz")
        self.assertEqual(db.find_fingerprint("yok"), [])

    def test_migration_adds_columns(self):
        # Eski şema: yalnızca 4 sütun
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TABLE investigations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, target_name TEXT,
                report_data TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")
            conn.commit()
        DatabaseManager(self.path)  # migrasyon burada çalışır
        with sqlite3.connect(self.path) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(investigations)")}
        self.assertIn("confidence", cols)
        self.assertIn("verified_count", cols)
        # Yeni şema ile kayıt yapılabilmeli
        db = DatabaseManager(self.path)
        db.save_scan("t", "r", 50.0, 1)
        self.assertEqual(db.stats()["scans"], 1)


# --- Pipeline ---------------------------------------------------------------

class _FakeHarvester:
    def __init__(self, target):
        self.target = target

    def harvest(self):
        return {
            "snippets": ["Örnek açık kaynak özeti " * 3],
            "candidates": [{
                "platform": "GitHub", "url": "https://github.com/johnsmith",
                "handle": "johnsmith", "target": self.target,
                "archive": "https://web.archive.org/x",
                "platform_confirm": 1.0, "page_has_handle": True,
                "source_count": 2, "is_profile_url": True, "source": "direct",
            }],
            "socials": [],
        }


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(os.path.join(tempfile.mkdtemp(), "p.sqlite"))

    def test_run_verifies_and_persists(self):
        pipeline = OSINTPipeline(db=self.db,
                                 harvester_factory=lambda t: _FakeHarvester(t))
        logs = []
        session = pipeline.run("John Smith", on_log=logs.append)

        self.assertTrue(session.verified)
        self.assertEqual(session.verified[0]["platform"], "GitHub")
        self.assertGreater(session.confidence, 0)
        self.assertIn("GitHub", session.report)
        self.assertIn("network", session.__dict__)
        self.assertTrue(session.network.get("nodes"))
        self.assertEqual(self.db.recent_scans()[0]["target"], "John Smith")
        self.assertTrue(logs)

    def test_photo_fingerprint_detected_on_second_run(self):
        img_path = os.path.join(tempfile.mkdtemp(), "p.jpg")
        Image.new("RGB", (64, 48), (10, 20, 30)).save(img_path)

        pipeline = OSINTPipeline(db=self.db,
                                 harvester_factory=lambda t: _FakeHarvester(t))
        first = pipeline.run("John Smith", photo_path=img_path)
        self.assertIsNotNone(first.photo)
        self.assertEqual(first.fingerprint_matches, [])
        self.assertIn("FOTOĞRAF", first.report)

        second = pipeline.run("Jane Doe", photo_path=img_path)
        self.assertTrue(second.fingerprint_matches)   # aynı görsel yakalanmalı
        self.assertIn("TEKRAR KULLANIM", second.report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
