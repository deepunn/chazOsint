"""
Platform doğrulama sağlayıcıları (verification providers).

NEDEN GEREKLİ?
--------------
Instagram, TikTok ve LinkedIn profilleri anonim bir HTTP istemcisine
genellikle içerik göstermez: giriş duvarı, WAF meydana okuması (TikTok'ta
`Please wait...`, `slardarWAF`) veya bot koruması (LinkedIn HTTP 999) döner.
Bu durumda "içerik kanıtı yok" bilgisi "hesap yok" DEMEK DEĞİLDİR.

Bu modül, doğrudan HTML yoklaması duvara takıldığında devreye giren
ALTERNATİF doğrulama yollarını toplar:

  1. `TikTokEmbedVerifier`    – TikTok'un resmi embed sayfasındaki kullanıcı
     verisini (`uniqueId`, `nickname`) okur. ÖLÇÜLDÜ (17.10.2026): Chrome
     taklidiyle var olan hesap 200 + nickname, olmayan hesap 503 + 26 bayt
     döndürüyor. Bu, TikTok için anonim ÇALIŞAN tek kanıt yolu.
  2. `TikTokOEmbedVerifier`   – TikTok'un herkese açık oEmbed uç noktası.
     Oturum gerektirmez; profili gömülebilir olan hesaplar için görünen adı
     ve profil URL'sini döndürür. (Bu ağda sık sık 429 döner; sırada sonra.)
  3. `InstagramHtmlVerifier`  – Instagram profil sayfasının `og:title`
     başlığını okur. ÖLÇÜLDÜ: yalnızca Chrome taklidiyle (TLS/HTTP2 parmak
     izi) gerçek profil geldi; bilinen 5 hesapta 5/5 görünen ad çıktı.
     Normal Python istemcisi aynı URL'de duvar/kabuk aldı (og:title yok).
  4. `InstagramProfileApiVerifier` – Instagram web uygulamasının herkese
     açık uygulama kimliğiyle (`x-ig-app-id`) `web_profile_info` ucunu
     dener. ÖLÇÜLDÜ: oturumsuz çağrı artık 401 `require_login` döndürüyor;
     bu yol yalnızca kullanıcının kendi oturumunda işe yarar.
  5. `SessionCookieVerifier`  – KULLANICI İSTEĞE BAĞLI olarak kendi oturum
     çerezini verirse (`config/session_cookies.txt` veya ortam değişkeni),
     aynı uç noktalar o oturumla sorgulanır. Kapalı gelir.
  6. `AltFrontendVerifier`    – Yapılandırılabilir üçüncü taraf ön yüzler.
     Varsayılan olarak KAPALIDIR (boş liste); açılırsa dener.

TEMEL KURAL (yanlış-pozitif/yanlış-negatif koruması):
  * Sağlayıcılar yalnızca KANIT üretir; kanıt yokluğu ASLA "hesap yok"
    sonucuna çevrilmez.
  * `confirmed` -> sayfa hedefe ait olduğu KANITLANDI (görünen ad ile).
  * `rejected`  -> yalnızca sağlayıcı kesin bir "yok" yanıtı verdiğinde
                   (ör. Instagram API'nin gerçek 404'ü).
  * `unknown`   -> ulaşılamadı, engellendi, throttle edildi veya yanıt
                   ayrıştırılamadı. Aday silinmez; "elle kontrol" olarak kalır.
"""

import html
import json
import os
import re
import urllib.parse

from utils.logger import get_logger

log = get_logger("verifiers")

CONFIRMED = "confirmed"
REJECTED = "rejected"
UNKNOWN = "unknown"

# Instagram web uygulamasının herkese açık uygulama kimliği (gizli bir anahtar
# DEĞİLDİR; tarayıcıdaki isteklerde görünür ve birçok açık kaynak projede
# aynı değer kullanılır).
DEFAULT_IG_APP_ID = "936619743392459"

# Mobil/masaüstü tarayıcı benzeri User-Agent: bazı uç noktalar kısa
# User-Agent'ları reddediyor.
_MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
              "Mobile/15E148 Safari/604.1")

# Chrome taklidiyle atılan isteklerde kullanılan başlıklar. `Accept-Language`
# İngilizce tutulur: `og:description` metni ("Followers") böylece sabit olur.
_CHROME_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


class VerificationResult:
    """Bir doğrulama sağlayıcısının sonucu."""

    def __init__(self, status: str, display_name: str = "", evidence: str = "",
                 provider: str = "", profile: dict = None):
        self.status = status
        self.display_name = display_name or ""
        self.evidence = evidence or ""
        self.provider = provider or ""
        self.profile = profile or {}

    @property
    def confirmed(self) -> bool:
        return self.status == CONFIRMED

    @property
    def rejected(self) -> bool:
        return self.status == REJECTED

    def describe(self) -> str:
        label = {CONFIRMED: "doğrulandı", REJECTED: "yok",
                 UNKNOWN: "bilinmiyor"}.get(self.status, self.status)
        extra = f" — {self.evidence}" if self.evidence else ""
        return f"{self.provider}: {label}{extra}"

    def __repr__(self):
        return f"<VerificationResult {self.status} {self.provider} {self.display_name!r}>"


# ---------------------------------------------------------------------------
# Sağlayıcılar
# ---------------------------------------------------------------------------

class BaseVerifier:
    """Tüm sağlayıcıların ortak iskeleti."""

    name = "base"
    supports = ()          # hangi platformlar

    def __init__(self, client, settings=None):
        self.client = client
        self.settings = settings

    def verify(self, platform: str, url: str, handle: str) -> VerificationResult:
        if self.supports and platform not in self.supports:
            return VerificationResult(UNKNOWN, provider=self.name,
                                      evidence="platform desteklenmiyor")
        try:
            return self._verify(platform, url, handle) or VerificationResult(
                UNKNOWN, provider=self.name)
        except Exception as e:      # sağlayıcı hatası asla taramayı bozmaz
            log.debug("%s doğrulama hatası (%s): %s", self.name, url, e)
            return VerificationResult(UNKNOWN, provider=self.name,
                                      evidence=f"hata: {type(e).__name__}")

    def _verify(self, platform, url, handle) -> VerificationResult:
        raise NotImplementedError

    # -- Yardımcılar --------------------------------------------------------

    def settings_timeout(self) -> float:
        return float(getattr(self.settings, "timeout", 12) or 12) if self.settings else 12.0

    @staticmethod
    def _json_from(resp):
        try:
            return json.loads(resp.text or "")
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _clean_handle(value: str) -> str:
        value = (value or "").strip()
        value = value.split("?")[0].rstrip("/")
        return value.split("/")[-1].lstrip("@").lower()

    def _handle_matches(self, handle: str, candidate: str) -> bool:
        return self._clean_handle(handle) == self._clean_handle(candidate)

    @staticmethod
    def _is_blocked(resp) -> bool:
        """Throttle / WAF / bot engeli mi? (uyarı sayfası 'yok' değildir)"""
        if resp.status_code in (429, 503, 999) or resp.status_code == 0:
            return True
        low = (resp.text or "").lower()
        if len(low) < 400 and any(k in low for k in
                                  ("ratelimit", "overload-protect",
                                   "too many requests", "please wait")):
            return True
        return any(k in low for k in ("slardarwaf", "wafchallengeid"))


class TikTokOEmbedVerifier(BaseVerifier):
    """TikTok'un herkese açık oEmbed uç noktası (oturum gerektirmez)."""

    name = "tiktok-oembed"
    supports = ("TikTok",)
    ENDPOINT = "https://www.tiktok.com/oembed?url="

    def _verify(self, platform, url, handle) -> VerificationResult:
        target = f"https://www.tiktok.com/@{self._clean_handle(handle)}"
        query = self.ENDPOINT + urllib.parse.quote(target, safe="")
        resp = self.client.get(query, timeout=self.settings_timeout(),
                               impersonate=True)

        if self._is_blocked(resp):
            return VerificationResult(UNKNOWN, provider=self.name,
                                      evidence=f"uç nokta engelledi (HTTP {resp.status_code})")
        payload = self._json_from(resp)
        if resp.status_code == 200 and isinstance(payload, dict):
            author_url = payload.get("author_url") or ""
            author_name = payload.get("author_name") or ""
            if author_url and self._handle_matches(handle, author_url):
                return VerificationResult(
                    CONFIRMED, display_name=author_name, provider=self.name,
                    evidence=f"oEmbed author_name='{author_name}'",
                    profile={"display_name": author_name, "author_url": author_url})
            return VerificationResult(UNKNOWN, provider=self.name,
                                      evidence="oEmbed yanıtı hedefle eşleşmedi")
        # Var olmayan kullanıcı 404/400 dönebilir; AMA gizli/yaş-kısıtlı
        # hesaplar da gömülemez. Bu yüzden burada "yok" DEMİYORUZ.
        return VerificationResult(
            UNKNOWN, provider=self.name,
            evidence=f"oEmbed sonuç vermedi (HTTP {resp.status_code}) — "
                     "hesap gizli/kısıtlı veya uç nokta throttle'lı olabilir")


class TikTokEmbedVerifier(BaseVerifier):
    """TikTok'un resmi embed sayfasındaki gömülü kullanıcı verisini okur."""

    name = "tiktok-embed"
    supports = ("TikTok",)
    EMBED = "https://www.tiktok.com/embed/@{h}"
    DATA_RE = re.compile(
        r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        re.DOTALL)
    UNIQUE_RE = re.compile(r'"uniqueId"\s*:\s*"([^"]+)"')
    NICKNAME_RE = re.compile(r'"nickname"\s*:\s*"([^"]{1,80})"')

    def _verify(self, platform, url, handle) -> VerificationResult:
        handle = self._clean_handle(handle)
        resp = self.client.get(self.EMBED.format(h=handle),
                              timeout=self.settings_timeout(),
                              impersonate=True, extra_headers=_CHROME_HEADERS)
        body = resp.text or ""

        # POZİTİF KANIT ÖNCE: embed sayfası WAF betiği de içerebiliyor (ölçüldü:
        # sayfada 'slardar' geçiyor ama gerçek nickname/uniqueId de var).
        # Kanıt varsa engel işareti onu geçersiz kılmaz.
        unique_id, nickname = None, ""
        block = self.DATA_RE.search(body)
        if block:
            payload = self._json_from_text(block.group(1))
            user = self._find_user(payload) if payload else None
            if user:
                unique_id = user.get("uniqueId")
                nickname = user.get("nickname") or unique_id or ""
        if unique_id is None:
            unique = self.UNIQUE_RE.search(body)
            if unique:
                unique_id = unique.group(1)
                nick = self.NICKNAME_RE.search(body)
                nickname = nick.group(1) if nick else unique_id

        if unique_id and not self._handle_matches(handle, unique_id):
            return VerificationResult(
                UNKNOWN, provider=self.name,
                evidence=f"embed sayfası başka bir kullanıcı gösteriyor ({unique_id})")
        if unique_id and nickname:
            return VerificationResult(
                CONFIRMED, display_name=nickname, provider=self.name,
                evidence=f"embed verisi: nickname='{nickname}'",
                profile={"display_name": nickname})

        if self._is_blocked(resp):
            # ÖLÇÜLDÜ: var olmayan kullanıcıda da 200/503 + minik gövde gelir;
            # bu kesin "yok" DEĞİLDİR (throttle olabilir), o yüzden aday
            # silinmez, "elle kontrol" olarak kalır.
            return VerificationResult(
                UNKNOWN, provider=self.name,
                evidence=(f"embed ucu bu kullanıcı için veri döndürmedi "
                          f"(HTTP {resp.status_code}, {len(body)} bayt) — "
                          "throttle veya gerçekten hesap yok olabilir"))
        return VerificationResult(
            UNKNOWN, provider=self.name,
            evidence=f"embed sayfasında kullanıcı verisi yok (HTTP {resp.status_code})")

    @staticmethod
    def _json_from_text(text):
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    @classmethod
    def _find_user(cls, node):
        """İç içe JSON içinde kullanıcı sözlüğünü bulur."""
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("user", "userInfo") and isinstance(value, dict):
                    if value.get("uniqueId") or value.get("nickname"):
                        return value
                found = cls._find_user(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = cls._find_user(item)
                if found:
                    return found
        return None


class InstagramHtmlVerifier(BaseVerifier):
    """Instagram profil sayfasının `og:title` başlığından anonim kanıt.

    ÖLÇÜM (17.10.2026, bu ağ):
      * Normal Python istemcisi (`requests`, OpenSSL, HTTP/1.1) -> ~595 KB
        kabuk sayfa, `og:title` YOK. Var olan ve olmayan hesapta aynı.
      * Chrome taklit eden istemci (TLS/JA3 + HTTP/2) -> ~785 KB GERÇEK
        profil sayfası; `og:title` = "Görünen Ad (@kullanıcı) • Instagram...".
        Bilinen 5 hesapta 5/5 isabet, tekrarlanabilir.

    Bu yüzden kanıt ARANIRKEN istek taklitli taşımayla atılır. `og:title`
    içinde hedef kullanıcı adı varsa profil KANITLANMIŞTIR. Yokluğu ise
    "hesap yok" DEĞİLDİR (aynı kabuk var olan hesaba da gelebilir).
    """

    name = "instagram-html"
    supports = ("Instagram",)
    PROFILE = "https://www.instagram.com/{h}/"

    def _verify(self, platform, url, handle) -> VerificationResult:
        handle = self._clean_handle(handle)
        resp = self.client.get(self.PROFILE.format(h=handle),
                              timeout=self.settings_timeout(),
                              impersonate=True, extra_headers=_CHROME_HEADERS)

        if resp.status_code in (404, 410):
            return VerificationResult(
                REJECTED, provider=self.name,
                evidence=f"profil adresi HTTP {resp.status_code} döndürdü")

        body = resp.text or ""
        raw_title = _extract_og_title(body)
        if raw_title and handle and handle in raw_title.lower():
            display = _clean_display_name(raw_title, handle)
            return VerificationResult(
                CONFIRMED, display_name=display, provider=self.name,
                evidence=f"og:title: '{raw_title[:80]}'",
                profile={"display_name": display,
                         "description": _extract_og_description(body)})
        if raw_title:
            return VerificationResult(
                UNKNOWN, provider=self.name,
                evidence=f"sayfa başka bir hesabı gösteriyor ('{raw_title[:60]}')")
        if resp.status_code == 0 or self._is_blocked(resp):
            return VerificationResult(
                UNKNOWN, provider=self.name,
                evidence=f"istemci engellendi (HTTP {resp.status_code})")
        hint = ""
        if not getattr(self.client, "impersonation_available", False):
            hint = (" — Chrome taklidi (TLS/HTTP2 parmak izi) kurulu değil: "
                    "kurulursa bu sayfa gerçek profil verisi döndürebilir "
                    "(pip install curl_cffi)")
        return VerificationResult(
            UNKNOWN, provider=self.name,
            evidence=(f"anonim yanıt profil verisi içermedi "
                      f"({len(body)} bayt kabuk/duvar){hint}"))


class InstagramProfileApiVerifier(BaseVerifier):
    """Instagram web uygulamasının `web_profile_info` ucunu dener.

    Herkese açık uygulama kimliği ile yapılan istek kabul edilirse:
      * var olan kullanıcı  -> HTTP 200 + JSON (full_name, is_private)
      * var olmayan kullanıcı -> HTTP 404
      * oturum isteyen yanıt -> 401/429 (bu durumda sonuç 'bilinmiyor')
    """

    name = "instagram-web-api"
    supports = ("Instagram",)
    ENDPOINTS = ("https://i.instagram.com/api/v1/users/web_profile_info/?username=",
                 "https://www.instagram.com/api/v1/users/web_profile_info/?username=")

    def _verify(self, platform, url, handle) -> VerificationResult:
        handle = self._clean_handle(handle)
        app_id = DEFAULT_IG_APP_ID
        if self.settings is not None:
            app_id = self.settings.get("instagram_app_id", DEFAULT_IG_APP_ID) or DEFAULT_IG_APP_ID
        headers = {"x-ig-app-id": app_id, "User-Agent": _MOBILE_UA,
                   "Accept": "*/*", "X-Requested-With": "XMLHttpRequest"}

        last = None
        for endpoint in self.ENDPOINTS:
            resp = self.client.get(endpoint + urllib.parse.quote(handle),
                                  timeout=self.settings_timeout(),
                                  impersonate=True, extra_headers=headers)
            last = resp
            if self._is_blocked(resp):
                continue
            if resp.status_code in (401, 403):
                continue            # oturum istiyor -> diğer ucu dene
            if resp.status_code == 404:
                return VerificationResult(
                    REJECTED, provider=self.name,
                    evidence="Instagram API: kullanıcı bulunamadı (HTTP 404)")
            payload = self._json_from(resp)
            user = self._extract_user(payload)
            if resp.status_code == 200 and user:
                username = user.get("username") or handle
                if not self._handle_matches(handle, username):
                    return VerificationResult(
                        UNKNOWN, provider=self.name,
                        evidence="API yanıtı başka bir kullanıcıya ait")
                return VerificationResult(
                    CONFIRMED,
                    display_name=user.get("full_name") or "",
                    provider=self.name,
                    evidence=("Instagram API: profil doğrulandı"
                              + (" (gizli hesap)" if user.get("is_private") else "")),
                    profile={"display_name": user.get("full_name") or "",
                             "is_private": bool(user.get("is_private"))})
        return VerificationResult(
            UNKNOWN, provider=self.name,
            evidence=(f"Instagram API yanıt vermedi "
                      f"(HTTP {getattr(last, 'status_code', 0)}) — oturumsuz "
                      "çağrıda 401 `require_login` beklenir; bu yol ancak "
                      "kullanıcının kendi oturum çereziyle çalışır"))

    @staticmethod
    def _extract_user(payload):
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("user"), dict):
            return data["user"]
        if isinstance(payload.get("user"), dict):
            return payload["user"]
        return None


class SessionCookieVerifier(BaseVerifier):
    """KULLANICININ kendi oturum çerezini kullanarak doğrulama (isteğe bağlı).

    Varsayılan olarak kapalıdır. Kullanıcı kendi hesabıyla oturum açtığı
    tarayıcıdan çerezini `config/session_cookies.txt` dosyasına ya da
    `OSINT_SESSION_COOKIES` ortam değişkenine yazarsa bu sağlayıcı devreye
    girer ve duvarlı platformlar gerçekten doğrulanabilir hale gelir.

    Notlar:
      * Çerez GİZLİ bir kimlik bilgisidir; dosya sürüm kontrolüne
        eklenmemelidir (bkz. .gitignore).
      * Yalnızca kullanıcının kendi hesabıyla, kendi isteğiyle yapılır.
    """

    name = "kendi-oturumun"
    supports = ("Instagram", "TikTok", "LinkedIn")

    def __init__(self, client, settings=None, cookie_header: str = None):
        super().__init__(client, settings)
        self.cookie_header = cookie_header or self._load_cookie_header()

    def _load_cookie_header(self) -> str:
        if self.settings is None:
            return ""
        env_name = self.settings.get("session_cookie_env", "OSINT_SESSION_COOKIES")
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
        path = self.settings.get("session_cookie_file", "")
        if not path:
            return ""
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    @property
    def enabled(self) -> bool:
        return bool(self.cookie_header)

    def _verify(self, platform, url, handle) -> VerificationResult:
        if not self.enabled:
            return VerificationResult(UNKNOWN, provider=self.name,
                                      evidence="oturum çerezi verilmedi")
        resp = self.client.get(url, timeout=self.settings_timeout(),
                              impersonate=True,
                              extra_headers={"Cookie": self.cookie_header})
        if resp.status_code in (404, 410):
            return VerificationResult(REJECTED, provider=self.name,
                                      evidence=f"oturumla alınan yanıt: HTTP {resp.status_code}")
        body = resp.text or ""
        low = body.lower()
        handle_low = self._clean_handle(handle)
        for marker in ("sorry, this page isn't available", "page not found",
                       "couldn't find this account"):
            if marker in low:
                return VerificationResult(REJECTED, provider=self.name,
                                          evidence=f"oturumla alınan yanıt: '{marker}'")
        if 'property="og:title"' in low or "og:title" in low:
            raw = _extract_og_title(body)
            if raw and handle_low and handle_low in raw.lower():
                display = _clean_display_name(raw, handle_low)
                return VerificationResult(
                    CONFIRMED, display_name=display, provider=self.name,
                    evidence="oturumlu sayfada görünen ad okundu",
                    profile={"display_name": display})
        return VerificationResult(UNKNOWN, provider=self.name,
                                  evidence="oturumlu yanıt yorumlanamadı")


class AltFrontendVerifier(BaseVerifier):
    """Yapılandırılabilir üçüncü taraf ön yüzler (VARSAYILAN OLARAK KAPALI).

    `settings.json` -> `alt_frontends`: ["https://imginn.com/{h}/", ...]
    Üçüncü taraf sitelerdir; kararlılıkları ve kullanım koşulları garanti
    değildir. Yalnızca kullanıcı açıkça etkinleştirirse denenir.
    """

    name = "alternatif-on-yuz"
    supports = ("Instagram", "TikTok")

    def __init__(self, client, settings=None, templates=None):
        super().__init__(client, settings)
        if templates is None and settings is not None:
            templates = settings.get("alt_frontends", []) or []
        self.templates = list(templates or [])

    def _verify(self, platform, url, handle) -> VerificationResult:
        if not self.templates:
            return VerificationResult(UNKNOWN, provider=self.name,
                                      evidence="yapılandırılmış ön yüz yok")
        handle = self._clean_handle(handle)
        for template in self.templates:
            target = template.format(h=handle, platform=platform.lower())
            resp = self.client.get(target, timeout=self.settings_timeout())
            if self._is_blocked(resp):
                continue
            low = (resp.text or "").lower()
            if resp.status_code == 404 or any(k in low for k in
                                              ("not found", "no user", "bulunamadı")):
                return VerificationResult(REJECTED, provider=self.name,
                                          evidence=f"{template}: profil yok")
            if resp.status_code == 200 and handle in low:
                display = _extract_og_title(resp.text or "")
                return VerificationResult(
                    CONFIRMED, display_name=display, provider=self.name,
                    evidence=f"{template}: profil sayfası okundu",
                    profile={"display_name": display})
        return VerificationResult(UNKNOWN, provider=self.name,
                                  evidence="ön yüzlerden yanıt alınamadı")


# ---------------------------------------------------------------------------
# Toplayıcı
# ---------------------------------------------------------------------------

class VerifierRegistry:
    """Etkin sağlayıcıları sırayla dener; ilk KESİN sonucu döndürür.

    Sıra önemlidir: resmi ve herkese açık uç noktalar önce, üçüncü taraf
    ön yüzler en sonda. 'Bilinmiyor' sonucu bir sonraki sağlayıcıyı dener.
    """

    def __init__(self, client, settings=None):
        self.client = client
        self.settings = settings
        self.providers = []
        if settings is None or settings.get("public_verifiers", True):
            # Sıra ÖLÇÜME dayanır: kanıt ürettiği doğrulanmış yollar önce.
            self.providers.extend([
                TikTokEmbedVerifier(client, settings),
                InstagramHtmlVerifier(client, settings),
                TikTokOEmbedVerifier(client, settings),
                InstagramProfileApiVerifier(client, settings),
            ])
        self.session = SessionCookieVerifier(client, settings)
        if self.session.enabled:
            self.providers.append(self.session)
        alt = AltFrontendVerifier(client, settings)
        if alt.templates:
            self.providers.append(alt)

    @property
    def session_enabled(self) -> bool:
        return self.session.enabled

    def verify(self, platform: str, url: str, handle: str) -> VerificationResult:
        """Platformu, destekleyen sağlayıcılarla doğrulamayı dener."""
        attempted = []
        for provider in self.providers:
            if provider.supports and platform not in provider.supports:
                continue
            result = provider.verify(platform, url, handle)
            attempted.append(result)
            if result.confirmed or result.rejected:
                return result
        if not attempted:
            return VerificationResult(UNKNOWN, provider="yok",
                                      evidence="bu platform için sağlayıcı tanımlı değil")
        # En açıklayıcı sonucu (son denemeyi) raporla; kanıt metinleri hangi
        # sağlayıcıdan geldikleriyle birlikte yazılır (günlük dürüst kalsın).
        last = attempted[-1]
        parts = [f"{r.provider}: {r.evidence}" for r in attempted if r.evidence]
        last.evidence = "; ".join(parts) or last.evidence
        return last


def _extract_meta(page: str, prop: str) -> str:
    """Bir `<meta property=...>` içeriğini (HTML varlıkları çözülmüş) döndürür."""
    match = re.search(r'<meta[^>]+property="%s"[^>]+content="([^"]{1,400})"' % prop,
                      page, re.IGNORECASE)
    if not match:
        match = re.search(r'<meta[^>]+content="([^"]{1,400})"[^>]+property="%s"' % prop,
                          page, re.IGNORECASE)
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()


def _extract_og_title(page: str) -> str:
    """`og:title` içeriğini çıkarır ve platform ekini temizler.

    Kullanıcı adını KORUR ("Ad Soyad (@handle)"): kanıt kontrolü onu arar.
    Görünen ad için `_clean_display_name` kullanılır.
    """
    title = _extract_meta(page, "og:title")
    if not title:
        return ""
    # "... • Instagram fotoğrafları ve videoları" / "... · Instagram" ekini at.
    title = re.sub(r"\s*[•·|]\s*(Instagram|TikTok|LinkedIn)\b.*$", "", title,
                   flags=re.IGNORECASE)
    title = re.sub(r"\s*[-–—]\s*(Instagram|TikTok|LinkedIn)\b.*$", "", title,
                   flags=re.IGNORECASE)
    return title.strip()


def _extract_og_description(page: str) -> str:
    return _extract_meta(page, "og:description")[:200]


def _clean_display_name(title: str, handle: str = "") -> str:
    """`og:title`dan GÖRÜNEN ADI çıkarır.

    "National Geographic (@natgeo) • Instagram fotoğrafları ve videoları"
    -> "National Geographic"
    """
    name = html.unescape(title or "").strip()
    if handle:
        name = re.sub(r"\s*[\[\uff08(]\s*@?\s*" + re.escape(handle) + r"\s*[\]\uff09)]",
                      "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*[•·|]\s*(Instagram|TikTok|LinkedIn)\b.*$", "", name,
                  flags=re.IGNORECASE)
    name = re.sub(r"\s*[-–—]\s*(Instagram|TikTok|LinkedIn)\b.*$", "", name,
                  flags=re.IGNORECASE)
    name = name.strip(" \t-–—•·|")
    # Ad hiç kalmadıysa (yalnızca kullanıcı adı vardıysa) onu kullan.
    return name or (handle or "")
