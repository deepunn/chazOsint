"""
PhotoIdentityAI - Bir fotoğraftan hesap/kişi izleri çıkarır.

Amaç: kullanıcı bir görsel verdiğinde, görselin İÇİNDE taşıdığı kimlik
sinyallerini (meta veri alanları, dosya adı, gömülü URL/e-posta/handle)
çıkarıp mevcut OSINT boru hattına "aranacak hedef" olarak sunmak.

Dürüst sınırlama: Yerel yüz tanıma YAPMAZ (bu, bir yüz veritabanı ve harici
servis gerektirir). Görselden bulunan hesaplar ancak şu durumlarda çıkar:
  * meta veride isim/kullanıcı adı/copyright yazıyorsa,
  * dosya adı bir kullanıcı adı içeriyorsa,
  * görselin içine yazılmış (filigran) bir handle varsa*,
  * ya da ters görsel arama kişiyi buluyorsa (arayüzden tek tıkla açılır).

(*) Gömülü metnin okunması (OCR) isteğe bağlıdır ve ortamda OCR motoru
    yoksa atlanır; bu modül OCR olmadan da meta veri + dosya adından çalışır.
"""

import os
import re

# Kimlik taşıyabilecek EXIF/IPTC etiketleri (tag id -> okunur ad).
IDENTITY_TAGS = {
    0x013B: "Artist",
    0x8298: "Copyright",
    0x010E: "ImageDescription",
    0x9286: "UserComment",
    0x9C9B: "XPTitle",
    0x9C9C: "XPComment",
    0x9C9D: "XPAuthor",
    0x9C9E: "XPKeywords",
    0x9C9F: "XPSubject",
    0xA430: "CameraOwnerName",
    0x0131: "Software",
}

# Bu alanlar düşük değerli (ürün/uygulama adı) olduğu için elenir.
NOISE_VALUES = {
    "adobe", "photoshop", "lightroom", "gimp", "snapseed", "picsart", "canva",
    "windows", "microsoft", "apple", "ios", "android", "google", "samsung",
    "canon", "nikon", "sony", "fujifilm", "panasonic", "olympus", "leica",
    "oneplus", "xiaomi", "huawei", "oppo", "vivo", "pixel", "iphone", "redmi",
    "camera", "image", "photo", "screenshot", "untitled", "img", "dsc", "dcim",
    "edited", "edit", "final", "copy", "download", "wallpaper", "sample",
    "firmware", "version", "ver", "unknown", "user", "admin", "default",
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "you", "your", "www",
    "com", "net", "org", "http", "https", "pic", "pics", "foto", "resim",
}

_HANDLE_RE = re.compile(r"@([A-Za-z0-9_.\-]{2,30})")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")
_MENTION_DOMAINS = {
    "instagram.com": "Instagram", "twitter.com": "Twitter / X", "x.com": "Twitter / X",
    "tiktok.com": "TikTok", "github.com": "GitHub", "youtube.com": "YouTube",
    "soundcloud.com": "SoundCloud", "spotify.com": "Spotify",
    "linkedin.com": "LinkedIn", "twitch.tv": "Twitch", "facebook.com": "Facebook",
}

# Ters görsel arama sayfaları (yerel dosya doğrudan URL ile gönderilemez;
# kullanıcı bu sayfalara görseli sürükleyip bırakır).
# Kimlik alanı kaynakları (token tabanlı kullanıcı adı çıkarımı için).
IDENTITY_SOURCES = set(IDENTITY_TAGS.values())

REVERSE_SEARCH_PAGES = [
    ("Google Lens", "https://lens.google.com/"),
    ("Yandex Görsel", "https://yandex.com/images/"),
    ("Bing Görsel", "https://www.bing.com/visualsearch"),
    ("TinEye", "https://tineye.com/"),
]


class PhotoIdentityAI:
    """Görselden kimlik izleri çıkarır ve aranabilir hedefler önerir."""

    def __init__(self, image_path: str):
        self.image_path = image_path

    # -- Ana akış -----------------------------------------------------------

    def analyze(self) -> dict:
        result = {
            "identity_fields": {},
            "leads": [],
            "suggested_targets": [],
            "gps": None,
            "gps_maps_url": None,
            "reverse_search": list(REVERSE_SEARCH_PAGES),
            "notes": [],
        }
        if not self.image_path or not os.path.exists(self.image_path):
            result["notes"].append("Görsel bulunamadı.")
            return result

        fields = self._read_identity_fields()
        result["identity_fields"] = fields

        gps = self._read_gps()
        if gps:
            result["gps"] = gps
            result["gps_maps_url"] = f"https://www.google.com/maps?q={gps['lat']},{gps['lon']}"

        # 1) Meta veri alanlarından izler
        for tag_name, value in fields.items():
            for lead in self._extract_leads_from_text(value, source=tag_name):
                result["leads"].append(lead)

        # 2) Dosya adından izler
        for lead in self._extract_leads_from_filename():
            result["leads"].append(lead)

        # 3) OCR motoru varsa görsel içindeki yazıyı da oku (isteğe bağlı)
        ocr_text = self._try_ocr()
        if ocr_text:
            result["notes"].append("Görsel içi yazı (OCR) okundu.")
            for lead in self._extract_leads_from_text(ocr_text, source="Görsel içi yazı"):
                result["leads"].append(lead)
        else:
            result["notes"].append(
                "Görsel içi yazı (OCR) atlandı: ortamda OCR motoru yok.")

        result["leads"] = self._dedupe(result["leads"])
        result["suggested_targets"] = self._rank_targets(result["leads"])
        if not result["suggested_targets"]:
            result["notes"].append(
                "Doğrudan kimlik izi yok. 'Ters Görsel Arama' ile kişiyi bulmayı deneyin.")
        return result

    # -- Meta veri okuma ----------------------------------------------------

    def _read_identity_fields(self) -> dict:
        fields = {}
        try:
            from PIL import Image
            with Image.open(self.image_path) as img:
                exif = img.getexif()
                for tag_id, name in IDENTITY_TAGS.items():
                    value = self._decode(exif.get(tag_id))
                    if value:
                        fields[name] = value
                # Alt IFD'lerde (ExifIFD) bulunan alanlar
                try:
                    for ifd_id in (0x8769,):  # ExifIFD
                        sub = exif.get_ifd(ifd_id)
                        for tag_id, name in IDENTITY_TAGS.items():
                            value = self._decode(sub.get(tag_id))
                            if value and name not in fields:
                                fields[name] = value
                except Exception:
                    pass
        except Exception as e:
            fields["_hata"] = str(e)
        return fields

    def _read_gps(self):
        try:
            from PIL import Image
            from PIL.ExifTags import GPSTAGS
            with Image.open(self.image_path) as img:
                exif = img.getexif()
                gps_ifd = exif.get_ifd(0x8825) if hasattr(exif, "get_ifd") else None
                if not gps_ifd:
                    return None
                gps = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
                lat = _gps_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
                lon = _gps_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
                if lat is None or lon is None:
                    return None
                return {"lat": lat, "lon": lon}
        except Exception:
            return None

    @staticmethod
    def _decode(value):
        """EXIF değerini okunur metne çevirir (XP* etiketleri UTF-16LE'dir)."""
        if value is None:
            return None
        if isinstance(value, bytes):
            raw = value
            # UserComment başındaki kodlama damgasını at.
            for marker in (b"ASCII\x00\x00\x00", b"UNICODE\x00", b"8BIT\x00"):
                if raw.startswith(marker):
                    raw = raw[len(marker):]
                    break
            for enc in ("utf-16-le", "utf-8", "latin-1"):
                try:
                    text = raw.decode(enc)
                    if text.count("\x00") > 2:
                        continue
                    cleaned = text.replace("\x00", "").strip()
                    if cleaned:
                        return cleaned
                except Exception:
                    continue
            return None
        text = str(value).replace("\x00", "").strip()
        return text or None

    # -- İz çıkarımı --------------------------------------------------------

    def _extract_leads_from_text(self, text: str, source: str) -> list:
        leads = []
        if not text:
            return leads
        text = str(text)

        for handle in _HANDLE_RE.findall(text):
            if self._is_meaningful(handle):
                leads.append(_lead(handle, "handle", source, 0.95))

        for email in _EMAIL_RE.findall(text):
            local = email.split("@", 1)[0]
            leads.append(_lead(email, "email", source, 0.75))
            if self._is_meaningful(local):
                leads.append(_lead(local, "handle", source, 0.8))

        for url in _URL_RE.findall(text):
            leads.extend(self._leads_from_url(url, source))

        # Şemasız alan adları (örn. "instagram.com/ahmet_yz")
        leads.extend(self._leads_from_bare_domain(text, source))

        # Kimlik alanlarında düz kullanıcı adı (örn. XPAuthor = "ahmet_yz")
        if source in IDENTITY_SOURCES:
            leads.extend(self._leads_from_tokens(text, source))

        # Düz isim: büyük harfle başlayan kelime grupları
        for phrase in re.findall(r"\b[A-ZÇĞİÖŞÜ][\wçğıöşü]+(?:\s+[A-ZÇĞİÖŞÜ][\wçğıöşü]+)+\b", text):
            if not any(n in phrase.lower() for n in NOISE_VALUES):
                words = phrase.split()
                if all(len(w) >= 3 for w in words):
                    leads.append(_lead(phrase, "name", source, 0.55))
        return leads

    def _leads_from_url(self, url: str, source: str) -> list:
        """https://instagram.com/handle/... -> handle (ilk yol parçası)."""
        leads = []
        low = url.lower()
        for domain, platform in _MENTION_DOMAINS.items():
            if domain in low:
                after = low.split(domain, 1)[1].lstrip("/")
                parts = [p for p in after.split("/") if p]
                if parts:
                    handle = parts[0].lstrip("@").split("?")[0].split("#")[0]
                    if self._is_meaningful(handle):
                        leads.append(_lead(handle, "handle", f"{source} ({platform})", 0.9))
                break
        return leads

    def _leads_from_bare_domain(self, text: str, source: str) -> list:
        """Şemasız alan adı geçişleri (örn. 'instagram.com/ahmet_yz')."""
        leads = []
        for domain, platform in _MENTION_DOMAINS.items():
            pattern = re.escape(domain) + r"/([A-Za-z0-9_.@\-]{2,40})"
            for m in re.finditer(pattern, text, re.IGNORECASE):
                handle = m.group(1).lstrip("@")
                if self._is_meaningful(handle):
                    leads.append(_lead(handle, "handle", f"{source} ({platform})", 0.9))
        return leads

    def _leads_from_tokens(self, text: str, source: str) -> list:
        """Kimlik alanındaki düz kullanıcı adlarını yakalar (örn. XPAuthor)."""
        leads = []
        tokens = [t for t in re.split(r"[,\s/|;]+", text) if t]
        single = len(tokens) == 1
        for token in tokens:
            token = token.strip()
            if not re.fullmatch(r"[A-Za-z0-9._\-]{3,40}", token):
                continue
            if not any(c.isalpha() for c in token):   # sürüm/tarih gibi değerleri atla
                continue
            if not self._is_meaningful(token):
                continue
            has_sep = any(c in token for c in "._-")
            has_digit = any(c.isdigit() for c in token)
            if has_sep or has_digit or single:
                leads.append(_lead(token, "handle", source, 0.8))
        return leads

    def _extract_leads_from_filename(self) -> list:
        leads = []
        base = os.path.splitext(os.path.basename(self.image_path or ""))[0]
        if not base:
            return leads
        for token in re.split(r"[_\-\s.]+", base):
            token = token.strip()
            if self._is_meaningful(token) and not token.isdigit():
                leads.append(_lead(token, "handle", "Dosya adı", 0.5))
        return leads

    @staticmethod
    def _is_meaningful(token: str) -> bool:
        if not token:
            return False
        t = token.strip().lower()
        if len(t) < 3 or len(t) > 40:
            return False
        if t in NOISE_VALUES or t in STOPWORDS:
            return False
        if re.fullmatch(r"[\d_\-]+", t):   # sadece sayı/ayraç
            return False
        return True

    # -- Sıralama -----------------------------------------------------------

    @staticmethod
    def _dedupe(leads: list) -> list:
        seen, out = set(), []
        weight = {"handle": 3, "email": 2, "name": 1}
        for lead in leads:
            key = lead["value"].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(lead)
        out.sort(key=lambda x: (weight.get(x["kind"], 0), x["confidence"]), reverse=True)
        return out

    def _rank_targets(self, leads: list, limit: int = 5) -> list:
        def values(group):
            out = []
            for lead in group:
                value = lead["value"]
                if "@" in value:  # e-posta yerine yerel kısmı
                    value = value.split("@", 1)[0]
                if value and value not in out:
                    out.append(value)
            return out

        handles = [l for l in leads if l["kind"] in ("handle", "email")]
        # Meta veri izleri dosya adından daha güvenilirdir.
        from_meta = [l for l in handles if l["source"] != "Dosya adı"]
        targets = values(from_meta) or values(handles)

        # Hiç handle yoksa en güçlü isim adayını kullan.
        if not targets:
            name = next((l["value"] for l in leads if l["kind"] == "name"), None)
            if name:
                targets.append(name)
        return targets[:limit]

    # -- İsteğe bağlı OCR ---------------------------------------------------

    def _try_ocr(self):
        """pytesseract + tesseract kuruluysa görsel içi yazıyı okur; yoksa None."""
        try:
            import pytesseract
            from PIL import Image
            with Image.open(self.image_path) as img:
                return pytesseract.image_to_string(img) or None
        except Exception:
            return None


# --- Yardımcılar ------------------------------------------------------------

def _lead(value, kind, source, confidence):
    return {"value": str(value).strip(), "kind": kind, "source": source,
            "confidence": float(confidence)}


def _gps_decimal(coord, ref):
    if not coord or len(coord) != 3:
        return None
    try:
        def _val(x):
            return float(x) if not isinstance(x, tuple) else x[0] / x[1]
        d, m, s = _val(coord[0]), _val(coord[1]), _val(coord[2])
        decimal = d + m / 60.0 + s / 3600.0
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 6)
    except Exception:
        return None
