"""
CHAZ AI Engine - Yerel (self-contained) yapay zeka çekirdeği.

Bu modül, OSINT boru hattının en kritik parçasıdır: bir "aday profil"in
gerçekten hedef kişiye ait olup olmadığını OTOMATİK olarak karara bağlar.

Harici API, bulut veya harici ML kütüphanesi KULLANMAZ. Saf Python:

  * Özellik çıkarımı (feature engineering): kullanıcı adı benzerliği, profil
    sayfasındaki GÖRÜNEN AD benzerliği, isim parçalarının kapsanması,
    handle şekli (rastgele rakam/generic kelime cezası), platform doğrulaması,
    kaynak sayısı, alan adı güveni...
  * Lojistik regresyon sınıflandırıcısı (gradyan inişi + L2).
  * DÜRÜST DEĞERLENDİRME: metrikler, eğitimde GÖRÜLMEYEN sınıf dengeli
    (stratified) bir doğrulama kümesinden gelir. Aynı veriyle hem eğitip hem
    ölçmek metrikleri şişirir; bu yüzden `metrics` holdout'tur.
  * OLASILIK KALİBRASYONU (Platt/sıcaklık ölçekleme): küçük veriyle eğitilen
    lojistik regresyon aşırı özgüvenlidir (%0 / %100). Sıcaklık, doğrulama
    kümesinde log-loss'u en aza indirecek şekilde seçilir ve Brier skoru +
    güvenilirlik tablosu (kalibrasyon raporu) olarak raporlanır.
  * Eşik KALİBRASYONU: karar eşiği, doğrulama kümesinde F1'i en yükseğe
    çıkaran değerden seçilir.
  * Değerlendirme metrikleri (accuracy / precision / recall / F1 / log-loss /
    Brier).
  * Geri bildirim güvenliği: çevrimiçi güncellemeler anlık görüntü alınarak
    yapılır (`revert_online_updates`) ve ağırlıklar sınırlanır (yanlış bir
    geri bildirim modeli bozamaz). Geri bildirim değişmediyse yeniden eğitim
    ATLANIR (her açılışta boşuna eğitim yapılmaz).
  * Kalıcılık: öğrenilen ağırlıklar config/ai_model.json'a yazılır.
  * Çevrimiçi öğrenme: kullanıcı geri bildirimi (doğru/yanlış) ile model
    anında güncellenir ve yeniden kaydedilir.
  * GERİ BİLDİRİMDEN YENİDEN EĞİTİM: arayüzde biriken
    database/ai_feedback.jsonl kayıtları başlangıçta parti hâlinde modele
    katılır; model kullanıldıkça gerçek verilerle güçlenir.

En kritik yenilik `display_name_similarity`: kullanıcı adı tutmasa bile profil
sayfasındaki görünen ad hedefi doğruluyorsa eşleşme gerçek sayılır; görünen ad
başka birini gösteriyorsa (ör. "abugat" arayıp başka bir "bugat" bulmak) aday
doğrulanmış sayılmaz.
"""

import json
import math
import os
import random
import re
import unicodedata

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

# Modelin kullandığı özellik vektörünün sırası (kanonik sıra).
FEATURE_NAMES = [
    "handle_similarity",        # hedef isim ile bulunan kullanıcı adı arasındaki benzerlik
    "slug_match",               # kullanıcı adı doğrudan isimden türetilmiş mi?
    "display_name_similarity",  # PROFİL SAYFASINDAKİ görünen ad hedefle uyuşuyor mu?
    "token_coverage",           # hedef ismin parçaları handle içinde geçiyor mu?
    "handle_shape",             # handle "kişi adı" gibi mi (rakam/generic cezası)?
    "platform_confirm",         # platform sayfasında içerik doğrulaması (0..1)
    "page_has_handle",          # profil sayfasının gövdesinde handle geçiyor mu?
    "source_count",             # kaç bağımsız kaynak bu sonucu bildirdi (0..1'e normalize)
    "domain_trust",             # bilinen/güvenilir bir platform alan adı mı?
    "is_profile_url",           # URL bir profil mi (gönderi/reklam değil)?
    "display_name_present",     # sayfada okunabilen bir görünen ad var mı (kanıt var mı)?
    "wall",                     # platform içeriği anonim istemciye gizliyor mu (IG/TikTok)?
    "single_token_target",      # hedef tek kelime mi (kullanıcı adı sorgusu) yoksa tam ad mı?
    "display_name_conflict",    # sayfa bir GÖRÜNEN AD gösteriyor ama hedefle uyuşmuyor
    "bio_similarity",           # profil açıklaması/biyografisinde hedef ismi geçiyor mu?
]

# Modelin şema sürümü: özellik listesi ya da karar mantığı değişirse eski
# ağırlıklar geçersizdir -> otomatik yeniden eğitim.
MODEL_VERSION = 5

# Olasılık KALİBRASYONU: logit, sıcaklık (T) ile bölünür. T=1 kalibrasyon yok.
# T>1 olasılıkları %50'ye çeker (aşırı özgüveni kırar), T<1 keskinleştirir.
DEFAULT_TEMPERATURE = 1.0
TEMPERATURE_RANGE = (0.6, 3.0)

# Çevrimiçi öğrenmede ağırlık sınırı: tek bir yanlış geri bildirim modeli
# bozamaz (ağırlık patlaması engellenir).
MAX_ABS_WEIGHT = 6.0

# Özelliklerin insan-okur adları (arayüzdeki karar panelinde gösterilir).
FEATURE_LABELS = {
    "handle_similarity": "Kullanıcı adı benzerliği",
    "slug_match": "İsimden türetilmiş kullanıcı adı",
    "display_name_similarity": "Profil görünen adı eşleşmesi",
    "token_coverage": "İsim parçalarının kapsanması",
    "handle_shape": "Kullanıcı adı şekli (kişi adı gibi)",
    "platform_confirm": "Platform içerik doğrulaması",
    "page_has_handle": "Sayfada handle kanıtı",
    "source_count": "Bağımsız kaynak sayısı",
    "domain_trust": "Güvenilir platform alan adı",
    "is_profile_url": "Profil sayfası (gönderi değil)",
    "display_name_present": "Sayfada görünen ad okunabildi",
    "wall": "Platform içeriği gizliyor (duvar)",
    "single_token_target": "Hedef tek kelime (kullanıcı adı sorgusu)",
    "display_name_conflict": "Sayfadaki ad hedefle çelişiyor",
    "bio_similarity": "Profil açıklamasında (bio) hedef ismi",
}

# Karar eşiği: bu olasılığın altındaki adaylar "yanlış eşleşme" olarak elenir.
DECISION_THRESHOLD = 0.55

# Kalibrasyonun izin verdiği aralık (aşırı uçlara kaymayı engeller).
THRESHOLD_MIN = 0.45
THRESHOLD_MAX = 0.70

# İLGİ KAPISI: Bir aday, biçim olarak isme bağlı değilse (gevşek varyasyon),
# sayfa doğrulanmış olsa bile eşleşme sayılmaz. Tier -> gereken min. benzerlik.
RELATEDNESS_REQUIRED = {0: 0.55, 1: 0.60, 2: 0.65, 3: 0.80, 4: 0.85}
RELATEDNESS_DEFAULT = 0.80

# Kanıt kuralı eşikleri (classify içinde kullanılır; tek yerde toplandı).
DISPLAY_NAME_STRONG = 0.90   # sayfadaki görünen ad hedefe neredeyse birebir
HANDLE_STRONG = 0.80         # kullanıcı adı hedef isme çok yakın
HANDLE_PARTIAL = 0.60        # görünen ad kanıtının sayılması için gereken alt sınır
PLATFORM_CONFIRM_MIN = 0.50  # platform içerik doğrulaması (sayfa gerçekten profil)

# Kanıt kuralı tetiklendiğinde sisteme yazılan GÜVEN (ensemble).
# Kural deterministik bir doğrulamadır; lojistik model ise kanıtın zayıf
# olduğu "gri bölge" için öğrenilmiştir. Nihai olasılık ikisinin birleşimidir.
EVIDENCE_CONFIDENCE = {
    "display_name": 0.93,    # sayfadaki görünen ad hedefle uyuşuyor
    "page_handle": 0.90,     # sayfa başlığı/meta hedef handle'ını doğruladı
    "platform_confirm": 0.85,  # platform "bu sayfa var" diyor + handle çok yakın
    "bio": 0.88,             # profil açıklaması/biyografisi hedefin adını içeriyor
}

# Bilinen platform alan adları (alan adı güveni için).
TRUSTED_DOMAINS = {
    "instagram.com": "Instagram",
    "twitter.com": "Twitter / X",
    "x.com": "Twitter / X",
    "spotify.com": "Spotify",
    "youtube.com": "YouTube",
    "soundcloud.com": "SoundCloud",
    "github.com": "GitHub",
    "tiktok.com": "TikTok",
    "linkedin.com": "LinkedIn",
    "facebook.com": "Facebook",
    "twitch.tv": "Twitch",
    "reddit.com": "Reddit",
    "medium.com": "Medium",
    "behance.net": "Behance",
    "pinterest.com": "Pinterest",
    "threads.net": "Threads",
    "threads.com": "Threads",
    "t.me": "Telegram",
    "telegram.me": "Telegram",
    "steamcommunity.com": "Steam",
    "gitlab.com": "GitLab",
    "dribbble.com": "Dribbble",
    "tumblr.com": "Tumblr",
    "vk.com": "VK",
    "mastodon.social": "Mastodon",
}

# Kişi hesabı olma olasılığını düşüren, jenerik/topluluk kullanıcı adı kelimeleri.
GENERIC_HANDLE_TOKENS = (
    "official", "thereal", "real", "user", "account", "admin", "test",
    "fake", "bot", "daily", "news", "team", "support", "store", "shop",
)

# Türkçe ve diğer aksanlı karakterlerin ASCII karşılıkları.
_TR_MAP = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i",
    "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
    "â": "a", "î": "i", "û": "u", "é": "e", "è": "e", "ê": "e",
    "á": "a", "à": "a", "ä": "a", "ñ": "n", "å": "a", "ø": "o",
})

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODEL_PATH = os.path.join(_ROOT, "config", "ai_model.json")
DEFAULT_FEEDBACK_PATH = os.path.join(_ROOT, "database", "ai_feedback.jsonl")

# Başlangıçta geri bildirimden yeniden eğitim için gereken en az kayıt sayısı.
MIN_FEEDBACK_FOR_RETRAIN = 5


# ---------------------------------------------------------------------------
# Metin normalizasyonu ve benzerlik fonksiyonları (saf Python)
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Metni küçük harfli, aksansız, sadece alfanumerik bir 'slug'a çevirir."""
    if not text:
        return ""
    text = text.translate(_TR_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in text.lower() if ch.isalnum())


def name_tokens(text: str) -> list:
    """İsmi ASCII slug parçalarına ayırır ('Aser Bugat' -> ['aser', 'bugat'])."""
    raw = re.split(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü]+", str(text or ""))
    return [t for t in (slugify(part) for part in raw) if len(t) >= 2]


def name_variants(name: str) -> set:
    """Bir isimden olası kullanıcı adı varyasyonlarını üretir.

    "John Smith" -> {johnsmith, john_smith, john.smith, jsmith, john, smith}
    """
    variants = set()
    slug = slugify(name)
    if slug:
        variants.add(slug)

    parts = [slugify(p) for p in str(name).replace("_", " ").split()]
    parts = [p for p in parts if p]
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        variants.update({
            first + last,
            first + "-" + last,
            first + "." + last,
            first + "_" + last,
            first[0] + last,
        })
    for p in parts:
        if len(p) >= 3:
            variants.add(p)
    return {v for v in variants if v}


def partial_name_match(target: str, other: str, fuzzy_threshold: float = 0.9) -> float:
    """Hedef ismin parçalarının `other` metninde geçme ORANI (0..1).

    "Aser Bugat" vs "Aser Bugat (@ABugat)" -> 1.0
    "Aser Bugat" vs "Bugat Bilisim"        -> 0.5
    "Aser Bugat" vs "Ahmet Yilmaz"         -> 0.0
    """
    target_tokens = name_tokens(target)
    if not target_tokens:
        return 0.0
    other_slug = slugify(other)
    if not other_slug:
        return 0.0
    other_tokens = name_tokens(other)
    hits = 0
    for token in target_tokens:
        if token in other_slug:
            hits += 1
        elif any(jaro_winkler(token, candidate) >= fuzzy_threshold
                 for candidate in other_tokens):
            hits += 1
    return round(hits / len(target_tokens), 4)


def jaro(s1: str, s2: str) -> float:
    """Jaro benzerlik katsayısı (0..1)."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_distance = max(len1, len2) // 2 - 1
    if match_distance < 0:
        match_distance = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0

    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    transpositions = 0
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    transpositions //= 2

    return (matches / len1 + matches / len2 + (matches - transpositions) / matches) / 3.0


def jaro_winkler(s1: str, s2: str, prefix_weight: float = 0.1) -> float:
    """Jaro-Winkler benzerliği; ortak öneki ödüllendirir (0..1)."""
    j = jaro(s1, s2)
    if j <= 0.0:
        return 0.0
    prefix = 0
    for a, b in zip(s1, s2):
        if a != b or prefix == 4:
            break
        prefix += 1
    return min(1.0, j + prefix * prefix_weight * (1.0 - j))


def handle_shape_score(handle: str) -> float:
    """Kullanıcı adının 'gerçek kişi hesabı' gibi görünme puanı (0..1).

    Rastgele rakam yığınları ("abugat4412"), jenerik kelimeler ("abugat_official")
    ve çok kısa adlar puanı düşürür; bunlar tesadüfi eşleşme olasılığını artırır.
    """
    if not handle:
        return 0.0
    low = handle.lower()
    score = 1.0

    digits = sum(1 for ch in low if ch.isdigit())
    if digits:
        score -= min(0.6, 0.18 * digits)

    if any(word in low for word in GENERIC_HANDLE_TOKENS):
        score -= 0.45

    if len(low) <= 3:
        score -= 0.25
    if re.fullmatch(r"[0-9]+", low):
        score = 0.0
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# AI Engine
# ---------------------------------------------------------------------------

class AIEngine:
    """Kendi kendine öğrenen yerel OSINT doğrulama motoru.

    Kullanım:
        engine = AIEngine()
        label, prob, feats = engine.classify(candidate)
        if label == 1:
            ...  # güvenilir eşleşme
    """

    def __init__(self, model_path: str = None, autoload: bool = True,
                 feedback_path: str = None, use_feedback: bool = True):
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.feedback_path = feedback_path or DEFAULT_FEEDBACK_PATH
        self.features = list(FEATURE_NAMES)
        self.bias = 0.0
        self.weights = {f: 0.0 for f in self.features}
        self.feedback_count = 0
        self.trained_samples = 0
        self.real_feedback_samples = 0
        self.metrics = {}            # holdout (dürüst) metrikleri
        self.train_metrics = {}      # eğitim kümesi metrikleri (karşılaştırma)
        self.calibration = {}        # log-loss / sıcaklık kaydı
        # Sınıf dengesizliğini telafi etmek için varsayılan karar eşiği.
        self.threshold = DECISION_THRESHOLD
        self.temperature = DEFAULT_TEMPERATURE
        self.online_updates = 0      # kaç çevrimiçi güncelleme uygulandı
        self.retrained_feedback_count = 0   # son yeniden eğitimdeki geri bildirim sayısı
        self.retrain_runs = 0        # bu süreçte kaç kez yeniden eğitildi
        self._baseline = None        # çevrimiçi güncelleme öncesi anlık görüntü

        loaded = autoload and self.load()
        if not loaded:
            self._bootstrap_train()
        if use_feedback:
            # Yalnızca YENİ geri bildirim varsa yeniden eğit: her açılışta
            # aynı veriyle yeniden eğitim yapmak zaman kaybıdır ve modelin
            # kullanıcı geri bildirimiyle kademeli iyileşmesini gizler.
            pending = self.load_feedback_samples()
            if len(pending) > self.retrained_feedback_count:
                self.retrain_from_feedback(path=self.feedback_path)

    @property
    def model_version(self) -> int:
        """Model şema sürümü (arayüz/CLI raporunda gösterilir)."""
        return MODEL_VERSION

    # -- Özellik çıkarımı ---------------------------------------------------

    def extract_features(self, candidate: dict) -> dict:
        """Bir aday profili model için özellik vektörüne çevirir."""
        target = candidate.get("target", "") or ""
        handle = candidate.get("handle", "") or ""
        url = candidate.get("url", "") or ""
        platform = candidate.get("platform", "") or ""
        display_name = candidate.get("display_name", "") or ""
        description = candidate.get("description", "") or ""

        handle_slug = slugify(handle) or _handle_from_url(url)
        variants = name_variants(target)

        # 1) İsim benzerliği: handle ile en yakın isim varyasyonu.
        similarity = 0.0
        for v in variants:
            similarity = max(similarity, jaro_winkler(v, handle_slug))
        # Tam içerme durumunda benzerliği yükselt.
        if handle_slug and any(v and (v in handle_slug or handle_slug in v) for v in variants):
            similarity = max(similarity, 0.85)

        # 2) Doğrudan slug eşleşmesi mi?
        slug_match = 0.0
        if handle_slug:
            if handle_slug in variants:
                slug_match = 1.0
            elif any(v and (handle_slug == v or handle_slug.startswith(v) or v.startswith(handle_slug))
                     for v in variants):
                slug_match = 0.5

        # 3) PROFİL SAYFASINDAKİ GÖRÜNEN AD (en güçlü yeni kanıt).
        #    Kullanıcı adı tutmasa bile görünen ad hedefi doğrulayabilir; tersi
        #    durumda (görünen ad başkasını gösteriyorsa) skoru düşürür.
        display_name_similarity = partial_name_match(target, display_name)

        # 4) İsim parçalarının handle içinde kapsanması.
        token_coverage = partial_name_match(target, handle_slug)

        # 5) Handle şekli: kişi hesabı gibi mi?
        handle_shape = handle_shape_score(handle)

        # 6) Platform içerik doğrulaması (harvester'dan gelir).
        platform_confirm = float(candidate.get("platform_confirm", 0.0) or 0.0)
        platform_confirm = min(max(platform_confirm, 0.0), 1.0)

        # 7) Sayfa gövdesinde handle geçiyor mu?
        page_has_handle = 1.0 if candidate.get("page_has_handle") else 0.0

        # 8) Kaç bağımsız kaynak bildirdi?
        source_count = min(float(candidate.get("source_count", 1) or 1) / 3.0, 1.0)

        # 9) Güvenilir alan adı mı?
        domain = _domain_of(url)
        if domain in TRUSTED_DOMAINS:
            domain_trust = 1.0
        elif platform and any(platform.lower().split()[0] in d for d in TRUSTED_DOMAINS):
            domain_trust = 1.0
        else:
            domain_trust = 0.4

        # 10) Profil URL'si mi (gönderi/video/reklam değil)?
        is_profile_url = 1.0 if candidate.get("is_profile_url", True) else 0.2

        # 11) Sayfada okunabilen bir görünen ad var mı? "Kanıt yok" ile
        #     "karşı kanıt var" durumunu ayırır: Instagram/TikTok duvarında
        #     sayfa içeriği hiç gelmediği için 0 kalır.
        display_name_present = 1.0 if (display_name or "").strip() else 0.0

        # 12) Platform içeriği anonim istemciye gizliyor mu (IG giriş duvarı,
        #     TikTok WAF)? Bu durumda "doğrulama yok" bilgisi "hesap yok"
        #     anlamına gelmez; modelin bunu bilmesi gerekir.
        wall = 1.0 if candidate.get("wall") else 0.0

        # 13) Hedef tek kelime mi? Kullanıcı "hamigumi" gibi bir KULLANICI ADI
        #     yazdıysa, o adla var olan sayfa aranan şeyin ta kendisidir;
        #     sayfadaki görünen adın farklı olması bunu çürütmez. Tam ad
        #     yazıldığında ("Ahmet Yılmaz") ise farklı görünen ad güçlü bir
        #     karşı kanıttır. Bu ayrım modele özellik olarak verilir.
        single_token_target = 1.0 if len(name_tokens(target)) == 1 else 0.0

        # 14) ÇELİŞKİ (etkileşim özelliği): sayfada bir görünen ad okundu ama
        #     hedefle uyuşmuyor. Doğrusal bir model bu "VE" koşulunu tek
        #     başına öğrenemez; bu yüzden açıkça hesaplanır. Sayfa açıkça
        #     BAŞKA birini gösteriyorsa bu güçlü bir karşı kanıttır.
        display_name_conflict = 1.0 if (display_name_present >= 1.0 and
                                       display_name_similarity < 0.3) else 0.0

        # 15) BİYOGRAFİ (bio) kanıtı: profil açıklamasında hedefin adı geçiyor mu?
        #     Birçok platformda og:title kişinin adını vermez ama açıklama (bio)
        #     verir ("Yazılım geliştirici · Ahmet Yılmaz" gibi). Bu, görünen ad
        #     okunamadığında bile değerli bir doğrulama sinyalidir.
        bio_similarity = partial_name_match(target, description) if description else 0.0

        return {
            "handle_similarity": round(similarity, 4),
            "slug_match": slug_match,
            "display_name_similarity": display_name_similarity,
            "token_coverage": token_coverage,
            "handle_shape": handle_shape,
            "platform_confirm": platform_confirm,
            "page_has_handle": page_has_handle,
            "source_count": round(source_count, 4),
            "domain_trust": domain_trust,
            "is_profile_url": is_profile_url,
            "display_name_present": display_name_present,
            "wall": wall,
            "single_token_target": single_token_target,
            "display_name_conflict": display_name_conflict,
            "bio_similarity": bio_similarity,
            # Model ağırlığı taşımaz; ilgi kapısında/derecelendirmede kullanılır.
            "variant_tier": float(candidate.get("variant_tier", 0) or 0),
        }

    def _vector(self, features: dict) -> list:
        return [float(features.get(f, 0.0) or 0.0) for f in self.features]

    # -- Tahmin -------------------------------------------------------------

    @staticmethod
    def _sigmoid(z: float) -> float:
        if z >= 0:
            ez = math.exp(-z)
            return 1.0 / (1.0 + ez)
        ez = math.exp(z)
        return ez / (1.0 + ez)

    def predict_proba(self, features: dict) -> float:
        """Pozitif (hedefe ait) olma olasılığı: 0..1.

        Logit, KALİBRE EDİLMİŞ sıcaklıkla ölçeklenir: ham lojistik regresyon
        küçük veride aşırı özgüvenli olur; sıcaklık bunu düzeltir.
        """
        z = self.bias
        for f, x in zip(self.features, self._vector(features)):
            z += self.weights.get(f, 0.0) * x
        temperature = self.temperature or DEFAULT_TEMPERATURE
        return self._sigmoid(z / max(temperature, 1e-6))

    def relatedness_required(self, feats: dict) -> float:
        """Bu varyasyon biçimi için gereken en düşük isim benzerliği."""
        tier = int(feats.get("variant_tier", 0) or 0)
        return RELATEDNESS_REQUIRED.get(tier, RELATEDNESS_DEFAULT)

    def is_related(self, feats: dict) -> bool:
        """Aday, BİÇİM olarak hedefe bağlı mı? (yanlış-pozitifin birinci kapısı)

        Üç kanıt yolu vardır:
          1) Kullanıcı adı doğrudan isimden türetilmiş (slug_match >= 0.9),
          2) Profil sayfasındaki görünen ad hedefle uyuşuyor ve kullanıcı
             adı da en azından kısmen benziyor,
          3) Kullanıcı adı benzerliği, biçim tier'ının gerektirdiği eşiğin
             üzerinde.

        Gevşek biçimler (harf sırası değişimi, ünlü düşürme) için gereken
        benzerlik daha yüksektir; çünkü bu biçimler başka kişilere ait olabilir.
        """
        dns = feats.get("display_name_similarity", 0.0)
        hs = feats.get("handle_similarity", 0.0)
        sm = feats.get("slug_match", 0.0)

        if sm >= 0.9:
            return True
        if dns >= DISPLAY_NAME_STRONG and hs >= HANDLE_PARTIAL:
            return True
        if dns >= 0.6 and hs >= 0.5:
            return True
        return hs >= self.relatedness_required(feats)

    def classify(self, candidate: dict) -> tuple:
        """Adayı sınıflar. Döner: (etiket, olasılık, özellikler).

        Karar iki katmanlıdır ve bilinçli olarak basittir:

        1) İLGİ KAPISI  (`is_related`): aday biçim olarak isme bağlı değilse
           doğrudan elenir — model ne derse desin.
        2) ÇELİŞKİ KAPISI: tam ad sorgusunda sayfa açıkça başka birini
           gösteriyorsa aday elenir.
        3) KANIT KURALI: isme bağlı bir adayda SAYFA KANITI aranır:
             (A) sayfada hedefin görünen adı okundu,
             (B) sayfa hedefin kullanıcı adını doğruladı (meta/başlık),
             (C) platform içerik doğrulaması yüksek (sayfa gerçekten profil).
           Kanıt varsa eşleşme kesinleşir.
        4) Kanıt yoksa karar lojistik modele (kalibre edilmiş eşik) bırakılır.
           Böylece "isim tutuyor ama sayfa doğrulanmadı" durumu — klasik
           yanlış-pozitif kaynağı — körlemesine kabul edilmez.

        Not: Instagram/TikTok gibi içeriği gizleyen platformlarda kanıt HİÇ
        elde edilemez. Bu adaylar modelde "wall" özelliğiyle işaretlenir,
        doğrulanmış sayılmaz ve kullanıcıya "elle kontrol edilecek aday"
        olarak sunulur (kararı HeuristicFilter verir).
        """
        feats = self.extract_features(candidate)
        prob = self.predict_proba(feats)

        # 1) İlgi kapısı: biçimsel bağ yoksa eşleşme yok.
        if not self.is_related(feats):
            return 0, prob, feats

        # 2) ÇELİŞKİ kapısı: sorgu tam ad ("Ahmet Yılmaz") ve sayfa açıkça
        #    BAŞKA birini gösteriyorsa, kullanıcı adı benzese bile bu bir
        #    yanlış eşleşmedir (ör. "abugat" arayıp bir firmanın sayfasını
        #    bulmak). Kullanıcı adı sorgusunda (tek kelime) aynı durum
        #    BEKLENENdir ve çelişki sayılmaz.
        if (feats.get("display_name_conflict", 0.0) >= 1.0
                and feats.get("single_token_target", 0.0) < 1.0):
            feats["decision_basis"] = "karşı kanıt: sayfadaki ad hedefle çelişiyor"
            return 0, prob, feats

        hs = feats.get("handle_similarity", 0.0)
        dns = feats.get("display_name_similarity", 0.0)
        confirm = feats.get("platform_confirm", 0.0)
        page_handle = feats.get("page_has_handle", 0.0)
        name_shown = feats.get("display_name_present", 0.0)
        single_token = feats.get("single_token_target", 0.0)
        bio = feats.get("bio_similarity", 0.0)

        # 3) Sayfa kanıtı varsa eşleşme kesinleşir.
        #    (A) Sayfada hedefin görünen adı okundu -> en güçlü kanıt.
        if name_shown and dns >= DISPLAY_NAME_STRONG and hs >= HANDLE_PARTIAL:
            return 1, self._evidence_probability(prob, feats, "display_name"), feats
        #    (B) Sayfanın başlığı/meta bilgisi hedef handle'ını doğruladı.
        if page_handle >= 1.0 and hs >= HANDLE_STRONG:
            return 1, self._evidence_probability(prob, feats, "page_handle"), feats
        #    (B2) Profil AÇIKLAMASI (bio) hedefin ismini içeriyor ve kullanıcı
        #         adı da en azından kısmen benziyor -> güçlü destekleyici kanıt.
        if bio >= 0.99 and hs >= HANDLE_PARTIAL:
            return 1, self._evidence_probability(prob, feats, "bio"), feats
        #    (C) Platform "bu sayfa var" diyor ve kullanıcı adı hedefe çok yakın.
        #        Sayfadaki görünen ad farklıysa bu yalnızca TAM AD sorgusunda
        #        karşı kanıttır; kullanıcı adı sorgusunda (tek kelime) beklenen
        #        bir durumdur.
        if confirm >= PLATFORM_CONFIRM_MIN and hs >= HANDLE_STRONG:
            if single_token >= 1.0 or name_shown < 0.5 or dns >= 0.5:
                return 1, self._evidence_probability(prob, feats, "platform_confirm"), feats

        # 4) Kanıt yok / zayıf: kararı modele bırak.
        feats["decision_basis"] = "model olasılığı"
        return (1 if prob >= self.threshold else 0), prob, feats

    def _evidence_probability(self, prob: float, feats: dict, kind: str) -> float:
        """Kanıt kuralı ile lojistik modelin olasılığını birleştirir.

        Kural, alan bilgisini (domain knowledge) temsil eden deterministik bir
        doğrulamadır; model ise kanıtın zayıf olduğu gri bölge için öğrenilmiştir.
        Nihai güven, ikisinin en güçlüsüdür: böylece kanıt varsa sistem kararını
        saklamaz, kanıt yoksa model ne diyorsa o geçerlidir.
        """
        label = {
            "display_name": "sayfadaki görünen ad hedefle uyuşuyor",
            "page_handle": "sayfa başlığı/meta handle'ı doğruladı",
            "platform_confirm": "platform doğrulaması + kullanıcı adı eşleşmesi",
            "bio": "profil açıklaması hedefin ismini içeriyor",
        }.get(kind, kind)
        feats["decision_basis"] = f"kanıt kuralı: {label}"
        return round(max(prob, EVIDENCE_CONFIDENCE.get(kind, 0.85)), 4)

    def explain(self, features: dict) -> str:
        """Modelin kararını insan-okur biçimde açıklar.

        Hem DESTEKLEYEN hem DESTEKLEMEYEN kanıtlar yazılır; böylece
        arayüzdeki "neden bu karar?" sorusu net cevaplanır.
        """
        if not self.is_related(features):
            return "isimle biçimsel bağ zayıf (ilgi kapısı) — aday elendi"

        if features.get("wall", 0.0) >= 0.5:
            return ("platform içeriği anonim erişime kapalı (giriş/anti-bot "
                    "duvarı) — otomatik doğrulama mümkün değil, elle kontrol")

        reasons = []
        if features.get("display_name_similarity", 0.0) >= 0.6:
            reasons.append("profil görünen adı hedefle uyuşuyor "
                           f"(%{features['display_name_similarity']*100:.0f})")
        if features.get("page_has_handle", 0.0) >= 1.0:
            reasons.append("sayfa başlığı/meta içinde handle doğrulandı")
        if features.get("platform_confirm", 0.0) >= PLATFORM_CONFIRM_MIN:
            reasons.append(
                f"platform içerik doğrulaması %{features['platform_confirm']*100:.0f}")
        if features.get("bio_similarity", 0.0) >= 0.99:
            reasons.append("profil açıklaması (bio) hedefin ismini içeriyor")
        if features.get("slug_match", 0.0) >= 1.0:
            reasons.append("kullanıcı adı isimden birebir türetilmiş")
        elif features.get("handle_similarity", 0.0) >= HANDLE_STRONG:
            reasons.append(
                f"isim benzerliği yüksek (%{features['handle_similarity']*100:.0f})")
        if features.get("source_count", 0.0) >= 0.66:
            reasons.append("birden çok bağımsız kaynak")

        # Karşı kanıtlar
        if (features.get("display_name_present", 0.0) >= 0.5
                and features.get("display_name_similarity", 0.0) < 0.3):
            reasons.append("sayfadaki görünen ad hedefle uyuşmuyor")
        if features.get("handle_shape", 1.0) < 0.6:
            reasons.append("kullanıcı adı şekli rastgele/jenerik")
        if (features.get("platform_confirm", 0.0) < PLATFORM_CONFIRM_MIN
                and features.get("page_has_handle", 0.0) < 1.0
                and features.get("display_name_present", 0.0) < 0.5):
            reasons.append("sayfa kanıtı yok (isim benzerliği tek başına yetersiz)")
        if not reasons:
            reasons.append("zayıf/tesadüfi benzerlik")
        return ", ".join(reasons)

    def explain_rows(self, feats: dict) -> list:
        """Arayüzdeki karar paneli için (etiket, değer, katkı) satırları üretir.

        Katkı = ağırlık x değer; pozitif katkı eşleşmeye, negatif katkı
        elemeye iter. Özellikle kullanıcıya "AI neden böyle karar verdi?"
        sorusunun sayısal cevabını verir.
        """
        rows = []
        for name in self.features:
            value = float(feats.get(name, 0.0) or 0.0)
            weight = float(self.weights.get(name, 0.0))
            rows.append({
                "key": name,
                "label": FEATURE_LABELS.get(name, name),
                "value": round(value, 4),
                "weight": round(weight, 4),
                "contribution": round(weight * value, 4),
            })
        rows.sort(key=lambda r: abs(r["contribution"]), reverse=True)
        return rows

    # -- Eğitim -------------------------------------------------------------

    def train(self, samples: list, epochs: int = 1500, lr: float = 0.35, l2: float = 0.002):
        """Lojistik regresyonu gradyan inişiyle eğitir.

        samples: [ (features_dict, label) , ... ]  label in {0, 1}

        L2 cezası (l2) ağırlıkların tek bir özelliğe yüklenip şişmesini
        engeller; aksi halde küçük bir sentetik küme ezberlenir ve gerçek
        adaylarda aşırı uç olasılıklar (%0 / %100) çıkar.
        """
        if not samples:
            return
        # Deterministik karıştırma -> tekrarlanabilir eğitim.
        rng = random.Random(42)
        data = [(self._vector(f), int(l)) for f, l in samples]

        for _ in range(epochs):
            rng.shuffle(data)
            for x, y in data:
                p = self._sigmoid(self.bias + sum(w * xi for w, xi in zip(self._weights_list(), x)))
                err = y - p
                self.bias += lr * err
                for i, f in enumerate(self.features):
                    self.weights[f] += lr * (err * x[i] - l2 * self.weights[f])

        self.trained_samples += len(samples)

    def _weights_list(self) -> list:
        return [self.weights.get(f, 0.0) for f in self.features]

    def _snapshot(self) -> dict:
        """Çevrimiçi güncelleme öncesi durumu saklar (geri alınabilirlik)."""
        return {"bias": self.bias, "weights": dict(self.weights),
                "threshold": self.threshold, "temperature": self.temperature}

    def online_update(self, features: dict, label: int, lr: float = 0.25):
        """Tek bir geri bildirimle modeli anında günceller (online learning).

        Güvenlik: ilk güncellemeden önce mevcut durum saklanır
        (`revert_online_updates`) ve ağırlıklar ±MAX_ABS_WEIGHT ile sınırlıdır.
        Böylece tek bir yanlış geri bildirim modeli bozamaz.
        """
        if self._baseline is None:
            self._baseline = self._snapshot()
        x = self._vector(features)
        p = self.predict_proba(features)
        err = int(label) - p
        self.bias = max(-12.0, min(12.0, self.bias + lr * err))
        for i, f in enumerate(self.features):
            updated = self.weights[f] + lr * (err * x[i] - 0.0005 * self.weights[f])
            self.weights[f] = max(-MAX_ABS_WEIGHT, min(MAX_ABS_WEIGHT, updated))
        self.online_updates += 1
        self.feedback_count += 1

    def revert_online_updates(self) -> bool:
        """Çevrimiçi güncellemeleri geri alır (son toplu modele döner).

        Yanlış verilmiş geri bildirimler modeli kaydırdıysa arayüzden tek
        komutla geri dönülebilsin diye vardır.
        """
        if not self._baseline:
            return False
        self.bias = self._baseline["bias"]
        self.weights = dict(self._baseline["weights"])
        self.threshold = self._baseline["threshold"]
        self.temperature = self._baseline["temperature"]
        self.online_updates = 0
        self._baseline = None
        return True

    # -- Değerlendirme ve kalibrasyon ---------------------------------------

    def predict_labels(self, samples: list, threshold: float = None) -> list:
        """Örnekler için etiket tahmini üretir (ilgi kapısı uygulanmaz)."""
        threshold = self.threshold if threshold is None else threshold
        return [1 if self.predict_proba(f) >= threshold else 0 for f, _l in samples]

    def evaluate(self, samples: list, threshold: float = None) -> dict:
        """Accuracy / precision / recall / F1 hesaplar (saf Python)."""
        if not samples:
            return {"count": 0, "accuracy": 0.0, "precision": 0.0,
                    "recall": 0.0, "f1": 0.0}
        preds = self.predict_labels(samples, threshold)
        tp = fp = tn = fn = 0
        for (pred, (_f, label)) in zip(preds, samples):
            if pred == 1 and label == 1:
                tp += 1
            elif pred == 1 and label == 0:
                fp += 1
            elif pred == 0 and label == 0:
                tn += 1
            else:
                fn += 1
        total = tp + fp + tn + fn
        accuracy = (tp + tn) / total if total else 0.0
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        return {"count": total, "accuracy": round(accuracy, 4),
                "precision": round(precision, 4), "recall": round(recall, 4),
                "f1": round(f1, 4), "tp": tp, "fp": fp, "tn": tn, "fn": fn}

    def log_loss(self, samples: list, temperature: float = None) -> float:
        """Ortalama log-loss (olasılık kalitesinin ana ölçütü; düşük daha iyi)."""
        if not samples:
            return 0.0
        keep = self.temperature
        if temperature is not None:
            self.temperature = float(temperature)
        try:
            eps = 1e-9
            total = 0.0
            for feats, label in samples:
                p = min(max(self.predict_proba(feats), eps), 1.0 - eps)
                total += -(int(label) * math.log(p) + (1 - int(label)) * math.log(1.0 - p))
            return round(total / len(samples), 4)
        finally:
            self.temperature = keep

    def brier_score(self, samples: list) -> float:
        """Brier skoru: (olasılık - gerçek)² ortalaması. Düşük daha iyi."""
        if not samples:
            return 0.0
        total = sum((self.predict_proba(f) - int(y)) ** 2 for f, y in samples)
        return round(total / len(samples), 4)

    def fit_temperature(self, samples: list, steps: int = 25) -> float:
        """Platt ölçekleme: doğrulama kümesinde log-loss'u en aza indiren T.

        Kalibrasyonun KENDİSİ de doğrulama kümesinde ölçülür; eğitimde
        kullanılan veriye bakmaz (yoksa yine şişmiş sonuç alınırdı).
        """
        if len(samples) < 4:
            return self.temperature
        lo, hi = TEMPERATURE_RANGE
        best_t, best_loss = self.temperature, None
        for i in range(steps + 1):
            t = lo + (hi - lo) * i / steps
            loss = self.log_loss(samples, temperature=t)
            if best_loss is None or loss < best_loss - 1e-12:
                best_t, best_loss = t, loss
        return round(best_t, 4)

    @staticmethod
    def _split(samples: list, holdout_ratio: float = 0.25, seed: int = 42) -> tuple:
        """Sınıf dengeli (stratified) eğitim/doğrulama ayrımı.

        Aynı veriyle hem eğitip hem ölçmek metrikleri şişirir; bu yüzden
        metrikler AYRI tutulan doğrulama kümesinden üretilir.
        """
        rng = random.Random(seed)
        positives = [s for s in samples if int(s[1]) == 1]
        negatives = [s for s in samples if int(s[1]) == 0]
        train, holdout = [], []
        for group in (positives, negatives):
            pool = list(group)
            rng.shuffle(pool)
            n_hold = max(1, int(round(len(pool) * holdout_ratio))) if len(pool) > 2 else 0
            holdout.extend(pool[:n_hold])
            train.extend(pool[n_hold:])
        rng.shuffle(train)
        rng.shuffle(holdout)
        return train, holdout

    def _fit(self, samples: list, epochs: int = 3000, lr: float = 0.3) -> dict:
        """Eğit + kalibre et + DÜRÜST metrikleri üret (tek noktadan).

        Dönüş: holdout metrik sözlüğü (`self.metrics`).
        """
        if not samples:
            return self.metrics
        train_set, holdout = self._split(samples)
        self.bias = 0.0
        self.weights = {f: 0.0 for f in self.features}
        self.temperature = DEFAULT_TEMPERATURE
        self.train(train_set or samples, epochs=epochs, lr=lr)
        eval_set = holdout or (train_set or samples)

        uncalibrated = self.log_loss(eval_set)
        if len(eval_set) >= 4:
            self.temperature = self.fit_temperature(eval_set)
        self.calibration = {
            "log_loss_uncalibrated": uncalibrated,
            "log_loss_calibrated": self.log_loss(eval_set),
            "temperature": self.temperature,
        }
        self.calibrate_threshold(eval_set)
        self.metrics = self.evaluate(eval_set)
        self.metrics.update({
            "split": "holdout" if holdout else "train",
            "holdout_size": len(eval_set),
            "log_loss": self.calibration["log_loss_calibrated"],
            "brier": self.brier_score(eval_set),
        })
        self.train_metrics = self.evaluate(train_set or samples)
        return self.metrics

    def calibration_report(self, samples: list = None, bins: int = 5) -> dict:
        """Güvenilirlik tablosu: "%70 dediğinde gerçekten %70 mi?"

        Arayüzdeki 'Yapay Zeka > Kalibrasyon' menüsü bunu gösterir.
        """
        samples = list(samples) if samples else self._synthetic_samples()
        pairs = [(self.predict_proba(f), int(y)) for f, y in samples]
        rows = []
        for i in range(bins):
            lo, hi = i / bins, (i + 1) / bins
            bucket = [(p, y) for p, y in pairs
                      if (lo <= p < hi) or (i == bins - 1 and p >= hi)]
            if not bucket:
                continue
            mean_p = sum(p for p, _y in bucket) / len(bucket)
            observed = sum(y for _p, y in bucket) / len(bucket)
            rows.append({
                "range": f"%{lo*100:.0f}-%{hi*100:.0f}",
                "count": len(bucket),
                "predicted": round(mean_p, 3),
                "observed": round(observed, 3),
                "gap": round(mean_p - observed, 3),
            })
        return {"bins": rows, "count": len(pairs), "temperature": self.temperature,
                "brier": self.brier_score(samples), "log_loss": self.log_loss(samples)}

    def feature_weights(self, limit: int = None) -> list:
        """Ağırlıkları büyüklüğe göre azalan sırada döndürür (açıklanabilirlik)."""
        rows = [{"key": f, "label": FEATURE_LABELS.get(f, f),
                 "weight": round(float(self.weights.get(f, 0.0)), 3)}
                for f in self.features]
        rows.sort(key=lambda r: abs(r["weight"]), reverse=True)
        return rows[:limit] if limit else rows

    def calibrate_threshold(self, samples: list, steps: int = 26) -> float:
        """F1'i en yükseğe çıkaran karar eşiğini bulur (THRESHOLD_MIN..MAX).

        Sentetik doğrulama kümesi küçük olduğu için aşırı uçlara kaymayı
        engellemek adına aralık sınırlandırılır.
        """
        if not samples:
            return self.threshold
        best_threshold, best_f1 = self.threshold, -1.0
        for i in range(steps + 1):
            candidate = THRESHOLD_MIN + (THRESHOLD_MAX - THRESHOLD_MIN) * i / steps
            f1 = self.evaluate(samples, candidate)["f1"]
            if f1 > best_f1 + 1e-9:
                best_f1, best_threshold = f1, candidate
        self.threshold = round(best_threshold, 4)
        return self.threshold

    # -- Geri bildirimden yeniden eğitim ------------------------------------

    def load_feedback_samples(self, path: str = None) -> list:
        """database/ai_feedback.jsonl kayıtlarını (features, label) hâline getirir.

        Arayüzdeki "Doğru/Yanlış" düğmeleri bu dosyaya yazar; böylece kullanıcı
        geri bildirimi uygulama yeniden açıldığında da modele katkı verir.
        """
        path = path or self.feedback_path
        samples = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    feats = record.get("features")
                    label = record.get("label")
                    if not isinstance(feats, dict) or label not in (0, 1):
                        continue
                    samples.append((self._normalize_feats(feats), int(label)))
        except OSError:
            return []
        return samples

    def _normalize_feats(self, feats: dict) -> dict:
        """Eski sürümden kalan özellik sözlüklerini yeni şemaya tamamlar."""
        return {name: float(feats.get(name, 0.0) or 0.0) for name in self.features}

    def retrain_from_feedback(self, path: str = None,
                              min_samples: int = MIN_FEEDBACK_FOR_RETRAIN) -> int:
        """Biriken gerçek geri bildirimle modeli yeniden eğitir.

        Sentetik bootstrap örnekleri temel alınır, üzerine gerçek geri bildirim
        örnekleri eklenir ve model yeniden eğitilip KAYDEDİLİR. Kaç gerçek
        örnek kullanıldığını döndürür.
        """
        samples = self.load_feedback_samples(path)
        if len(samples) < min_samples:
            return 0

        base = self._synthetic_samples()
        combined = base + samples
        # Gerçek veriyi daha ağır öğrenmek için hafif çoğaltma.
        combined += samples * 2

        self._fit(combined, epochs=3000, lr=0.3)
        self.real_feedback_samples = len(samples)
        self.feedback_count = max(self.feedback_count, len(samples))
        self.retrained_feedback_count = len(samples)
        self.retrain_runs += 1
        self._baseline = None      # toplu eğitim çevrimiçi güncellemeleri kapsar
        self.save()
        return len(samples)

    # -- Bootstrap ----------------------------------------------------------

    @staticmethod
    def _synthetic_samples() -> list:
        """Etiketi insan mantığıyla belirlenmiş sentetik eğitim/doğrulama kümesi.

        Tasarım ilkeleri:
          * POZİTİF ve NEGATİF sayısı eşittir ve özellik dağılımları
            birbirine benzer (tek bir özellik tek başına etiketi
            belirlemesin). Böylece model, veri kümesindeki yapay
            korelasyonları ezberlemek yerine gerçek kanıt mantığını öğrenir.
          * POZİTİF: sayfa kanıtı VAR (platform doğrulaması, sayfada handle,
            ya da sayfadaki görünen ad hedefle uyuşuyor).
          * NEGATİF: isim birebir tutsa bile sayfa kanıtı YOK/yetersiz, ya da
            sayfa açıkça BAŞKA birini gösteriyor.
          * Duvar (wall) örnekleri negatif tarafındadır: içerik gizliyken
            otomatik doğrulama iddia edilemez. Bu adaylar zaten
            HeuristicFilter tarafından "elle kontrol" listesine alınır.
        """
        def row(handle_similarity, slug_match, display_name_similarity, token_coverage,
                handle_shape, platform_confirm, page_has_handle, source_count,
                domain_trust=1.0, is_profile_url=1.0,
                display_name_present=0.0, wall=0.0, single_token_target=0.0,
                bio_similarity=0.0):
            # Etkileşim özelliği tek yerde hesaplanır: sayfada bir görünen ad
            # okundu ama hedefle uyuşmuyor -> çelişki.
            conflict = 1.0 if (display_name_present >= 1.0
                               and display_name_similarity < 0.3) else 0.0
            return {
                "handle_similarity": handle_similarity,
                "slug_match": slug_match,
                "display_name_similarity": display_name_similarity,
                "token_coverage": token_coverage,
                "handle_shape": handle_shape,
                "platform_confirm": platform_confirm,
                "page_has_handle": page_has_handle,
                "source_count": source_count,
                "domain_trust": domain_trust,
                "is_profile_url": is_profile_url,
                "display_name_present": display_name_present,
                "wall": wall,
                "single_token_target": single_token_target,
                "display_name_conflict": conflict,
                "bio_similarity": bio_similarity,
            }

        # --- Pozitif: sayfa kanıtı VAR -----------------------------------
        pos = [
            # 1) Tam slug + platform sayfası var (GitHub benzeri)
            row(1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0.67),
            row(1.0, 1.0, 0.0, 1.0, 1.0, 0.6, 1.0, 0.33),
            # 2) KULLANICI ADI sorgusu (tek kelime) + sayfa var, görünen ad farklı
            #    -> beklenen durum (ör. "hamigumi" -> youtube.com/@hamigumi)
            row(1.0, 1.0, 0.0, 1.0, 1.0, 0.55, 0.0, 0.67, 1.0, 1.0, 1.0, 0.0, 1.0),
            row(1.0, 1.0, 0.0, 1.0, 1.0, 0.7, 0.0, 0.33, 1.0, 1.0, 1.0, 0.0, 1.0),
            row(0.95, 1.0, 0.0, 1.0, 1.0, 0.6, 1.0, 0.67, 1.0, 1.0, 0.0, 0.0, 1.0),
            # 3) Tam slug + sayfadaki görünen ad da uyuşuyor
            row(1.0, 1.0, 1.0, 1.0, 1.0, 0.8, 1.0, 0.67, 1.0, 1.0, 1.0),
            row(0.95, 1.0, 1.0, 1.0, 1.0, 0.6, 1.0, 1.0, 1.0, 1.0, 1.0),
            # 4) Baş harf / kısaltma biçimi + görünen ad
            row(0.95, 0.5, 1.0, 0.5, 1.0, 0.8, 1.0, 0.67, 1.0, 1.0, 1.0),
            row(0.96, 0.5, 0.9, 0.5, 1.0, 0.5, 0.0, 0.33, 1.0, 1.0, 1.0),
            row(0.92, 0.5, 0.6, 0.5, 1.0, 0.6, 1.0, 0.33, 1.0, 1.0, 1.0),
            # 5) Gevşek biçim AMA görünen ad hedefi doğruluyor
            row(0.85, 0.0, 1.0, 0.5, 1.0, 0.8, 1.0, 0.67, 1.0, 1.0, 1.0),
            row(0.80, 0.0, 0.6, 0.5, 1.0, 0.7, 1.0, 0.33, 1.0, 1.0, 1.0),
            # 6) Gevşek biçim + sayfa handle'ı doğruladı (kırpma / ünlü düşmesi)
            row(0.95, 0.0, 0.0, 1.0, 1.0, 0.9, 1.0, 0.67),
            row(0.9, 0.5, 0.0, 1.0, 1.0, 0.55, 1.0, 0.67),
            # 7) Kısmi görünen ad (soyad) + güçlü handle kanıtı
            row(0.9, 0.5, 0.5, 1.0, 1.0, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0),
            # 8) Görünen ad YOK ama profil AÇIKLAMASI (bio) hedefin ismini
            #    içeriyor + kullanıcı adı da kısmen benziyor -> doğrulanır.
            row(0.9, 0.5, 0.0, 1.0, 1.0, 0.4, 0.0, 0.67, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            row(0.85, 0.0, 0.0, 0.5, 1.0, 0.5, 0.0, 0.33, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0),
        ]
        # --- Negatif: kanıt yok / karşı kanıt var ------------------------
        neg = [
            # 1) Tam slug ama sayfa doğrulanmadı (kabuk / kanıt yok)
            row(0.85, 1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.33),
            row(1.0, 1.0, 0.0, 1.0, 1.0, 0.1, 0.0, 0.33),
            row(0.9, 0.5, 0.0, 0.5, 1.0, 0.2, 0.0, 0.0),
            row(0.9, 0.5, 0.0, 0.5, 1.0, 0.2, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0),
            # 2) Duvar (Instagram/TikTok): içerik gizli, doğrulanamaz
            row(1.0, 1.0, 0.0, 1.0, 1.0, 0.3, 0.0, 0.33, 1.0, 1.0, 0.0, 1.0, 1.0),
            row(0.85, 0.0, 0.0, 0.5, 1.0, 0.3, 0.0, 0.33, 1.0, 1.0, 0.0, 1.0),
            # 3) TAM AD sorgusu ve sayfa BAŞKA birini gösteriyor -> yanlış eşleşme
            row(1.0, 1.0, 0.0, 1.0, 1.0, 0.8, 0.0, 0.67, 1.0, 1.0, 1.0),
            row(0.95, 1.0, 0.3, 1.0, 1.0, 0.7, 0.0, 0.33, 1.0, 1.0, 1.0),
            row(0.9, 1.0, 0.0, 1.0, 1.0, 0.6, 0.0, 0.67, 1.0, 1.0, 1.0),
            # 4) Alakasız isim (sayfada hedefin adı var ama handle alakasız)
            row(0.49, 0.0, 1.0, 0.0, 1.0, 0.2, 0.0, 0.33, 1.0, 1.0, 1.0),
            row(0.4, 0.0, 0.0, 0.0, 1.0, 0.2, 0.0, 0.0),
            row(0.3, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.33),
            # 5) Gönderi/reklam URL'si, profil değil
            row(0.45, 0.0, 0.0, 0.0, 1.0, 0.4, 0.0, 0.33, 1.0, 0.2),
            # 6) Rastgele rakamlı / jenerik handle, ad uyuşmuyor
            row(0.7, 0.0, 0.0, 0.5, 0.35, 0.6, 1.0, 0.67),
            row(0.65, 0.0, 0.0, 0.0, 0.3, 0.5, 1.0, 0.33),
            # 7) Kısmi görünen ad (soyad bile tutmuyor) + zayıf handle benzerliği
            row(0.6, 0.0, 0.5, 0.5, 0.9, 0.2, 0.0, 0.33, 1.0, 1.0, 1.0),
            # 8) Biyografide isim YOK ve sayfa kanıtı da yok -> doğrulanamaz.
            row(0.8, 0.0, 0.0, 0.5, 1.0, 0.3, 0.0, 0.33, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0),
            row(1.0, 1.0, 0.0, 1.0, 1.0, 0.2, 0.0, 0.33, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0),
        ]
        return [(f, 1) for f in pos] + [(f, 0) for f in neg]

    def _bootstrap_train(self):
        """Model dosyası yoksa sentetik örneklerle ön eğitim + kalibrasyon."""
        self._fit(self._synthetic_samples(), epochs=3000, lr=0.3)
        self.save()

    # -- Kalıcılık ----------------------------------------------------------

    def save(self, path: str = None) -> bool:
        path = path or self.model_path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = {
                "features": self.features,
                "bias": self.bias,
                "weights": self.weights,
                "threshold": self.threshold,
                "temperature": self.temperature,
                "feedback_count": self.feedback_count,
                "trained_samples": self.trained_samples,
                "real_feedback_samples": self.real_feedback_samples,
                "retrained_feedback_count": self.retrained_feedback_count,
                "online_updates": self.online_updates,
                "metrics": self.metrics,
                "train_metrics": self.train_metrics,
                "calibration": self.calibration,
                "version": MODEL_VERSION,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def load(self, path: str = None) -> bool:
        path = path or self.model_path
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            # Özellik şeması ya da model sürümü değiştiyse eski ağırlıklar
            # geçersizdir -> yeniden eğit.
            if payload.get("features") != self.features:
                return False
            if int(payload.get("version", 0)) != MODEL_VERSION:
                return False
            self.bias = float(payload.get("bias", 0.0))
            self.weights = {f: float(payload.get("weights", {}).get(f, 0.0)) for f in self.features}
            self.threshold = float(payload.get("threshold", DECISION_THRESHOLD))
            self.temperature = float(payload.get("temperature", DEFAULT_TEMPERATURE))
            self.feedback_count = int(payload.get("feedback_count", 0))
            self.trained_samples = int(payload.get("trained_samples", 0))
            self.real_feedback_samples = int(payload.get("real_feedback_samples", 0))
            self.retrained_feedback_count = int(payload.get("retrained_feedback_count", 0))
            self.online_updates = int(payload.get("online_updates", 0))
            for attr in ("metrics", "train_metrics", "calibration"):
                value = payload.get(attr)
                setattr(self, attr, value if isinstance(value, dict) else {})
            return True
        except Exception:
            return False

    # -- Durum -------------------------------------------------------------

    def describe(self) -> str:
        """Arayüz için kısa model durumu."""
        m = self.metrics or {}
        acc = f" · doğruluk %{m['accuracy']*100:.0f}" if m.get("accuracy") is not None else ""
        f1 = f" · F1 %{m['f1']*100:.0f}" if m.get("f1") is not None else ""
        split = f" ({m['split']})" if m.get("split") else ""
        return (f"{self.trained_samples} örnek · {self.feedback_count} geri bildirim"
                f" · eşik {self.threshold:.2f} · T={self.temperature:.2f}"
                f"{acc}{f1}{split}")

    def status_report(self) -> str:
        """Arayüz/CLI için tam model raporu (menüde gösterilir)."""
        m, tm = self.metrics or {}, self.train_metrics or {}
        cal = self.calibration or {}
        lines = [
            f"Model sürümü        : {MODEL_VERSION}",
            f"Eğitilen örnek      : {self.trained_samples}",
            f"Geri bildirim       : {self.feedback_count} "
            f"(son eğitimde kullanılan: {self.retrained_feedback_count})",
            f"Karar eşiği         : {self.threshold:.2f}",
            f"Kalibrasyon sıcaklığı: T={self.temperature:.2f}",
            f"Çevrimiçi güncelleme: {self.online_updates}",
        ]
        if m:
            lines.append(
                f"DOĞRULAMA ({m.get('split', '?')}, {m.get('holdout_size', 0)} örnek): "
                f"doğruluk %{m.get('accuracy', 0)*100:.0f}, "
                f"kesinlik %{m.get('precision', 0)*100:.0f}, "
                f"hatırlama %{m.get('recall', 0)*100:.0f}, "
                f"F1 %{m.get('f1', 0)*100:.0f}, "
                f"log-loss {m.get('log_loss', 0):.4f}, Brier {m.get('brier', 0):.4f}")
        if tm:
            lines.append(
                f"EĞİTİM ({tm.get('count', 0)} örnek)      : "
                f"doğruluk %{tm.get('accuracy', 0)*100:.0f}, F1 %{tm.get('f1', 0)*100:.0f}"
                "   (holdout ile arasındaki fark ezberleme göstergesidir)")
        if cal:
            before = cal.get("log_loss_uncalibrated")
            after = cal.get("log_loss_calibrated")
            if before is not None and after is not None:
                lines.append(f"Kalibrasyon etkisi  : log-loss {before:.4f} -> {after:.4f}"
                             f" (T={cal.get('temperature', 1.0):.2f})")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

def _domain_of(url: str) -> str:
    if not url or "://" not in url:
        return ""
    netloc = url.split("://", 1)[1].split("/", 1)[0].lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _handle_from_url(url: str) -> str:
    if not url:
        return ""
    path = url.split("://", 1)[-1]
    path = path.split("/", 1)[-1] if "/" in path else ""
    segments = [s for s in path.split("/") if s]
    if not segments:
        return ""
    return slugify(segments[-1].lstrip("@"))


_ENGINE_SINGLETON = None


def get_engine() -> AIEngine:
    """Süreç içinde tek AI motoru; geri bildirimler birikimli kalsın diye."""
    global _ENGINE_SINGLETON
    if _ENGINE_SINGLETON is None:
        _ENGINE_SINGLETON = AIEngine()
    return _ENGINE_SINGLETON
