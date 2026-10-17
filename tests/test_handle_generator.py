"""CHAZ OSINT - Kullanıcı adı varyasyon üreticisi + eşleşme derecelendirme testleri.

Bu testler üç şeyi kalıcı olarak korur:
  1) İsimden ÇOK sayıda varyasyon üretilir (kısaltma, ayraç, ünlü düşürme,
     harf düşmesi, harf sırası değişimi...).
  2) Deneme bütçesi tier'lar arasında dönüşümlü paylaşılır; dar bütçede bile
     hem güçlü hem gevşek biçimler denenir.
  3) Zengin varyasyonlar yanlış-pozitife yol AÇMAZ: gevşek biçimler
     "doğrulandı" sayılmaz, ayrı bir aday listesine düşer.

Çalıştırma:
    python -m unittest tests.test_handle_generator -v
"""

import os
import re
import tempfile
import unittest

from intelligence.handle_generator import (
    generate_handles, generate_handles_with_tiers, order_for_probing,
    tokenize, grade_for_tier, tier_label,
    TIER_EXACT, TIER_SEPARATOR, TIER_INITIAL, TIER_WORD, TIER_LOOSE,
    STRONG_TIER_MAX,
)
from intelligence.ai_engine import AIEngine, get_engine
from debugger.heuristic_filter import HeuristicFilter


def _tmp_model():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    return path


class TestHandleGenerator(unittest.TestCase):
    def test_tokenize(self):
        self.assertEqual(tokenize("Aser Bugat"), ["aser", "bugat"])
        self.assertEqual(tokenize("Ahmet Yılmaz"), ["ahmet", "yilmaz"])
        self.assertEqual(tokenize("abugat"), ["abugat"])

    def test_exact_slug_first(self):
        handles = generate_handles("Aser Bugat")
        self.assertEqual(handles[0], "aserbugat")

    def test_separator_and_initial_forms(self):
        handles = generate_handles("Aser Bugat")
        for expected in ["aser_bugat", "aser.bugat", "aser-bugat", "abugat"]:
            self.assertIn(expected, handles)

    def test_many_variant_families(self):
        """İstenen genişlik: tek bir isimden onlarca biçim üretilebilmeli."""
        handles = generate_handles("Aser Bugat")
        self.assertGreaterEqual(len(handles), 30)
        # ayraç ailesi
        self.assertIn("aser_bugat", handles)
        # baş harf ailesi
        self.assertTrue({"abugat", "aserb"} & set(handles))
        # kelime kısaltması / ters sıra / tek kelime ailesi
        self.assertTrue({"bugataser", "asbugat", "aserbug", "bugat"} & set(handles))
        # harf sırası değişimi ailesi
        self.assertTrue(any(h in handles for h in ("saerbugat", "aesrbugat")))
        # ünlü düşürme ailesi ("aserbugat" -> "srbgt")
        self.assertIn("srbgt", handles)

    def test_letter_order_swaps_present(self):
        """Kullanıcının istediği 'harf sırası değişimi' biçimleri üretilmeli."""
        handles = set(generate_handles("abugat"))
        for expected in ("baugat", "aubgat", "abguat", "abuagt", "abugta"):
            self.assertIn(expected, handles)

    def test_letter_drop_and_vowel_drop(self):
        handles = set(generate_handles("abugat"))
        self.assertIn("bugat", handles)    # baştaki harf düşmesi
        self.assertIn("abuga", handles)    # sondaki harf düşmesi
        self.assertIn("bgt", handles)      # ünlü düşürme

    def test_loose_forms_present(self):
        loose = generate_handles("Aser Bugat", include_loose=True)
        strict = generate_handles("Aser Bugat", include_loose=False)
        self.assertGreater(len(loose), len(strict))
        # ünlü düşürme gibi gevşek biçimler yalnızca loose modda gelir
        self.assertNotIn("srbgt", strict)

    def test_limit_respected(self):
        self.assertEqual(len(generate_handles("Ahmet Yılmaz", limit=3)), 3)

    def test_all_valid_and_unique(self):
        handles = generate_handles("Aser Bugat")
        self.assertEqual(len(handles), len(set(handles)))
        for h in handles:
            self.assertTrue(re.fullmatch(r"[A-Za-z0-9._\-]{3,30}", h), h)

    def test_single_word_name(self):
        handles = generate_handles("abugat")
        self.assertEqual(handles[0], "abugat")
        self.assertIn("bugat", handles)   # harf düşmesi

    def test_tiers_are_ordered(self):
        tiered = generate_handles_with_tiers("Aser Bugat")
        tiers = [t for _h, t in tiered]
        self.assertEqual(tiers, sorted(tiers))       # artan tier
        self.assertEqual(tiered[0][1], TIER_EXACT)
        self.assertEqual(max(tiers), TIER_LOOSE)

    def test_empty_name(self):
        self.assertEqual(generate_handles(""), [])
        self.assertEqual(generate_handles("   "), [])


class TestProbeOrder(unittest.TestCase):
    """Dar deneme bütçesinde bile her biçim ailesinden örnek denenmeli."""

    def test_round_robin_keeps_diversity(self):
        tiered = generate_handles_with_tiers("Aser Bugat")
        probe = order_for_probing(tiered, limit=12)
        self.assertEqual(len(probe), 12)
        self.assertEqual(probe[0], "aserbugat")          # en olası biçim ilk
        probed = set(probe)
        grades = [grade_for_tier(t) for h, t in tiered if h in probed]
        self.assertIn("strong", grades)
        self.assertIn("lead", grades)
        # gevşek bir biçim (harf sırası değişimi) gerçekten deneniyor
        loose = [h for h, t in tiered if h in probed and t >= TIER_WORD]
        self.assertTrue(loose, "gevşek biçim deneme listesine hiç girmemiş")

    def test_limit_none_returns_all(self):
        tiered = generate_handles_with_tiers("Aser Bugat")
        self.assertEqual(len(order_for_probing(tiered)), len(tiered))

    def test_single_token_covers_every_form(self):
        tiered = generate_handles_with_tiers("abugat")
        probe = order_for_probing(tiered, limit=12)
        self.assertEqual(set(probe), {h for h, _t in tiered})

    def test_no_duplicates_in_probe_list(self):
        probe = order_for_probing(generate_handles_with_tiers("Ahmet Yılmaz"), limit=12)
        self.assertEqual(len(probe), len(set(probe)))


class TestGrading(unittest.TestCase):
    def test_strong_tiers(self):
        for tier in (TIER_EXACT, TIER_SEPARATOR, TIER_INITIAL):
            self.assertEqual(grade_for_tier(tier), "strong")
            self.assertLessEqual(tier, STRONG_TIER_MAX)

    def test_lead_tiers(self):
        for tier in (TIER_WORD, TIER_LOOSE):
            self.assertEqual(grade_for_tier(tier), "lead")

    def test_tier_labels_are_human_readable(self):
        self.assertEqual(tier_label(TIER_LOOSE), "harf değişimi")
        self.assertEqual(tier_label(TIER_INITIAL), "baş harf")

    def test_invalid_tier_is_conservative(self):
        self.assertEqual(grade_for_tier(None), "lead")


class TestRelatednessGate(unittest.TestCase):
    """AI, isme biçimsel olarak bağlı olmayan doğrulanmış sayfaları eşleştirmemeli."""

    def setUp(self):
        self.engine = AIEngine(model_path=_tmp_model())

    def _cand(self, target, handle, tier):
        return {
            "target": target, "platform": "Twitter / X",
            "url": f"https://x.com/{handle}", "handle": handle,
            "platform_confirm": 0.6, "page_has_handle": True,
            "source_count": 2, "is_profile_url": True, "variant_tier": tier,
        }

    def test_exact_handle_accepted(self):
        label, _p, feats = self.engine.classify(self._cand("abugat", "abugat", 0))
        self.assertEqual(label, 1)
        self.assertTrue(self.engine.is_related(feats))

    def test_vowel_dropped_loose_handle_rejected(self):
        # "abugat" -> "bgt": doğrulanmış sayfa olsa da isim bağı zayıf
        label, _p, feats = self.engine.classify(self._cand("abugat", "bgt", 4))
        self.assertEqual(label, 0)
        self.assertFalse(self.engine.is_related(feats))

    def test_unrelated_existing_profile_rejected(self):
        """Var olan ama isimle alakasız bir profil eşleşme sayılmamalı."""
        label, _p, feats = self.engine.classify(self._cand("John Smith", "torvalds", 3))
        self.assertEqual(label, 0)

    def test_related_truncation_accepted(self):
        # "bugat" = "abugat"in ilk harfi düşmüş hâli; benzerlik yüksek
        # ilgi kapısı açıkça geçmiş ve handle_similarity yüksek (>=0.8);
        # model eğitimsiz bile bu eşleşmeyi kabul etmelidir.
        label, prob, feats = self.engine.classify(self._cand("abugat", "bugat", 4))
        self.assertEqual(label, 1,
                         msg=f"hs={feats['handle_similarity']}, related={self.engine.is_related(feats)}")



class TestMatchGradingInFilter(unittest.TestCase):
    """Gevşek varyasyonlar bulunur ama 'doğrulandı' diye raporlanmaz."""

    def setUp(self):
        self.engine = AIEngine(model_path=_tmp_model())

    def _cand(self, target, handle, tier):
        return {
            "target": target, "platform": "GitHub",
            "url": f"https://github.com/{handle}", "handle": handle,
            "platform_confirm": 1.0, "page_has_handle": True,
            "source_count": 2, "is_profile_url": True, "variant_tier": tier,
        }

    def test_strong_variant_is_verified(self):
        verified, tentative, _logs = HeuristicFilter(engine=self.engine).verify_candidates(
            "Aser Bugat", [self._cand("Aser Bugat", "abugat", TIER_INITIAL)])
        self.assertEqual([c["handle"] for c in verified], ["abugat"])
        self.assertEqual(tentative, [])
        self.assertEqual(verified[0]["match_grade"], "strong")

    def test_loose_variant_becomes_lead_not_verified(self):
        verified, tentative, _logs = HeuristicFilter(engine=self.engine).verify_candidates(
            "abugat", [self._cand("abugat", "bugat", TIER_LOOSE)])
        self.assertEqual(verified, [], "gevşek biçim doğrulanmış sayılmamalı")
        self.assertEqual([c["handle"] for c in tentative], ["bugat"])
        self.assertEqual(tentative[0]["match_grade"], "lead")
        self.assertIn("gevşek biçim", tentative[0]["lead_reason"])

    def test_unrelated_candidate_is_dropped_entirely(self):
        verified, tentative, _logs = HeuristicFilter(engine=self.engine).verify_candidates(
            "John Smith", [self._cand("John Smith", "torvalds", TIER_LOOSE)])
        self.assertEqual(verified, [])
        self.assertEqual(tentative, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
