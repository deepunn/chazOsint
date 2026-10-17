"""CHAZ OSINT - Duvarlı platformlar için alternatif doğrulama testleri.

Korunan davranış (yanlış-pozitif / yanlış-negatif dengesi):

  * Sağlayıcı KANIT bulursa aday doğrulanır ve görünen adı öğrenilir
    (TikTok oEmbed / embed, Instagram web API'si, kullanıcının kendi oturumu).
  * Sağlayıcı kanıt bulamazsa bu ASLA \"hesap yok\" sayılmaz; aday
    \"elle kontrol\" listesinde kalır (throttle/WAF/gizli hesap olabilir).
  * Yalnızca güvenilir bir 404 kesin \"yok\" sayılır.

Çalıştırma:
    python -m unittest tests.test_platform_verifiers -v
"""

import json
import unittest

from collectors.http_client import HttpClient
from collectors.platform_verifiers import (CONFIRMED, REJECTED, UNKNOWN,
                                           AltFrontendVerifier,
                                           InstagramHtmlVerifier,
                                           InstagramProfileApiVerifier,
                                           SessionCookieVerifier,
                                           TikTokEmbedVerifier,
                                           TikTokOEmbedVerifier,
                                           VerifierRegistry)
from collectors.web_harvester import WebHarvester
from config.settings import Settings
from debugger.heuristic_filter import HeuristicFilter
from intelligence.ai_engine import AIEngine
from utils.cache import DiskCache


class _Resp:
    def __init__(self, code, text="", url="https://example.invalid/"):
        self.status_code, self.text, self.url = code, text, url


class _Session:
    """URL'ye göre sahte yanıt döndüren basit oturum."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, headers=None, timeout=None, allow_redirects=None, **kw):
        self.calls.append((url, headers))
        for fragment, response in self.routes.items():
            if fragment in url:
                return response(url) if callable(response) else response
        return _Resp(404, "not found", url)


def _client(routes):
    return HttpClient(settings=Settings(), cache=DiskCache(enabled=False),
                      session=_Session(routes))


OEMBED_OK = json.dumps({
    "version": "1.0", "type": "rich", "title": "hamigumi (@hamigumi) | TikTok",
    "author_name": "Hamigumi", "author_url": "https://www.tiktok.com/@hamigumi",
})

EMBED_OK = ('<html><body><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" '
            'type="application/json">{"__DEFAULT_SCOPE__":{"webapp.user-detail":'
            '{"userInfo":{"user":{"id":"1","uniqueId":"hamigumi",'
            '"nickname":"Hamigumi"}}}}}</script></body></html>')

EMBED_WAF = ('<html><body>Please wait...'
             '<p id="wci" class="_wafchallengeid"></p></body></html>')

IG_OK = json.dumps({"data": {"user": {"username": "hamigumi",
                                      "full_name": "Hami Gumi",
                                      "is_private": False}},
                    "status": "ok"})


class TestTikTokVerifiers(unittest.TestCase):
    def test_oembed_confirms_with_display_name(self):
        client = _client({"tiktok.com/oembed": _Resp(200, OEMBED_OK)})
        result = TikTokOEmbedVerifier(client, Settings()).verify(
            "TikTok", "https://www.tiktok.com/@hamigumi", "hamigumi")
        self.assertEqual(result.status, CONFIRMED)
        self.assertEqual(result.display_name, "Hamigumi")

    def test_oembed_other_user_is_not_evidence(self):
        other = json.dumps({"author_name": "Someone", "author_url":
                            "https://www.tiktok.com/@someoneelse"})
        client = _client({"tiktok.com/oembed": _Resp(200, other)})
        result = TikTokOEmbedVerifier(client, Settings()).verify(
            "TikTok", "https://www.tiktok.com/@hamigumi", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)

    def test_oembed_throttle_is_unknown_not_missing(self):
        """429/503 'ratelimit triggered' yanıtı 'hesap yok' DEĞİLDİR."""
        client = _client({"tiktok.com/oembed": _Resp(429, "ratelimit triggered")})
        result = TikTokOEmbedVerifier(client, Settings()).verify(
            "TikTok", "https://www.tiktok.com/@hamigumi", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)

    def test_oembed_404_is_unknown_not_missing(self):
        """Gizli/yaş kısıtlı hesaplar da gömülemez: 404 kanıt sayılmaz."""
        client = _client({"tiktok.com/oembed": _Resp(404, '{"status_msg":"error"}')})
        result = TikTokOEmbedVerifier(client, Settings()).verify(
            "TikTok", "https://www.tiktok.com/@hamigumi", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)

    def test_embed_confirms_from_universal_data(self):
        client = _client({"tiktok.com/embed/@hamigumi": _Resp(200, EMBED_OK)})
        result = TikTokEmbedVerifier(client, Settings()).verify(
            "TikTok", "https://www.tiktok.com/@hamigumi", "hamigumi")
        self.assertEqual(result.status, CONFIRMED)
        self.assertEqual(result.display_name, "Hamigumi")

    def test_embed_waf_page_is_unknown(self):
        client = _client({"tiktok.com/embed/@hamigumi": _Resp(200, EMBED_WAF)})
        result = TikTokEmbedVerifier(client, Settings()).verify(
            "TikTok", "https://www.tiktok.com/@hamigumi", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)

    def test_embed_other_user_is_not_evidence(self):
        other = EMBED_OK.replace("hamigumi", "someoneelse")
        client = _client({"tiktok.com/embed/@hamigumi": _Resp(200, other)})
        result = TikTokEmbedVerifier(client, Settings()).verify(
            "TikTok", "https://www.tiktok.com/@hamigumi", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)


class TestInstagramVerifier(unittest.TestCase):
    def test_api_confirms_and_returns_full_name(self):
        client = _client({"web_profile_info": _Resp(200, IG_OK)})
        result = InstagramProfileApiVerifier(client, Settings()).verify(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        self.assertEqual(result.status, CONFIRMED)
        self.assertEqual(result.display_name, "Hami Gumi")

    def test_api_404_is_rejected(self):
        client = _client({"web_profile_info": _Resp(404, '{"status":"fail"}')})
        result = InstagramProfileApiVerifier(client, Settings()).verify(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        self.assertEqual(result.status, REJECTED)

    def test_api_login_required_is_unknown(self):
        body = '{"message":"Please wait a few minutes before you try again.",' \
               '"require_login":true,"status":"fail"}'
        client = _client({"web_profile_info": _Resp(401, body)})
        result = InstagramProfileApiVerifier(client, Settings()).verify(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)

    def test_api_other_user_is_not_evidence(self):
        other = json.dumps({"data": {"user": {"username": "someoneelse"}}})
        client = _client({"web_profile_info": _Resp(200, other)})
        result = InstagramProfileApiVerifier(client, Settings()).verify(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)

    def test_app_id_header_is_sent(self):
        session = _Session({"web_profile_info": _Resp(200, IG_OK)})
        client = HttpClient(settings=Settings(), cache=DiskCache(enabled=False),
                            session=session)
        InstagramProfileApiVerifier(client, Settings()).verify(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        sent = [h for _u, h in session.calls if h]
        self.assertTrue(any("x-ig-app-id" in (h or {}) for h in sent))


IG_HTML_OK = ('<html><head><meta property="og:title" content="National Geographic '
              '(&#064;hamigumi) &#x2022; Instagram foto&#x11f;raflar&#x131;">'
              '<meta property="og:description" content="268M Followers"></head></html>')

IG_HTML_WALL = ("<html><head><style>:root{--fds-black:#000}</style></head>"
                '<body>--fds- polarisprofilepage login</body></html>')


class TestInstagramHtmlVerifier(unittest.TestCase):
    """Ölçülen davranış: og:title/duvar ayrımı.

    Ölçüm (17.10.2026): Chrome taklidiyle çağrıldığında GERÇEK profil sayfası
    "Görünen Ad (@kullanıcı) • Instagram..." başlığını veriyor; kabuk/duvar
    yanıtı og:title içermiyor. Kanıt yokluğu "hesap yok" değildir.
    """

    def test_confirms_and_cleans_display_name(self):
        client = _client({"instagram.com/hamigumi": _Resp(200, IG_HTML_OK)})
        result = InstagramHtmlVerifier(client, Settings()).verify(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        self.assertEqual(result.status, CONFIRMED)
        self.assertEqual(result.display_name, "National Geographic")

    def test_title_of_another_user_is_not_evidence(self):
        page = IG_HTML_OK.replace("hamigumi", "someoneelse")
        client = _client({"instagram.com/hamigumi": _Resp(200, page)})
        result = InstagramHtmlVerifier(client, Settings()).verify(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)

    def test_wall_shell_is_unknown_not_missing(self):
        client = _client({"instagram.com/hamigumi": _Resp(200, IG_HTML_WALL)})
        result = InstagramHtmlVerifier(client, Settings()).verify(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)
        self.assertFalse(result.rejected)

    def test_real_404_is_rejected(self):
        client = _client({"instagram.com/hamigumi": _Resp(404, "not found")})
        result = InstagramHtmlVerifier(client, Settings()).verify(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        self.assertEqual(result.status, REJECTED)


class TestImpersonationTransport(unittest.TestCase):
    """Testlerde Chrome taklidi ASLA devreye girmemeli (ağa çıkılmaz)."""

    def test_custom_session_disables_impersonation(self):
        client = _client({})
        self.assertIsNone(client.impersonating_session)
        self.assertFalse(client.impersonation_available)

    def test_impersonate_flag_falls_back_to_custom_session(self):
        session = _Session({"instagram.com/hamigumi": _Resp(200, IG_HTML_OK)})
        client = HttpClient(settings=Settings(), cache=DiskCache(enabled=False),
                            session=session)
        resp = client.get("https://www.instagram.com/hamigumi/", impersonate=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(session.calls), 1)

    def test_cache_keys_differ_between_transports(self):
        """Taklitli ve taklitsiz yanıtlar farklıdır; önbellekte karışmamalı."""
        client = _client({})
        plain = client._cache_key("https://www.instagram.com/x/", None, impersonate=False)
        impersonated = client._cache_key("https://www.instagram.com/x/", None,
                                        impersonate=True)
        self.assertNotEqual(plain, impersonated)


class TestTikTokEmbedPositiveEvidence(unittest.TestCase):
    def test_waf_marker_does_not_override_real_data(self):
        """Embed sayfası WAF betiği taşısa da içindeki gerçek veri kanıttır."""
        page = EMBED_OK.replace("<body>", '<body><script src="slardarWAF.js"></script>')
        client = _client({"tiktok.com/embed/@hamigumi": _Resp(200, page)})
        result = TikTokEmbedVerifier(client, Settings()).verify(
            "TikTok", "https://www.tiktok.com/@hamigumi", "hamigumi")
        self.assertEqual(result.status, CONFIRMED)
        self.assertEqual(result.display_name, "Hamigumi")

    def test_tiny_503_is_unknown_but_descriptive(self):
        client = _client({"tiktok.com/embed/@hamigumi": _Resp(503, "Service Unavailable")})
        result = TikTokEmbedVerifier(client, Settings()).verify(
            "TikTok", "https://www.tiktok.com/@hamigumi", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)
        self.assertIn("veri döndürmedi", result.evidence)


class TestSessionAndAltFrontend(unittest.TestCase):
    def test_session_verifier_is_disabled_without_cookie(self):
        client = _client({"instagram.com/hamigumi": _Resp(200, "<html></html>")})
        verifier = SessionCookieVerifier(client, Settings(), cookie_header="")
        self.assertFalse(verifier.enabled)
        result = verifier.verify("Instagram", "https://www.instagram.com/hamigumi/",
                                 "hamigumi")
        self.assertEqual(result.status, UNKNOWN)

    def test_session_verifier_confirms_with_cookie(self):
        page = '<meta property="og:title" content="Hami Gumi (@hamigumi) · Instagram">'
        client = _client({"instagram.com/hamigumi": _Resp(200, page)})
        verifier = SessionCookieVerifier(client, Settings(), cookie_header="sessionid=abc")
        result = verifier.verify("Instagram", "https://www.instagram.com/hamigumi/",
                                 "hamigumi")
        self.assertEqual(result.status, CONFIRMED)
        # Görünen ad, başlıktaki kullanıcı adı ve platform eki temizlenerek verilir.
        self.assertEqual(result.display_name, "Hami Gumi")

    def test_alt_frontend_disabled_by_default(self):
        client = _client({})
        result = AltFrontendVerifier(client, Settings()).verify(
            "Instagram", "https://www.instagram.com/hamigumi/", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)

    def test_alt_frontend_confirms_when_configured(self):
        page = '<meta property="og:title" content="hamigumi">'
        client = _client({"mirror.example/hamigumi": _Resp(200, page)})
        verifier = AltFrontendVerifier(client, Settings(),
                                      templates=["https://mirror.example/{h}/"])
        result = verifier.verify("Instagram", "https://www.instagram.com/hamigumi/",
                                 "hamigumi")
        self.assertEqual(result.status, CONFIRMED)


class TestRegistryAndHarvest(unittest.TestCase):
    def test_registry_prefers_definite_result(self):
        client = _client({"tiktok.com/oembed": _Resp(429, "ratelimit triggered"),
                          "tiktok.com/embed/@hamigumi": _Resp(200, EMBED_OK)})
        result = VerifierRegistry(client, Settings()).verify(
            "TikTok", "https://www.tiktok.com/@hamigumi", "hamigumi")
        self.assertEqual(result.status, CONFIRMED)

    def test_unknown_platform_has_no_provider(self):
        client = _client({})
        result = VerifierRegistry(client, Settings()).verify(
            "Spotify", "https://open.spotify.com/user/hamigumi", "hamigumi")
        self.assertEqual(result.status, UNKNOWN)

    def test_instagram_html_evidence_promotes_candidate(self):
        """Chrome taklidiyle okunan og:title, adayı DOĞRULANMIŞ yapar."""
        h = WebHarvester("hamigumi", session=_Session({
            "instagram.com/hamigumi": _Resp(200, IG_HTML_OK),
        }))
        h.rate_limiter.wait = lambda: None
        candidates = h._verify_platform("Instagram", "https://www.instagram.com/{h}/")
        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        self.assertFalse(cand.get("wall"))
        self.assertGreaterEqual(cand["platform_confirm"], 0.5)
        self.assertEqual(cand["display_name"], "National Geographic")

    def test_wall_candidate_is_promoted_when_provider_confirms(self):
        h = WebHarvester("hamigumi", session=_Session({
            "instagram.com/hamigumi": _Resp(200, "<html>--fds- login</html>"),
            "web_profile_info": _Resp(200, IG_OK),
        }))
        h.rate_limiter.wait = lambda: None
        candidates = h._verify_platform("Instagram", "https://www.instagram.com/{h}/")
        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        self.assertFalse(cand.get("wall"))
        self.assertGreaterEqual(cand["platform_confirm"], 0.5)
        self.assertEqual(cand["display_name"], "Hami Gumi")
        self.assertEqual(cand["verified_by"], "instagram-web-api")

    def test_wall_candidate_remains_lead_when_provider_blocked(self):
        h = WebHarvester("hamigumi", session=_Session({
            "instagram.com/hamigumi": _Resp(200, "<html>--fds- login</html>"),
            "web_profile_info": _Resp(401, '{"require_login":true,"status":"fail"}'),
        }))
        h.rate_limiter.wait = lambda: None
        candidates = h._verify_platform("Instagram", "https://www.instagram.com/{h}/")
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["wall"])
        self.assertLess(candidates[0]["platform_confirm"], 0.5)

    def test_provider_confirmed_candidate_can_be_verified_by_ai(self):
        """Sağlayıcının kanıtı (profil + görünen ad) doğrulanmış sonuç üretir."""
        engine = AIEngine()
        candidates = [{
            "target": "hamigumi", "platform": "Instagram",
            "url": "https://www.instagram.com/hamigumi/", "handle": "hamigumi",
            "platform_confirm": 0.8, "page_has_handle": True, "source_count": 2,
            "is_profile_url": True, "variant_tier": 0,
            "display_name": "Hami Gumi", "verified_by": "instagram-web-api",
        }]
        verified, tentative, _logs = HeuristicFilter(engine=engine).verify_candidates(
            "hamigumi", candidates)
        self.assertEqual([c["handle"] for c in verified], ["hamigumi"])
        self.assertEqual(tentative, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
