"""
HandleGenerator - Bir isimden olası kullanıcı adı varyasyonları üretir.

Amaç: tam eşleşmeyen hesapları da bulabilmek. Örneğin "Aser Bugat" için
`aserbugat`, `aser_bugat`, `aser.bugat`, `abugat`, `aserb`, `asrbugat`,
`abugat` -> `baugat` (harf sırası) gibi biçimler üretilir ve sırayla denenir.

Tasarım ilkeleri:
  * Sıralıdır: en olası biçimler önce gelir (tier 0 = tam eşleşme).
  * İsimle sözlüksel bağını korur: rastgele üretim yapmaz. Böylece AI'nın
    benzerlik eşiği anlamlı kalır ve yanlış-pozitif riski düşer.
  * `include_loose=False` ile yalnızca sıkı biçimler istenebilir.
  * Her biçim bir TIER taşır. Tier, "bu biçim isme ne kadar bağlı?" sorusunun
    cevabıdır ve iki yerde kullanılır:
      1) AI ilgi kapısı (gevşek biçimden daha yüksek benzerlik ister),
      2) raporlama (güçlü eşleşme = doğrulandı, gevşek eşleşme = manuel
         kontrol edilecek aday).

Üretilen biçim aileleri (tier küçükten büyüğe):
  * TIER_EXACT     : tam slug ("aserbugat")
  * TIER_SEPARATOR : ayraçlı biçimler ("aser_bugat", "aser.bugat", "aser-bugat")
  * TIER_INITIAL   : baş harf kombinasyonları ("abugat", "aserb", "a_bugat")
  * TIER_WORD      : kelime kısaltmaları, ters sıra, tek kelimeler
                     ("bugataser", "asbugat", "aserbug", "bugat")
  * TIER_LOOSE     : ünlü düşürme, harf düşmesi, komşu harf sırası değişimi,
                     tekrar eden harfin sadeleşmesi ("srbgt", "baugat")
"""

import re

from intelligence.ai_engine import slugify

_LOOSE_VOWELS = "aeiou"

# Tier (öncelik) değerleri: küçük olan önce denenir.
TIER_EXACT = 0        # tam slug
TIER_SEPARATOR = 1    # ayraçlı biçimler (_, ., -)
TIER_INITIAL = 2      # baş harf kombinasyonları
TIER_WORD = 3         # kelime kısaltmaları / ters sıra / tek kelime
TIER_LOOSE = 4        # ünlü düşürme, harf düşmesi, harf sırası değişimi

# Bu tier'a kadar olan biçimler "güçlü" sayılır: isme doğrudan bağlıdırlar.
# Üstündekiler (TIER_WORD ve TIER_LOOSE) yalnızca ADAY/ipucudur; yanlış
# pozitife açık oldukları için manuel doğrulama gerektirirler.
STRONG_TIER_MAX = TIER_INITIAL

# Tier adları raporlarda/loglarda gösterilir.
TIER_LABELS = {
    TIER_EXACT: "tam eşleşme",
    TIER_SEPARATOR: "ayraçlı",
    TIER_INITIAL: "baş harf",
    TIER_WORD: "kelime kısaltması",
    TIER_LOOSE: "harf değişimi",
}


def tokenize(name: str) -> list:
    """İsmi ASCII slug parçalarına ayırır ('Aser Bugat' -> ['aser', 'bugat'])."""
    raw = re.split(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü]+", str(name or ""))
    return [p for p in (slugify(part) for part in raw) if p]


def _valid(handle: str) -> bool:
    if not handle or not (3 <= len(handle) <= 30):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._\-]+", handle))


def _drop_vowels(text: str) -> str:
    return "".join(ch for ch in text if ch not in _LOOSE_VOWELS)


def _collapse_repeats(text: str) -> str:
    """Ardışık tekrar eden harfleri tekleştirir ('ahmett' -> 'ahmet')."""
    out = []
    for ch in text:
        if out and out[-1] == ch:
            continue
        out.append(ch)
    return "".join(out)


def generate_handles(name: str, limit: int = None, include_loose: bool = True) -> list:
    """İsme sözlüksel olarak bağlı, sıralı kullanıcı adı adayları üretir."""
    tiered = generate_handles_with_tiers(name, limit=limit, include_loose=include_loose)
    return [handle for handle, _tier in tiered]


def generate_handles_with_tiers(name: str, limit: int = None,
                                include_loose: bool = True) -> list:
    """(handle, tier) çiftleri döndürür; tier küçükse biçim daha güvenilirdir.

    Tier bilgisi AI'nın ilgi kapısında kullanılır: gevşek biçimlerin
    raporlanabilmesi için isme daha çok benzemesi gerekir.
    """
    parts = tokenize(name)
    if not parts:
        return []

    best = {}  # handle -> en iyi (en küçük) tier

    def add(handle, tier):
        handle = (handle or "").strip("._-")
        if not _valid(handle):
            return
        if handle not in best or tier < best[handle]:
            best[handle] = tier

    base = "".join(parts)
    add(base, TIER_EXACT)

    _add_separators(add, parts)
    _add_initials(add, parts)
    _add_word_forms(add, parts, base)
    if include_loose:
        _add_loose(add, parts, base)

    # Tier'a göre sırala; eşit tier'da ekleme sırası korunur (kararlı sıralama)
    # böylece "en olası biçim önce" garantisi bozulmaz.
    ordered = sorted(best.items(), key=lambda kv: kv[1])
    return ordered[:limit] if limit else ordered


# ---------------------------------------------------------------------------
# Biçim aileleri
# ---------------------------------------------------------------------------

def _add_separators(add, parts: list):
    """1) Ayraçlı biçimler: `aser_bugat`, `aser.bugat`, `aser-bugat`."""
    if len(parts) < 2:
        return
    for sep in ("_", ".", "-"):
        add(sep.join(parts), TIER_SEPARATOR)
    # İkiden fazla kelimede yalnızca ilk ve son kelime de denenir.
    if len(parts) > 2:
        for sep in ("_", ".", "-"):
            add(parts[0] + sep + parts[-1], TIER_SEPARATOR)


def _add_initials(add, parts: list):
    """2) Baş harf kombinasyonları: `abugat`, `aserb`, `a_bugat`, `ab`."""
    if len(parts) < 2:
        return
    first, last = parts[0], parts[-1]
    # Baş harf + soyad  ("Aser Bugat" -> "abugat")
    for sep in ("", "_", ".", "-"):
        add(first[0] + sep + last, TIER_INITIAL)
    # Ad + soyadın baş harfi ("Aser Bugat" -> "aserb")
    for sep in ("", "_", ".", "-"):
        add(first + sep + last[0], TIER_INITIAL)
    # Tüm kelimelerin baş harfleri ("Aser Bugat" -> "ab")
    add("".join(p[0] for p in parts), TIER_INITIAL)


def _add_word_forms(add, parts: list, base: str):
    """3) Kelime kısaltmaları, ters sıra ve tek kelime biçimleri."""
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        # Ters sıra ("Asergat Bugat" -> "bugataser")
        add(last + first, TIER_WORD)
        for sep in ("_", "."):
            add(last + sep + first, TIER_WORD)
        # İlk kelimenin kısaltması + soyad ("asbugat", "asebugat", "as_bugat")
        for cut in (2, 3, 4, 5):
            if len(first) > cut:
                add(first[:cut] + last, TIER_WORD)
                add(first[:cut] + "_" + last, TIER_WORD)
                add(first[:cut] + "." + last, TIER_WORD)
        # Ad + soyadın kısaltması ("aserbug", "aser_bug")
        for cut in (2, 3, 4, 5):
            if len(last) > cut:
                add(first + last[:cut], TIER_WORD)
                add(first + "_" + last[:cut], TIER_WORD)
    # Tek kelimeler ("bugat", "aser") — soyad tek başına yaygın bir kullanımdır.
    for part in parts:
        if len(part) >= 3:
            add(part, TIER_WORD)


def _add_loose(add, parts: list, base: str):
    """4) Gevşek biçimler: harf sırası değişimi, harf düşmesi, kısaltma, ünlü düşürme.

    Sıralama ÖNEMLİDİR: deneme bütçesi tier'lar arasında dönüşümlü
    paylaştırıldığı için, bu tier'dan ilk sıradaki biçim en çok denenendir.
    Bu yüzden gerçekten olası biçimler (harf sırası değişimi) önce eklenir,
    anlaşılması zor biçimler ("aserbugat" -> "srbgt") en sona bırakılır.
    """
    # Komşu harflerin yeri değişmiş biçimler (yazım hatası toleransı).
    # Tüm konumlar denenir: "abugat" -> "baugat", "aubgat", "abuagt"...
    if len(base) >= 4:
        for i in range(len(base) - 1):
            add(base[:i] + base[i + 1] + base[i] + base[i + 2:], TIER_LOOSE)

    # Baştaki/sondaki harfin düşmesi: "abugat" -> "bugat", "abuga"
    if len(base) >= 5:
        add(base[1:], TIER_LOOSE)
        add(base[:-1], TIER_LOOSE)
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        if len(first) >= 3:
            add(first[1:] + last, TIER_LOOSE)
        if len(last) >= 3:
            add(first + last[1:], TIER_LOOSE)

    # Tekrar eden harflerin sadeleşmesi: "ahmett" -> "ahmet"
    collapsed = _collapse_repeats(base)
    if collapsed != base:
        add(collapsed, TIER_LOOSE)

    # Kısaltmalar: yalnızca ilk karakterler
    for cut in (6, 7, 8, 5, 4):
        if len(base) > cut:
            add(base[:cut], TIER_LOOSE)

    # İlk kelimenin kısa kısaltması + son kelime: "aser" -> "ase"/"as" + "bugat"
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        for cut in (4, 3, 2):
            if len(first) >= cut:
                add(first[:cut] + last, TIER_LOOSE)
                add(first[:cut] + "_" + last, TIER_LOOSE)
                add(first[:cut] + "." + last, TIER_LOOSE)

    # Ünlüleri düşürme (klasik kısaltma): "aserbugat" -> "srbgt"
    no_vowels = _drop_vowels(base)
    if no_vowels != base:
        add(no_vowels, TIER_LOOSE)


# ---------------------------------------------------------------------------
# Deneme sırası ve derecelendirme
# ---------------------------------------------------------------------------

def order_for_probing(tiered: list, limit: int = None) -> list:
    """Denenecek biçimleri, tier'lar arasında dönüşümlü (round-robin) seçer.

    Neden gerekli? Tier'a göre düz sıralama yapılırsa, ayraç/baş harf
    biçimleri (ki sayıları fazladır) üst sınırı doldurur ve asıl aranan
    gevşek biçimler (harf sırası değişimi, ünlü düşürme) hiç denenmez.
    Dönüşümlü seçim sayesinde dar bir bütçede bile her aileden örnek denenir.
    """
    by_tier = {}
    for handle, tier in tiered:
        by_tier.setdefault(tier, []).append(handle)

    ordered, index = [], 0
    while True:
        added = False
        for tier in sorted(by_tier):
            bucket = by_tier[tier]
            if index < len(bucket):
                ordered.append(bucket[index])
                added = True
        if not added:
            break
        index += 1
    return ordered[:limit] if limit else ordered


def grade_for_tier(tier) -> str:
    """Bir tier'ın eşleşme derecesi: 'strong' (doğrulanabilir) veya 'lead'."""
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        tier = TIER_LOOSE
    return "strong" if tier <= STRONG_TIER_MAX else "lead"


def tier_label(tier) -> str:
    """Tier'ı insan-okur etikete çevirir."""
    try:
        return TIER_LABELS.get(int(tier), "bilinmeyen biçim")
    except (TypeError, ValueError):
        return "bilinmeyen biçim"
