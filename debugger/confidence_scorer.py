"""
ConfidenceScorer - Rapor geneli güven skorunu hesaplar.

Eski sürümdeki hata: `len(verified_handles) * 30` nedeniyle 4 sonuçta bile
skor tavana (100) vuruyordu; skor anlamsızdı.

Bu sürüm, doğrulanmış adayların AI olasılıklarından türetilen ağırlıklı bir
skor üretir: en güçlü kanıt + ortalama kanıt gücü + kapsam (kaç platform).
Hiç doğrulanmış sonuç yoksa skor 0'dır.
"""


class ConfidenceScorer:
    """Veri doğruluk ve güvenilirlik matrisini AI olasılıklarından hesaplar."""

    def __init__(self, verified_candidates: list, snippets_count: int = 0):
        self.verified_candidates = verified_candidates or []
        self.snippets_count = snippets_count

    def calculate_score(self) -> float:
        if not self.verified_candidates:
            return 0.0

        probs = [float(c.get("ai_prob", 0.0)) for c in self.verified_candidates]
        best = max(probs)
        mean = sum(probs) / len(probs)

        # Platformlar arası kapsam (en fazla 3 farklı platform tam katkı).
        platforms = {c.get("platform") for c in self.verified_candidates}
        coverage = min(len(platforms) / 3.0, 1.0)

        # Metin/özet kanıtı: en fazla 0.1 katkı.
        snippet_bonus = min(self.snippets_count * 0.01, 0.10)

        score = (best * 0.55 + mean * 0.30 + coverage * 0.15 + snippet_bonus) * 100.0
        return round(min(score, 100.0), 1)
