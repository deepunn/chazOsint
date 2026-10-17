"""
SessionManager - Tek bir taramanın tüm durumunu taşıyan nesne.

Önceden kullanılmayan bu sınıf artık boru hattının (pipeline) durum
taşıyıcısıdır: ham veri, adaylar, doğrulananlar, günlükler, güven skoru,
rapor, ilişki ağı ve fotoğraf analizi burada toplanır. Arayüz ve CLI yalnızca
bu nesneyi okur.
"""

import time


class SessionManager:
    """Aktif/bitmiş bir tarama oturumunun durumu."""

    def __init__(self, target_name: str):
        self.target_name = target_name
        self.raw_data = {}
        self.snippets = []
        self.candidates = []
        self.verified = []
        self.tentative = []
        self.logs = []
        self.confidence = 0.0
        self.report = ""
        self.network = {}
        # Çapraz platform kimlik kümeleri (aynı kullanıcı adı >1 platformda).
        self.clusters = []
        self.photo = None            # PhotoForensicsAI sonucu
        self.photo_identity = None   # PhotoIdentityAI sonucu
        self.fingerprint_matches = []
        self.started_at = time.time()
        self.finished_at = None

    # -- Yardımcılar --------------------------------------------------------

    def log(self, message: str):
        self.logs.append(message)

    def update_state(self, key: str, value):
        self.raw_data[key] = value

    def finish(self):
        self.finished_at = time.time()

    @property
    def duration(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 2)

    @property
    def verified_count(self) -> int:
        return len(self.verified)

    def get_summary(self) -> dict:
        return {
            "target": self.target_name,
            "candidates": len(self.candidates),
            "verified": len(self.verified),
            "tentative": len(self.tentative),
            "clusters": len(self.clusters),
            "confidence": self.confidence,
            "duration": self.duration,
            "logs_count": len(self.logs),
        }

    def to_dict(self) -> dict:
        """JSON'a yazılabilir özet."""
        return {
            "target": self.target_name,
            "summary": self.get_summary(),
            "verified": [
                {"platform": c.get("platform"), "handle": c.get("handle"),
                 "url": c.get("url"), "archive": c.get("archive"),
                 "ai_probability": c.get("ai_prob"), "reason": c.get("ai_reason")}
                for c in self.verified
            ],
            "tentative": [
                {"platform": c.get("platform"), "url": c.get("url"),
                 "ai_probability": c.get("ai_prob"),
                 # Duvarlı platformlarda (Instagram/TikTok) AI olasılığı
                 # ölçülemez; rapora bu bilgi taşınır.
                 "wall": bool(c.get("wall")),
                 "match_grade": c.get("match_grade"),
                 "lead_reason": c.get("lead_reason")}
                for c in self.tentative
            ],
            "clusters": [c.to_dict() for c in self.clusters
                         if hasattr(c, "to_dict")],
            "snippets": self.snippets[:10],
            "photo": self.photo,
            "photo_identity": self.photo_identity,
            "fingerprint_matches": self.fingerprint_matches,
            "logs": self.logs,
        }
