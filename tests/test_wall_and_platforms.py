"""CHAZ OSINT - Platform duvarı (Instagram/TikTok) ve kapsam testleri.

Bu dosya üç regresyonu kalıcı olarak korur:

  1. Instagram/TikTok gibi platformlar profili anonim istemciye göstermez.
     Bu yüzden \"içerik kanıtı yok\" bilgisi \"hesap yok\" SAYILAMAZ: aday
     silinmemeli, elle kontrol edilecek \"aday\" olarak sunulmalıydı.
  2. Instagram'ın JS kabuğunda geçen `ox_redesigned_404_page` metni yanlışlıkla
     \"hesap yok\" işareti sayılıyordu; bu metin NATGEO gibi kesin var olan
     hesaplarda da geçtiği için gerçek profiller eleniyordu.
  3. TikTok ve LinkedIn hiç yoklanmıyordu; artık doğrudan taranıyor.

Çalıştırma:
    python -m unittest tests.test_wall_and_platforms -v
"""

import unittest

from collectors.web_harvester import (OPAQUE_PLATFORMS, PLATFORM_TARGETS,
                                      WebHarvester)
from intelligence.ai_engine import AIEngine
from intelligence.handle_generator import TIER_INITIAL
from debugger.heuristic_filter import HeuristicFilter


class _FakeResp:
    def __init__(self, code, text, url):
        self.status_code, self.text, self.url = code, text, url


# Instagram'ın gerçek anonim yanıtının indirgenmiş hali: giriş kabuğu +
# JS paketinden gelen \"404\" bileşen adı (kanıt DEĞİLDİR).
IG_SHELL = ("<html><head><title>Instagram</title>"
            "<style data-btmanifest=\"1\">:root{--fds-black:#000}</style></head>"
            "<body><div id=\"react-root\"><script>ox_redesigned_404_page "
            "PolarisProfilePage require_login</script>"
            "Log in to Instagram.</div></body></html>")

# TikTok'un WAF meydan okuma sayfası: var olan/olmayan kullanıcı için aynı.
TIKTOK_WAF = ("<html><head><script id=\"slardar-config\">"
              "{\"slardarClient\":\"SlardarWAF\"}</script></head>"
              "<body>Please wait...<p id=\"wci\" class=\"_wafchallengeid\"></p>"
              "</body></html>")

NOT_FOUND = "<html><body>Sorry, this page isn't available.</body></html>"


class _WallSession:
    """Instagram/TikTok/LinkedIn duvarlarını ve gerçek 404'ü taklit eder."""

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        low = url.lower()
        if "instagram.com" in low:
            return _FakeResp(200, IG_SHELL, url)
        if "tiktok.com" in low:
            return _FakeResp(200, TIKTOK_WAF, url)
        if "linkedin.com" in low:
            return _FakeResp(999, "", url)
        if "soundcloud.com" in low:
            return _FakeResp(404, NOT_FOUND, url)
        return _FakeResp(404, NOT_FOUND, url)


def _harvester(target="hamigumi"):
    h = WebHarvester(target, session=_WallSession())
    h.rate_limiter.wait = lambda: None
    return h


class TestPlatformCoverage(unittest.TestCase):
    def test_new_platforms_are_probed(self):
        platforms = {name for name, _tpl in PLATFORM_TARGETS}
        for expected in ("Instagram", "TikTok", "Twitter / X", "YouTube",
                         "LinkedIn", "Twitch", "GitHub", "SoundCloud", "Spotify"):
            self.assertIn(expected, platforms)

    def test_handle_budget_is_tier_aware_for_opaque_platforms(self):
        """Duyarlı platformlarda yalnızca isme doğrudan bağlı biçimler denenir."""
        h = WebHarvester("Aser Bugat")
        strong = h._candidate_handles(strong_only=True)
        self.assertIn("aserbugat", strong)
        for handle in strong:
            self.assertLessEqual(h.handle_tiers.get(handle, 9), TIER_INITIAL)
        # gevşek biçimler (ünlü düşürme, harf sırası değişimi) listeye girmemeli
        for loose in ("srbgt", "saerbugat", "aserbug"):
            self.assertNotIn(loose, strong)
        # gevşek biçimler normal (tam) deneme listesinde bulunur
        full = h._candidate_handles()
        self.assertIn("saerbugat", full)
        self.assertTrue(any(h.handle_tiers.get(x, 0) > TIER_INITIAL for x in full))
        self.assertFalse(any(h.handle_tiers.get(x, 0) > TIER_INITIAL for x in strong))


class TestWalledPlatforms(unittest.TestCase):
    def test_instagram_shell_is_wall_not_missing(self):
        """Giriş kabuğu 'hesap yok' sayılamaz; duvar olarak işaretlenmeli."""
        v = _harvester()._verify_profile(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        self.assertIsNone(v["exists"])
        self.assertTrue(v["wall"])

    def test_instagram_js_404_component_is_not_evidence(self):
        """Kök neden: JS paketindeki '404' bileşen adı kanıt değildir."""
        v = _harvester()._verify_profile(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        self.assertIsNot(v["exists"], False)
        self.assertFalse(any("ox_redesigned_404_page" in m for m in v["markers"]))

    def test_tiktok_waf_is_wall(self):
        v = _harvester()._verify_profile(
            "TikTok", "https://www.tiktok.com/@hamigumi", "hamigumi")
        self.assertIsNone(v["exists"])
        self.assertTrue(v["wall"])

    def test_linkedin_bot_block_is_wall(self):
        v = _harvester()._verify_profile(
            "LinkedIn", "https://www.linkedin.com/in/hamigumi", "hamigumi")
        self.assertIsNone(v["exists"])
        self.assertTrue(v["wall"])

    def test_real_404_is_still_rejected(self):
        """Duvar tespiti gerçek 'yok' sonucunu maskelememeli."""
        v = _harvester()._verify_profile(
            "SoundCloud", "https://soundcloud.com/hamigumi", "hamigumi")
        self.assertIs(v["exists"], False)
        self.assertFalse(v["wall"])

    def test_wall_candidates_are_kept_for_manual_check(self):
        h = _harvester()
        data = h.harvest()
        walls = [c for c in data["candidates"] if c.get("wall")]
        by_platform = {c["platform"] for c in walls}
        self.assertIn("Instagram", by_platform)
        self.assertIn("TikTok", by_platform)
        # doğrulanamayan adreslerle liste dolmasın: platform başına sınır var
        for platform in by_platform:
            self.assertLessEqual(
                sum(1 for c in walls if c["platform"] == platform),
                int(h.settings.get("max_wall_candidates", 3)))

    def test_wall_never_verified_but_listed_as_lead(self):
        """Duvar adayları 'doğrulandı' sayılmaz, aday listesinde gösterilir."""
        engine = AIEngine()
        candidates = [{
            "target": "hamigumi", "platform": "Instagram",
            "url": "https://www.instagram.com/hamigumi/", "handle": "hamigumi",
            "platform_confirm": 0.3, "page_has_handle": False, "source_count": 1,
            "is_profile_url": True, "variant_tier": 0, "wall": True,
        }]
        verified, tentative, _logs = HeuristicFilter(engine=engine).verify_candidates(
            "hamigumi", candidates)
        self.assertEqual(verified, [])
        self.assertEqual([c["handle"] for c in tentative], ["hamigumi"])
        self.assertIn("duvar", tentative[0]["lead_reason"])

    def test_lead_order_puts_exact_match_first(self):
        engine = AIEngine()
        candidates = [
            {"target": "hamigumi", "platform": "GitHub",
             "url": "https://github.com/hamigu", "handle": "hamigu",
             "platform_confirm": 0.3, "page_has_handle": False, "source_count": 1,
             "is_profile_url": True, "variant_tier": 4, "wall": True},
            {"target": "hamigumi", "platform": "Instagram",
             "url": "https://www.instagram.com/hamigumi/", "handle": "hamigumi",
             "platform_confirm": 0.3, "page_has_handle": False, "source_count": 1,
             "is_profile_url": True, "variant_tier": 0, "wall": True},
        ]
        _v, tentative, _logs = HeuristicFilter(engine=engine).verify_candidates(
            "hamigumi", candidates)
        self.assertEqual(tentative[0]["platform"], "Instagram")


class TestUsernameQueryPolicy(unittest.TestCase):
    """Kullanıcı adı sorgusu ve tam ad sorgusu farklı davranmalı."""

    def setUp(self):
        self.engine = AIEngine()

    def test_single_token_query_with_mismatching_display_name_is_accepted(self):
        """'hamigumi' yazıldı, sayfa var (YouTube): görünen ad farklı olsa da eşleşme."""
        cand = {
            "target": "hamigumi", "platform": "YouTube",
            "url": "https://www.youtube.com/@hamigumi", "handle": "hamigumi",
            "platform_confirm": 0.55, "page_has_handle": False, "source_count": 2,
            "is_profile_url": True, "variant_tier": 0,
            "display_name": "mahami megumi",
        }
        label, prob, feats = self.engine.classify(cand)
        self.assertEqual(label, 1)
        self.assertGreaterEqual(prob, self.engine.threshold)
        self.assertEqual(feats["single_token_target"], 1.0)
        self.assertEqual(feats["display_name_conflict"], 1.0)

    def test_full_name_query_with_conflicting_page_is_rejected(self):
        """Tam ad sorgusunda sayfa TAMAMEN başka birini gösteriyorsa yanlış eşleşmedir."""
        cand = {
            "target": "Ahmet Yilmaz", "platform": "GitHub",
            "url": "https://github.com/ahmetyilmaz", "handle": "ahmetyilmaz",
            "platform_confirm": 0.8, "page_has_handle": True, "source_count": 2,
            "is_profile_url": True, "variant_tier": 0,
            "display_name": "Mehmet Kaya",
        }
        label, _prob, feats = self.engine.classify(cand)
        self.assertEqual(label, 0)
        self.assertEqual(feats["display_name_conflict"], 1.0)
        self.assertIn("çelişiyor", feats["decision_basis"])

    def test_partial_display_name_with_strong_evidence_is_accepted(self):
        """Görünen ad kısmen tutuyor (ör. ad aynı) ve sayfa doğrulandıysa eşleşme."""
        cand = {
            "target": "Ahmet Yilmaz", "platform": "GitHub",
            "url": "https://github.com/ahmetyilmaz", "handle": "ahmetyilmaz",
            "platform_confirm": 0.8, "page_has_handle": True, "source_count": 2,
            "is_profile_url": True, "variant_tier": 0,
            "display_name": "Ahmet Bilisim",
        }
        label, _prob, feats = self.engine.classify(cand)
        self.assertEqual(label, 1)
        self.assertEqual(feats["display_name_conflict"], 0.0)

    def test_new_features_are_extracted(self):
        feats = self.engine.extract_features({
            "target": "hamigumi", "platform": "TikTok", "handle": "hamigumi",
            "url": "https://www.tiktok.com/@hamigumi", "wall": True,
        })
        for key in ("wall", "display_name_present", "single_token_target",
                    "display_name_conflict"):
            self.assertIn(key, feats)
        self.assertEqual(feats["wall"], 1.0)
        self.assertEqual(feats["single_token_target"], 1.0)

    def test_wall_explanation_is_honest(self):
        feats = self.engine.extract_features({
            "target": "hamigumi", "platform": "Instagram", "handle": "hamigumi",
            "url": "https://www.instagram.com/hamigumi/", "wall": True,
        })
        self.assertIn("duvar", self.engine.explain(feats))


if __name__ == "__main__":
    unittest.main(verbosity=2)
