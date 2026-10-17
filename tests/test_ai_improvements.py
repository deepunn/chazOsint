"""AI çekirdeği iyileştirmeleri için regresyon testleri.

Korunan davranışlar:

  * Metrikler, eğitimde GÖRÜLMEYEN sınıf dengeli holdout kümesinden gelir
    (aynı veriyle eğitip ölçmek metrikleri şişirir).
  * Sıcaklık kalibrasyonu log-loss'u KÖTÜLEŞTİRMEZ ve uç olasılıkları
    %50'ye doğru yumuşatır.
  * Çevrimiçi geri bildirim güncellemeleri geri alınabilir ve ağırlıklar
    sınırlıdır (tek yanlış geri bildirim modeli bozamaz).
  * Geri bildirim değişmediyse açılışta yeniden eğitim YAPILMAZ.
  * Aynı kullanıcı adı birden çok platformda görülürse kimlik kümesi oluşur;
    duvar (wall) üyeleri kanıt puanı KATMAZ.

Çalıştırma:
    python -m unittest tests.test_ai_improvements -v
"""

import json
import os
import tempfile
import unittest
from collections import Counter

from intelligence.ai_engine import (AIEngine, DEFAULT_TEMPERATURE,
                                    MAX_ABS_WEIGHT, MODEL_VERSION)
from intelligence.identity_cluster import (MAX_COMBINED, IdentityClusterer)
from intelligence.synthesizer import IntelligenceSynthesizer


def _tmp_path(suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    os.remove(path)
    return path


def _pair(candidate):
    """Bir örneği (özellikler, etiket) bağımsız anahtara çevirir."""
    feats, label = candidate
    return (json.dumps(feats, sort_keys=True), int(label))


class TestHonestEvaluation(unittest.TestCase):
    def setUp(self):
        self.engine = AIEngine(model_path=_tmp_path(".json"))

    def test_metrics_come_from_holdout_not_training_set(self):
        metrics = self.engine.metrics
        self.assertEqual(metrics.get("split"), "holdout")
        self.assertGreater(metrics.get("holdout_size", 0), 0)
        self.assertGreater(metrics["count"], 0)
        self.assertIn("log_loss", metrics)
        self.assertIn("brier", metrics)
        # Eğitim kümesi metrikleri AYRI tutulur (ezberleme farkı görülebilsin).
        self.assertGreater(self.engine.train_metrics.get("count", 0),
                           metrics["count"])

    def test_split_is_disjoint_and_stratified(self):
        samples = self.engine._synthetic_samples()
        train, holdout = self.engine._split(samples)
        self.assertTrue(train)
        self.assertTrue(holdout)
        # Hiçbir örnek iki tarafta birden olmamalı (çoklu küme eşitliği).
        self.assertEqual(Counter(map(_pair, samples)),
                         Counter(map(_pair, train)) + Counter(map(_pair, holdout)))
        # Her iki sınıf holdout'ta temsil edilmeli.
        self.assertEqual({label for _f, label in holdout}, {0, 1})

    def test_calibration_never_worsens_log_loss(self):
        calibration = self.engine.calibration
        self.assertIn("temperature", calibration)
        self.assertLessEqual(calibration["log_loss_calibrated"],
                             calibration["log_loss_uncalibrated"] + 1e-9)

    def test_temperature_softens_extreme_probabilities(self):
        feats = self.engine.extract_features({
            "target": "john smith", "platform": "GitHub",
            "url": "https://github.com/johnsmith", "handle": "johnsmith",
            "platform_confirm": 1.0, "page_has_handle": True,
            "source_count": 2, "is_profile_url": True,
            "display_name": "John Smith",
        })
        self.engine.temperature = 1.0
        sharp = self.engine.predict_proba(feats)
        self.engine.temperature = 2.5
        soft = self.engine.predict_proba(feats)
        if sharp > 0.5:
            self.assertLess(soft, sharp)
        else:
            self.assertGreater(soft, sharp)


class TestOnlineUpdateSafety(unittest.TestCase):
    def setUp(self):
        self.engine = AIEngine(model_path=_tmp_path(".json"))
        self.feats = self.engine.extract_features({
            "target": "jane doe", "platform": "GitHub",
            "url": "https://github.com/janedoe", "handle": "janedoe",
            "platform_confirm": 0.3, "page_has_handle": False,
            "source_count": 1, "is_profile_url": True,
        })

    def test_revert_restores_baseline(self):
        before = self.engine.predict_proba(self.feats)
        for _ in range(20):
            self.engine.online_update(self.feats, 1, lr=0.4)
        self.assertNotEqual(self.engine.predict_proba(self.feats), before)
        self.assertTrue(self.engine.revert_online_updates())
        self.assertAlmostEqual(self.engine.predict_proba(self.feats), before, places=9)
        self.assertEqual(self.engine.online_updates, 0)
        self.assertFalse(self.engine.revert_online_updates())

    def test_weights_are_clamped(self):
        for _ in range(300):
            self.engine.online_update(self.feats, 1, lr=0.9)
        for name, weight in self.engine.weights.items():
            self.assertLessEqual(abs(weight), MAX_ABS_WEIGHT, msg=name)
        self.assertLess(abs(self.engine.bias), 13.0)


class TestRetrainGuard(unittest.TestCase):
    def _feedback_file(self, count=6):
        path = _tmp_path(".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for _i in range(count):
                f.write(json.dumps({
                    "url": "https://github.com/ok", "platform": "GitHub",
                    "handle": "ok", "label": 1,
                    "features": {
                        "handle_similarity": 0.95, "slug_match": 0.5,
                        "display_name_similarity": 1.0, "token_coverage": 0.5,
                        "handle_shape": 0.9, "platform_confirm": 0.5,
                        "page_has_handle": 1.0, "source_count": 0.33,
                        "domain_trust": 1.0, "is_profile_url": 1.0,
                    },
                }, ensure_ascii=False) + "\n")
        return path

    def test_no_retrain_when_feedback_is_unchanged(self):
        model_path = _tmp_path(".json")
        feedback = self._feedback_file()
        try:
            first = AIEngine(model_path=model_path, feedback_path=feedback)
            self.assertEqual(first.retrain_runs, 1)   # model yoktu -> eğitildi
            self.assertEqual(first.retrained_feedback_count, 6)

            second = AIEngine(model_path=model_path, feedback_path=feedback)
            self.assertEqual(second.retrain_runs, 0)  # YENİ geri bildirim yok
            self.assertGreater(second.trained_samples, 0)
            self.assertEqual(second.model_version, MODEL_VERSION)
        finally:
            for path in (model_path, feedback):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def test_new_feedback_triggers_retrain(self):
        model_path = _tmp_path(".json")
        feedback = self._feedback_file(6)
        try:
            AIEngine(model_path=model_path, feedback_path=feedback)
            with open(feedback, encoding="utf-8") as source:
                record = json.loads(source.readline())
            record["label"] = 0
            with open(feedback, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            engine = AIEngine(model_path=model_path, feedback_path=feedback)
            self.assertEqual(engine.retrain_runs, 1)
            self.assertEqual(engine.retrained_feedback_count, 7)
        finally:
            for path in (model_path, feedback):
                try:
                    os.remove(path)
                except OSError:
                    pass


class TestModelReporting(unittest.TestCase):
    def setUp(self):
        self.engine = AIEngine(model_path=_tmp_path(".json"))

    def test_calibration_report_shape(self):
        report = self.engine.calibration_report()
        self.assertGreater(report["count"], 0)
        self.assertGreaterEqual(report["brier"], 0.0)
        self.assertLessEqual(report["brier"], 1.0)
        self.assertTrue(report["bins"])
        for row in report["bins"]:
            self.assertIn("range", row)
            self.assertGreater(row["count"], 0)
            self.assertGreaterEqual(row["observed"], 0.0)
            self.assertLessEqual(row["observed"], 1.0)

    def test_feature_weights_sorted_and_labelled(self):
        rows = self.engine.feature_weights()
        self.assertEqual(len(rows), len(self.engine.features))
        weights = [abs(r["weight"]) for r in rows]
        self.assertEqual(weights, sorted(weights, reverse=True))
        self.assertTrue(all(r["label"] for r in rows))

    def test_status_report_mentions_holdout_and_temperature(self):
        text = self.engine.status_report()
        self.assertIn("DOĞRULAMA", text)
        self.assertIn("holdout", text)
        self.assertIn("T=", text)


class TestIdentityClustering(unittest.TestCase):
    @staticmethod
    def _cand(platform, handle, prob, wall=False, display="", url=None):
        return {
            "platform": platform, "handle": handle,
            "url": url or f"https://example.com/{handle}",
            "ai_prob": prob, "wall": wall, "display_name": display,
        }

    def test_same_handle_on_two_platforms_forms_cluster(self):
        clusters = IdentityClusterer([
            self._cand("YouTube", "hamigumi", 0.93, display="Hami Gumi"),
            self._cand("TikTok", "hamigumi", 0.85),
        ]).build()
        self.assertEqual(len(clusters), 1)
        cluster = clusters[0]
        self.assertTrue(cluster.cross_platform)
        self.assertEqual(cluster.platforms, ["YouTube", "TikTok"])
        self.assertEqual(cluster.display_names, ["Hami Gumi"])
        self.assertEqual(cluster.verified_count, 2)

    def test_single_platform_is_not_a_cluster(self):
        clusters = IdentityClusterer([self._cand("GitHub", "hamigumi", 0.9)]).build()
        self.assertEqual(clusters, [])

    def test_wall_members_add_no_evidence(self):
        """Duvar adayı BİLİNMEYEN kanıttır: birleşik olasılığı yükseltmez."""
        clusters = IdentityClusterer([
            self._cand("YouTube", "hamigumi", 0.93),
            self._cand("Instagram", "hamigumi", 0.0, wall=True),
        ]).build()
        self.assertEqual(len(clusters), 1)
        self.assertAlmostEqual(clusters[0].combined_probability, 0.93, places=3)
        self.assertEqual(clusters[0].wall_count, 1)
        self.assertEqual(clusters[0].verified_count, 1)

    def test_combined_probability_beats_strongest_but_is_capped(self):
        clusters = IdentityClusterer([
            self._cand("YouTube", "hamigumi", 0.93),
            self._cand("TikTok", "hamigumi", 0.85),
            self._cand("GitHub", "hamigumi", 0.88),
        ]).build()
        combined = clusters[0].combined_probability
        self.assertGreater(combined, 0.93)
        self.assertLessEqual(combined, MAX_COMBINED)

    def test_all_wall_cluster_has_no_probability(self):
        clusters = IdentityClusterer([
            self._cand("Instagram", "hamigumi", 0.0, wall=True),
            self._cand("LinkedIn", "hamigumi", 0.0, wall=True),
        ]).build()
        self.assertEqual(len(clusters), 1)
        self.assertIsNone(clusters[0].combined_probability)

    def test_loose_clusters_sort_last_and_are_flagged(self):
        """Gevşek biçim kümesi kimlik iddiası değildir: en sonda ve UYARILI."""
        strong = IdentityClusterer([
            self._cand("YouTube", "hamigumi", 0.6),
            self._cand("TikTok", "hamigumi", 0.6),
            {**self._cand("GitHub", "hamig", 0.99), "variant_tier": 2},
            {**self._cand("Twitch", "hamig", 0.99), "variant_tier": 2},
        ]).build()
        self.assertEqual([c.handle for c in strong], ["hamigumi", "hamig"])
        self.assertFalse(strong[0].is_loose)
        self.assertTrue(strong[1].is_loose)
        self.assertEqual(strong[1].tier, 2)

    def test_loose_cluster_warning_in_report(self):
        candidates = [
            {**self._cand("GitHub", "hamig", 0.99), "variant_tier": 2,
             "ai_features": {"handle_similarity": 0.9}},
            {**self._cand("Twitch", "hamig", 0.99), "variant_tier": 2,
             "ai_features": {"handle_similarity": 0.9}},
        ]
        clusters = IdentityClusterer(candidates).build()
        report = IntelligenceSynthesizer(
            "hamigumi", {"snippets": []}, [], [], 0.0, candidates,
            clusters=clusters).generate_report()
        self.assertIn("UYARI: gevşek biçim", report)

    def test_merge_suggestions_are_marked_unverified(self):
        clusterer = IdentityClusterer([
            self._cand("YouTube", "hamigumi", 0.9),
            self._cand("TikTok", "hamigumi", 0.8),
            self._cand("GitHub", "hamigumi1", 0.7),
            self._cand("Twitch", "hamigumi1", 0.6),
            self._cand("SoundCloud", "totally-unrelated", 0.9),
            self._cand("Medium", "totally-unrelated", 0.9),
        ])
        clusters = clusterer.build()
        suggestions = clusterer.merge_suggestions(clusters)
        pairs = {(s["a"], s["b"]) for s in suggestions}
        self.assertIn(("hamigumi", "hamigumi1"), pairs)
        for s in suggestions:
            self.assertIn("doğrulanmadı", s["note"])


class TestReportIntegration(unittest.TestCase):
    def test_cluster_section_is_rendered(self):
        candidates = [
            {"platform": "YouTube", "handle": "hamigumi",
             "url": "https://www.youtube.com/@hamigumi", "ai_prob": 0.93,
             "match_grade": "strong", "variant_tier": 0,
             "ai_features": {"handle_similarity": 1.0}},
            {"platform": "TikTok", "handle": "hamigumi",
             "url": "https://www.tiktok.com/@hamigumi", "ai_prob": 0.9,
             "match_grade": "strong", "variant_tier": 0,
             "ai_features": {"handle_similarity": 1.0}},
        ]
        clusters = IdentityClusterer(candidates).build()
        report = IntelligenceSynthesizer(
            "hamigumi", {"snippets": []}, [candidates[0]], [],
            80.0, [candidates[1]], clusters=clusters).generate_report()
        self.assertIn("ÇAPRAZ PLATFORM KİMLİK KÜMELERİ", report)
        self.assertIn("hamigumi", report)
        self.assertIn("TikTok", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
