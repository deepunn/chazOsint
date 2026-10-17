"""
PhotoForensicsAI - Yerel fotoğraf adli-tıp analiz motoru.

Yüklenen bir görselden sinyal çıkarır ve düzenlenmiş/manipüle edilmiş olma
olasılığını yerel bir lojistik modelle puanlar (0..1). Harici servis gerekmez.

Sinyaller:
  * Düzenleme yazılımı izi (Photoshop, Snapseed, PicsArt, Lightroom...)
  * Meta verinin tamamen silinmiş olması (sosyal medya / ekran görüntüsü izi)
  * EXIF var ama kamera bilgisi yok
  * EXIF boyutu ile gerçek boyutun uyuşmaması (yeniden boyutlandırma izi)
  * Kamera / tarih / GPS kanıtının varlığı (orijinallik lehine)
Ayrıca algısal hash (dHash) üretir: aynı görselin tekrar kullanımını tespit için.
"""

import os

from collectors.forensics.exif_analyzer import EXIFAnalyzer

# Yaygın düzenleme / filtre uygulamaları.
EDITING_SOFTWARE = [
    "photoshop", "gimp", "snapseed", "picsart", "lightroom", "pixlr", "canva",
    "vsco", "facetune", "meitu", "afterlight", "adobe", "affinity", "capture one",
    "paint.net", "inkscape", "instagram", "snapchat", "tiktok", "whatsapp",
]

BIAS = -0.5
WEIGHTS = {
    "editing_software": 2.6,   # düzenleme yazılımı izi
    "metadata_stripped": 1.4,  # hiç EXIF yok
    "no_camera_info": 0.8,     # EXIF var ama kamera yok
    "dimension_mismatch": 1.0,  # EXIF boyutu != gerçek boyut
    "has_camera": -1.0,         # kamera bilgisi var
    "has_datetime": -0.4,       # çekim tarihi var
    "gps_present": -0.5,        # GPS var
}


class PhotoForensicsAI:
    """Yerel görsel adli-tıp AI motoru."""

    def __init__(self, image_path: str):
        self.analyzer = EXIFAnalyzer(image_path)
        self.image_path = image_path

    def analyze(self) -> dict:
        meta = self.analyzer.analyze()
        signals = []

        if not meta.get("exists"):
            return {
                "meta": meta,
                "signals": [],
                "risk_score": 0.0,
                "verdict": "Analiz edilemedi",
                "verdict_color": "muted",
                "phash": None,
                "active": [],
            }

        raw_software = (meta.get("software") or "").lower()
        software_hit = next((s for s in EDITING_SOFTWARE if s in raw_software), None)
        if software_hit:
            signals.append(("editing_software", f"Düzenleme yazılımı izi: '{software_hit}'"))

        if not meta.get("has_exif"):
            signals.append(("metadata_stripped", "Meta veri (EXIF) tamamen silinmiş"))
        else:
            if not (meta.get("camera_make") or meta.get("camera_model")):
                signals.append(("no_camera_info", "EXIF var ama kamera bilgisi yok"))
            if meta.get("width") and meta.get("exif_width") and meta["width"] != meta["exif_width"]:
                signals.append(("dimension_mismatch",
                                f"Boyut uyuşmazlığı (gerçek {meta['width']}x{meta['height']}, "
                                f"EXIF {meta['exif_width']}x{meta['exif_height']})"))

        if meta.get("camera_make") or meta.get("camera_model"):
            signals.append(("has_camera", "Kamera bilgisi mevcut"))
        if meta.get("datetime"):
            signals.append(("has_datetime", "Çekim tarihi mevcut"))
        if meta.get("gps"):
            signals.append(("gps_present", "GPS konum verisi mevcut"))

        active = {name for name, _ in signals}
        z = BIAS + sum(WEIGHTS[name] for name in active)
        risk = _sigmoid(z)
        verdict, color = _verdict(risk)

        return {
            "meta": meta,
            "signals": signals,
            "active": sorted(active),
            "risk_score": round(risk, 4),
            "verdict": verdict,
            "verdict_color": color,
            "phash": self._dhash(),
        }

    def _dhash(self, hash_size: int = 8):
        """Algısal fark-hash (dHash) -> 16 haneli hex. Aynı görseli tanımak için."""
        try:
            from PIL import Image
            resample = getattr(Image, "Resampling", Image).LANCZOS
            with Image.open(self.image_path) as img:
                small = img.convert("L").resize((hash_size + 1, hash_size), resample)
                pixels = list(small.tobytes())  # 'L' modunda her bayt bir piksel
            bits = []
            for row in range(hash_size):
                for col in range(hash_size):
                    left = pixels[row * (hash_size + 1) + col]
                    right = pixels[row * (hash_size + 1) + col + 1]
                    bits.append("1" if left > right else "0")
            return f"{int(''.join(bits), 2):016x}"
        except Exception:
            return None


# --- Yardımcılar ------------------------------------------------------------

def _sigmoid(z: float) -> float:
    import math
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _verdict(risk: float):
    if risk >= 0.65:
        return "Yüksek risk - düzenleme olasılığı yüksek", "danger"
    if risk >= 0.30:
        return "Orta risk - bazı işaretler var", "warning"
    return "Düşük risk - orijinal görünümlü", "success"
