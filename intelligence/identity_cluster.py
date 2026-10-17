"""
Çapraz platform kimlik kümeleme (identity clustering).

NEDEN?
------
Bir OSINT taramasında aynı kullanıcı adı birden çok platformda çıkabilir
(ör. `hamigumi` -> YouTube + TikTok + Instagram). Bunlar büyük olasılıkla
AYNI kişiye aittir ve kullanıcıya tek tek adres listesi yerine tek bir
"kimlik kümesi" olarak sunulmalıdır.

NASIL?
------
Her platformun kanıtı bağımsız bir gözlem sayılır ve olasılıklar
log-odds uzayında toplanır:

    z_toplam = logit(p_en_güçlü) + SÖNÜMLEME x Σ logit(p_diğerleri)
    p_küme   = sigmoid(z_toplam)

İlk (en güçlü) kanıt tam, EK platformların kanıtı yarıya indirilerek sayılır.
SÖNÜMLEME ŞARTTIR: aynı kullanıcı adı farklı platformlarda görünse de
kanıtlar tam bağımsız değildir (isim benzerliği her platformda tekrar eder).
Sönümlemesiz toplam, "üç platformda var" bilgisini hızla %100'e taşıyıp
yanlış güven üretirdi; ayrıca sonuç yine üst sınırla kapatılır.

DÜRÜSTLÜK KURALLARI
-------------------
  * Duvar (wall) adayı = BİLİNMEYEN kanıt: olasılık birleşimine PUAN KATMAZ,
    yalnızca "bu ad burada da görünüyor" bilgisini taşır. Küme, duvar
    üyelerini ayrı sayar ve raporda bunu açıkça yazar.
  * Tek platformda kalan adlar küme DEĞİLDİR (kimlik iddiası yapılmaz).
  * Benzer ama farklı adlar (`hamigumi` vs `hamigumi_`) yalnızca
    `merge_suggestions()` ile "olası bağlantı (doğrulanmadı)" olarak
    önerilir; otomatik birleştirilmez.
"""

import math


# EK platformların kanıtı bu oranla sayılır (ilk kanıt tam sayılır).
DAMPING = 0.5
# Birleşik olasılığın üst sınırı: "kesin" iddiası yapılmaz.
MAX_COMBINED = 0.99
# Birleşime katılacak en düşük üye olasılığı (neredeyse sıfırlar katkı vermez).
MIN_MEMBER_PROB = 0.05


def _logit(p: float) -> float:
    p = min(max(float(p or 0.0), 1e-4), 1.0 - 1e-4)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _slug(handle: str) -> str:
    """Kullanıcı adını karşılaştırılabilir biçime indirger."""
    return "".join(ch for ch in (handle or "").lower() if ch.isalnum())


class IdentityCluster:
    """Aynı kullanıcı adını taşıyan adayların oluşturduğu kimlik kümesi."""

    def __init__(self, handle: str):
        self.slug = _slug(handle)
        self.handle = handle or ""
        self.members = []

    # -- Ekleme -------------------------------------------------------------

    def add(self, candidate: dict, probability: float):
        self.members.append({"candidate": candidate, "probability": float(probability)})
        return self

    # -- Görünüm ------------------------------------------------------------

    @property
    def tier(self) -> int:
        """Kümedeki en sıkı varyasyon biçimi (0 = isimden birebir türetilmiş)."""
        tiers = [int(m["candidate"].get("variant_tier", 0) or 0)
                 for m in self.members]
        return min(tiers) if tiers else 0

    @property
    def is_loose(self) -> bool:
        """Küme yalnızca gevşek biçimlerden mi oluşuyor?

        Gevşek biçimler (harf sırası değişimi, ünlü düşmesi, kısaltma) BAŞKA
        bir kişiye ait olabilir; bu yüzden böyle bir küme kimlik iddiası
        değildir ve raporda açıkça uyarılır.
        """
        return self.tier > 0

    @property
    def platforms(self) -> list:
        seen = []
        for m in self.members:
            platform = (m["candidate"].get("platform") or "?")
            if platform not in seen:
                seen.append(platform)
        return seen

    @property
    def cross_platform(self) -> bool:
        return len(self.platforms) >= 2

    @property
    def verified_count(self) -> int:
        return sum(1 for m in self.members if not m["candidate"].get("wall"))

    @property
    def wall_count(self) -> int:
        return sum(1 for m in self.members if m["candidate"].get("wall"))

    @property
    def display_names(self) -> list:
        names = []
        for m in self.members:
            name = (m["candidate"].get("display_name") or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    @property
    def combined_probability(self):
        """Sönümlemeli log-odds birleşimi; ölçülebilir kanıt yoksa None.

        En güçlü üye tam, diğerleri DAMPING oranıyla sayılır. Böylece tek
        platformun kanıtı şişirilmez, ikinci/üçüncü platform makul ölçüde
        güven ekler. Duvar üyeleri bilinmeyendir: puan katmaz.
        """
        measurable = sorted(
            [m for m in self.members
             if not m["candidate"].get("wall") and m["probability"] >= MIN_MEMBER_PROB],
            key=lambda m: -m["probability"])
        if not measurable:
            return None
        z = _logit(measurable[0]["probability"])
        z += DAMPING * sum(_logit(m["probability"]) for m in measurable[1:])
        return round(min(_sigmoid(z), MAX_COMBINED), 4)

    def to_dict(self) -> dict:
        probability = self.combined_probability
        return {
            "handle": self.handle,
            "slug": self.slug,
            "platforms": self.platforms,
            "cross_platform": self.cross_platform,
            "members": len(self.members),
            "verified": self.verified_count,
            "wall": self.wall_count,
            "tier": self.tier,
            "loose": self.is_loose,
            "combined_probability": probability,
            "display_names": self.display_names,
            "urls": [m["candidate"].get("url", "") for m in self.members],
        }

    def describe(self) -> str:
        probability = self.combined_probability
        text = (f"\"{self.handle}\" — {len(self.platforms)} platform, "
                f"{len(self.members)} adres, {self.verified_count} doğrulanmış")
        if probability is not None:
            text += f", birleşik olasılık %{probability*100:.1f}"
        if self.wall_count:
            text += f", {self.wall_count} adres duvar nedeniyle doğrulanamadı"
        if self.is_loose:
            text += " [gevşek biçim — kimlik iddiası değil]"
        return text


class IdentityClusterer:
    """Adayları kullanıcı adına göre kimlik kümelerine ayırır."""

    def __init__(self, candidates: list = None, min_platforms: int = 2):
        self.candidates = list(candidates or [])
        self.min_platforms = max(1, int(min_platforms))

    # -- Ana çıktı ----------------------------------------------------------

    def build(self) -> list:
        """Çapraz platform kümelerini döndürür (en güçlü kanıt en üstte)."""
        groups = {}
        for cand in self.candidates:
            handle = cand.get("handle") or _handle_of(cand)
            slug = _slug(handle)
            if not slug:
                continue
            prob = float(cand.get("ai_prob", 0.0) or 0.0)
            groups.setdefault(slug, IdentityCluster(handle)).add(cand, prob)

        clusters = [c for c in groups.values() if len(c.platforms) >= self.min_platforms]
        # Sıra: önce isimden BİREBİR türetilmiş adlar (tier 0), sonra doğrulanmış
        # üye sayısı, sonra birleşik olasılık. Gevşek biçimler (harf sırası
        # değişimi vb.) başka bir kişiye ait olabileceği için en sonda listelenir.
        clusters.sort(key=lambda c: (c.tier, -c.verified_count,
                                     -(c.combined_probability or 0.0)))
        return clusters

    def merge_suggestions(self, clusters: list = None, threshold: float = 0.9) -> list:
        """Benzer ama FARKLI adları 'olası bağlantı' olarak önerir.

        `hamigumi` ile `hamigumi_` aynı kişi olabilir; ama bu bir KANIT
        değildir. Bu yüzden otomatik birleştirilmez, yalnızca öneri olarak ve
        "doğrulanmadı" notuyla raporlanır.
        """
        clusters = clusters if clusters is not None else self.build()
        suggestions = []
        for i, a in enumerate(clusters):
            for b in clusters[i + 1:]:
                similarity = _similarity(a.slug, b.slug)
                if threshold <= similarity < 1.0:
                    suggestions.append({
                        "a": a.handle, "b": b.handle,
                        "similarity": round(similarity, 3),
                        "platforms": sorted(set(a.platforms) | set(b.platforms)),
                        "note": "olası bağlantı — doğrulanmadı, elle kontrol edin",
                    })
        suggestions.sort(key=lambda s: s["similarity"], reverse=True)
        return suggestions


def _handle_of(candidate: dict) -> str:
    url = (candidate.get("url") or "").rstrip("/")
    return url.split("/")[-1].lstrip("@") if url else ""


def _similarity(a: str, b: str) -> float:
    """İki slug arası Jaro-Winkler benzerliği (motor zaten bunu sağlıyor)."""
    from intelligence.ai_engine import jaro_winkler
    return jaro_winkler(a, b)
