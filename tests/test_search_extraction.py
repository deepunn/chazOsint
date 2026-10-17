"""CHAZ OSINT - Arama motoru link çözme ve aday doğrulama testleri.

Bu testler, "arama sonuçları hiç aday üretmiyordu" hatasını (Bing `u=a1<base64>`
kodlaması ve DuckDuckGo `uddg=` yönlendirmeleri) kalıcı olarak korur.

Çalıştırma:
    python -m unittest tests.test_search_extraction -v
"""

import base64
import unittest

from collectors.web_harvester import WebHarvester


def _bing_wrap(url: str) -> str:
    """Bing'in `u=a1<base64url>` biçiminde kodladığı gerçek linki üretir."""
    encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f'<a href="https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;u=a1{encoded}&amp;ntb=1">sonuç</a>'


class _FakeResp:
    def __init__(self, code, text, url):
        self.status_code, self.text, self.url = code, text, url


class _VerifySession:
    """Gerçek profil için 200 + og:title, olmayan için 404 döner."""

    def __init__(self):
        self.calls = 0

    def get(self, url, headers=None, timeout=None, allow_redirects=None):
        self.calls += 1
        if "gercek" in url.lower():
            handle = url.rstrip("/").split("/")[-1].lstrip("@")
            html = (f'<html><head><meta property="og:title" content="{handle} (@{handle})">'
                    f'<title>{handle}</title></head><body>{handle}</body></html>')
            return _FakeResp(200, html, url)
        return _FakeResp(404, "not found", url)


class TestSearchExtraction(unittest.TestCase):
    def setUp(self):
        self.h = WebHarvester("Aser Bugat")

    def test_bing_base64_links_decoded(self):
        html = _bing_wrap("https://x.com/abugat") + _bing_wrap("https://github.com/abugat")
        urls = self.h._decode_bing_redirects(html)
        self.assertIn("https://x.com/abugat", urls)
        self.assertIn("https://github.com/abugat", urls)

    def test_ddg_uddg_links_decoded(self):
        html = ('<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.com%2Fabugat'
                '&rut=xyz">link</a>')
        urls = self.h._decode_ddg_redirects(html)
        self.assertIn("https://x.com/abugat", urls)

    def test_extract_from_decoded_extra_urls(self):
        """Kodlanmış link çözülünce aday olarak yakalanmalı."""
        cands = self.h._extract_social_candidates("<html></html>",
                                                   ["https://x.com/abugat"])
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["platform"], "Twitter / X")
        self.assertEqual(cands[0]["handle"], "abugat")

    def test_unrelated_search_result_ignored(self):
        cands = self.h._extract_social_candidates("<html></html>",
                                                   ["https://x.com/tamamen_alakasiz_kisi"])
        self.assertEqual(cands, [])

    def test_irrelevant_snippets_filtered(self):
        """Hedef adı geçmeyen (alakasız) özet metinleri rapora girmemeli."""
        snippets = [
            "Aser Bugat hakkında bir haber metni",
            "Convert YouTube videos to accurate text or subtitles.",
            "bugat sosyal medya hesabı hakkında",
        ]
        kept = self.h._filter_relevant_snippets(snippets)
        self.assertEqual(len(kept), 2)
        self.assertNotIn(snippets[1], kept)

    def test_snippet_deduplication(self):
        kept = self.h._filter_relevant_snippets(["abugat profil sayfası",
                                                 "ABUGAT profil sayfası"])
        self.assertEqual(len(kept), 1)

    def test_search_candidates_verified_and_filtered(self):
        """Arama adayları içerikle doğrulanır; ölü olan elenir."""
        h = WebHarvester("Aser Bugat", session=_VerifySession())
        store = {
            ("Twitter / X", "https://x.com/gercek_kisi"): {
                "platform": "Twitter / X", "url": "https://x.com/gercek_kisi",
                "handle": "gercek_kisi", "platform_confirm": 0.3,
                "page_has_handle": False, "source_count": 1, "source": "search",
                "target": "Aser Bugat",
            },
            ("Instagram", "https://instagram.com/olu_link"): {
                "platform": "Instagram", "url": "https://instagram.com/olu_link",
                "handle": "olu_link", "platform_confirm": 0.3,
                "page_has_handle": False, "source_count": 1, "source": "search",
                "target": "Aser Bugat",
            },
        }
        h._verify_search_candidates(store)

        good = store[("Twitter / X", "https://x.com/gercek_kisi")]
        dead = store[("Instagram", "https://instagram.com/olu_link")]

        self.assertGreater(good["platform_confirm"], 0.5)
        self.assertTrue(good["page_has_handle"])
        self.assertEqual(good["source"], "search+verified")
        self.assertTrue(dead.get("rejected"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
