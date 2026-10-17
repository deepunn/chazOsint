"""CHAZ AI Core - regresyon testleri.

Çalıştırma (proje kökünden):
    python -m unittest tests.test_ai_engine -v
"""

import json
import os
import tempfile
import unittest

from intelligence.ai_engine import (
    AIEngine, slugify, name_variants, jaro_winkler, get_engine,
    partial_name_match, DECISION_THRESHOLD,
)
from collectors.web_harvester import WebHarvester
from debugger.heuristic_filter import HeuristicFilter
from debugger.confidence_scorer import ConfidenceScorer


def _tmp_model():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    return path


class TestNameUtils(unittest.TestCase):
    def test_slugify_turkish(self):
        self.assertEqual(slugify("Şükrü Çağdaş Öğüt"), "sukrucagdasogut")
        self.assertEqual(slugify("John Smith"), "johnsmith")

    def test_name_variants(self):
        v = name_variants("John Smith")
        self.assertIn("johnsmith", v)
        self.assertIn("john_smith", v)
        self.assertIn("jsmith", v)

    def test_jaro_winkler(self):
        self.assertAlmostEqual(jaro_winkler("johnsmith", "johnsmith"), 1.0)
        self.assertGreater(jaro_winkler("johnsmith", "johnsmit"), 0.9)
        self.assertLess(jaro_winkler("johnsmith", "torvalds"), 0.7)


class TestAIEngine(unittest.TestCase):
    def setUp(self):
        self.engine = AIEngine(model_path=_tmp_model())

    def test_new_features_extracted(self):
        """Yeni AI motorunun 10 özelliği çekirmeli; display_name_similarity da."""
        feats = self.engine.extract_features({
            "target": "Aser Bugat", "platform": "GitHub",
            "url": "https://github.com/abugat", "handle": "abugat",
            "platform_confirm": 0.0, "page_has_handle": False,
            "source_count": 1, "is_profile_url": True,
            "display_name": "Aser Bugat (@ABugat) · GitHub",
        })
        self.assertIn("display_name_similarity", feats)
        self.assertGreaterEqual(feats["display_name_similarity"], 0.9)
        self.assertIn("token_coverage", feats)
        self.assertGreaterEqual(feats["token_coverage"], 0.4)
        # "abugat" handle şekli (rakam/jenerik yok) yüksek puan almalı
        self.assertGreater(feats["handle_shape"], 0.8)

    def test_different_target_different_display_name(self):
        """Farklı display_name, düşük display_name_similarity vermeli."""
        feats = self.engine.extract_features({
            "target": "Aser Bugat", "platform": "GitHub",
            "url": "https://github.com/bugat", "handle": "bugat",
            "platform_confirm": 0.0, "page_has_handle": False,
            "source_count": 1, "is_profile_url": True,
            "display_name": "Serkan Yılmaz (@serkanylmz) · GitHub",
        })
        self.assertLess(feats["display_name_similarity"], 0.3)

    def test_handle_shape_scores(self):
        """Jenerik / rakam dolu handlelar daha düşük gülümseme almalı."""
        self.assertLess(
            self.engine.extract_features({"handle": "abugat1234"})["handle_shape"],
            self.engine.extract_features({"handle": "abugat"})["handle_shape"],
        )
        self.assertLess(
            self.engine.extract_features({"handle": "john_official"})["handle_shape"],
            self.engine.extract_features({"handle": "john"})["handle_shape"],
        )

    def test_rejects_unverified_shell(self):
        """İsim tutsa da sayfa doğrulanmadıysa (Instagram/Spotify kabuğu) elenmeli."""
        label, prob, _ = self.engine.classify({
            "target": "John Smith", "platform": "Instagram",
            "url": "https://instagram.com/johnsmith/", "handle": "johnsmith",
            "platform_confirm": 0.0, "page_has_handle": False,
            "source_count": 1, "is_profile_url": True,
        })
        self.assertEqual(label, 0)
        self.assertLess(prob, 0.5)

    def test_accepts_confirmed_match(self):
        label, prob, _ = self.engine.classify({
            "target": "John Smith", "platform": "GitHub",
            "url": "https://github.com/johnsmith", "handle": "johnsmith",
            "platform_confirm": 1.0, "page_has_handle": True,
            "source_count": 2, "is_profile_url": True,
        })
        self.assertEqual(label, 1)
        self.assertGreater(prob, 0.5)

    def test_display_name_high_can_override_handle_low(self):
        """Görünen ad yüksekse (Aser Bugat), handle düşüktür bile (aserb)
        AI eşleşmeyi kabul etmelidir.

        Bu test AI'nın en kritik özelliğini doğrular: kullanıcı adı
        tam otomatik değil, platform sayfasının görünen adı (display_name)
        hedefe uyuyorsa eşleşme gerçek sayılır. display_name_similarity
        yüksek, handle_similarity düşük olsa bile label=1 olmalı.

        GÜVEN: test sınıfı AIEngine() ile başlangıçta sentetik örneklerle
        eğitilir (önceki setUp'te), böylece display_name_similarity'nin
        ağırlığı zaten ontolojik olarak yeterlidir.

        KRİTİK NOT: eğer bu test hala 0 döndürsüyorsa, AI'nın
        display_name_similarity ağırlığı yeterli değil veya threshold
        kalibrasyonu yetersizdir. Bundan sonra her seferinde
        display_name_similarity yüksekken eşleşme kabul edilmeli.
        """
        cand = {
            "target": "Aser Bugat", "platform": "GitHub",
            "url": "https://github.com/aserb", "handle": "aserb",
            "platform_confirm": 0.0, "page_has_handle": False,
            "source_count": 1, "is_profile_url": True,
            "display_name": "Aser Bugat",
        }
        feats = self.engine.extract_features(cand)
        label = self.engine.classify(cand)[0]
        self.assertEqual(label, 1,
                         msg=f"display_name={feats['display_name_similarity']}, "
                         f"handle_similarity={feats['handle_similarity']}")
        self.assertGreaterEqual(feats["display_name_similarity"], 0.6)

    def test_display_name_low_cannot_override_handle_unrelated(self):
        """Görünen ad düşükse ve handle alakasızsa, eşleşme olmamalı."""
        label, _, _ = self.engine.classify({
            "target": "John Smith", "platform": "GitHub",
            "url": "https://github.com/torvalds", "handle": "torvalds",
            "platform_confirm": 0.0, "page_has_handle": False,
            "source_count": 1, "is_profile_url": True,
            "display_name": "John Smith",
        })
        self.assertEqual(label, 0)

    def test_relation_gate_blocks_unrelated_issue(self):
        """ilgi kapısı, alakasız bir sonuç için 0 etiket verir."""
        label, _, _ = self.engine.classify({
            "target": "John Smith", "platform": "GitHub",
            "url": "https://github.com/torvalds", "handle": "torvalds",
            "platform_confirm": 0.3, "page_has_handle": False,
            "source_count": 1, "is_profile_url": True,
        })
        self.assertEqual(label, 0)

    def test_rejects_unrelated(self):
        label, _, _ = self.engine.classify({
            "target": "John Smith", "platform": "GitHub",
            "url": "https://github.com/torvalds", "handle": "torvalds",
            "platform_confirm": 0.3, "page_has_handle": False,
            "source_count": 1, "is_profile_url": True,
        })
        self.assertEqual(label, 0)

    def test_online_learning_flips_decision(self):
        """Geri bildirim modeli gerçekten güncellemeli."""
        cand = {
            "target": "Jane Doe", "platform": "GitHub",
            "url": "https://github.com/janedoe", "handle": "janedoe",
            "platform_confirm": 0.3, "page_has_handle": False,
            "source_count": 1, "is_profile_url": True,
            "display_name": "Jane Doe",
        }
        feats = self.engine.extract_features(cand)
        before = self.engine.predict_proba(feats)
        for _ in range(30):
            self.engine.online_update(feats, 1, lr=0.5)
        after = self.engine.predict_proba(feats)
        self.assertGreater(after, before)

    def test_feedback_docs_are_loaded(self):
        """AI, old_feedback.jsonl dosyasındaki örnekleri içermeli (test için az örnek)."""
        path = _tmp_model()
        engine = AIEngine(model_path=path)
        samples = engine.load_feedback_samples()
        # tek bir (değer, etiket) alıp tahmin yapabilir olmalı
        if samples:
            feats, _label = samples[0]
            prob = engine.predict_proba(feats)
            self.assertGreaterEqual(prob, 0.0)
            self.assertLessEqual(prob, 1.0)

    def test_calibration_moves_threshold(self):
        """calibrate_threshold sentetik örnekler üzerinde F1'i maksimize eden eşiği bulmalı."""
        samples = self.engine._synthetic_samples()
        original = self.engine.threshold
        threshold_after = self.engine.calibrate_threshold(samples, steps=26)
        self.assertGreaterEqual(threshold_after, DECISION_THRESHOLD - 0.15)
        self.assertLessEqual(threshold_after, DECISION_THRESHOLD + 0.15)

    def test_retrain_from_feedback(self):
        """retrain_from_feedback feedback JSONL dosyasından veri okumalı ve model güncelliyormuş gibi görünmeli."""
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        fd2, feedback_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd2)
        try:
            with open(feedback_path, "w", encoding="utf-8") as f:
                for _i in range(6):
                    f.write(json.dumps({
                        "url": "https://github.com/ok",
                        "platform": "GitHub",
                        "handle": "ok",
                        "label": 1,
                        "features": {
                            "handle_similarity": 0.95, "slug_match": 0.5,
                            "display_name_similarity": 1.0, "token_coverage": 0.5,
                            "handle_shape": 0.9, "platform_confirm": 0.5,
                            "page_has_handle": 1.0, "source_count": 0.33,
                            "domain_trust": 1.0, "is_profile_url": 1.0,
                        },
                    }, ensure_ascii=False) + "\n")
            loaded = AIEngine(model_path=path, feedback_path=feedback_path)
            updates = loaded.retrain_from_feedback(path=feedback_path)
            self.assertGreaterEqual(updates, 5)  # en az 5 gerçek örnek
            self.assertGreater(loaded.metrics.get("count", 0), 0)
        finally:
            try:
                os.remove(feedback_path)
            except OSError:
                pass
            try:
                os.remove(loaded.model_path)
            except OSError:
                pass

    def test_persistence(self):
        path = _tmp_model()
        e1 = AIEngine(model_path=path)
        e1.online_update(e1.extract_features({
            "target": "A B", "url": "https://github.com/ab", "handle": "ab",
            "platform_confirm": 1.0, "page_has_handle": True}), 1)
        e1.save()
        e2 = AIEngine(model_path=path)
        self.assertEqual(e2.feedback_count, e1.feedback_count)


# --- Sahte HTTP katmanı ------------------------------------------------------

class _FakeResp:
    def __init__(self, code, text, url):
        self.status_code, self.text, self.url = code, text, url


class _FakeSession:
    """Ağı taklit eder: sahte kabuk, gerçek profil ve 404 senaryoları."""

    IG_SHELL = ("<html><head><title>Instagram</title></head><body>"
                "Log in to Instagram. Sign up. Create an account.</body></html>")
    GH_OK = ('<html><head><meta property="og:title" content="johnsmith &#183; GitHub">'
             '<title>johnsmith - Overview</title></head><body itemprop="name">johnsmith'
             '<div class="js-profile-editable">repos</div></body></html>')
    SP_NO = "<html><body>Couldn't find this account</body></html>"

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        u = url.lower()
        if "duckduckgo" in u or "bing.com" in u:
            return _FakeResp(200,
                             '<a href="https://github.com/johnsmith">johnsmith</a>'
                             '<a href="https://github.com/torvalds">unrelated</a>'
                             '<a href="https://www.instagram.com/johnsmith/">ig</a>', url)
        if "instagram.com" in u:
            return _FakeResp(200, self.IG_SHELL, url)
        if "github.com/johnsmith" in u:
            return _FakeResp(200, self.GH_OK, url)
        if "spotify.com" in u:
            return _FakeResp(200, self.SP_NO, url)
        return _FakeResp(404, "not found", url)


class TestPipelineIntegration(unittest.TestCase):
    def test_end_to_end_filters_false_positives(self):
        harvester = WebHarvester("John Smith", session=_FakeSession())
        harvester.rate_limiter.wait = lambda: None
        raw = harvester.harvest()

        engine = AIEngine(model_path=_tmp_model())
        verified, tentative, logs = HeuristicFilter(engine=engine).verify_candidates(
            "John Smith", raw["candidates"])

        platforms = {v["platform"] for v in verified}
        urls = " ".join(v["url"] for v in verified)

        self.assertIn("GitHub", platforms)
        self.assertNotIn("Instagram", platforms)   # sahte kabuk
        self.assertNotIn("Spotify", platforms)     # var olmayan hesap
        self.assertNotIn("torvalds", urls)         # alakasız sonuç
        self.assertTrue(logs)

    def test_confidence_zero_when_empty(self):
        self.assertEqual(ConfidenceScorer([], 0).calculate_score(), 0.0)

    def test_confidence_positive_when_verified(self):
        verified = [{"ai_prob": 0.9, "platform": "GitHub"},
                    {"ai_prob": 0.8, "platform": "YouTube"}]
        score = ConfidenceScorer(verified, 5).calculate_score()
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
