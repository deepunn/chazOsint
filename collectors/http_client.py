"""
Dayanıklı HTTP istemcisi.

Özellikler:
  * Tekil `requests.Session` (bağlantı havuzu / keep-alive)
  * Ağ hatalarında ve 429/5xx durumlarında üstel geri çekilmeli yeniden deneme
  * İsteğe bağlı TTL'li disk önbelleği (tekrarlı taramaları hızlandırır)
  * config/user_agents.txt üzerinden UA rotasyonu
  * İSTEĞE BAĞLI Chrome taklidi (`impersonate=True`): TLS/JA3 + HTTP/2
    parmak izi tarayıcıya benzetilir. `curl_cffi` (libcurl-impersonate'in
    Python bağlaması) kuruluysa kullanılır; kurulu değilse sessizce normal
    oturuma düşülür ve hiçbir şey bozulmaz.

NEDEN GEREKLİ (ölçüldü, 17.10.2026):
  Instagram `instagram.com/{kullanıcı}/` sayfasını normal bir Python
  istemcisine (OpenSSL, HTTP/1.1) kabuk/duvar olarak döndürüyor
  (~595 KB, og:title YOK). Aynı URL, Chrome taklit eden istemciyle
  GERÇEK profil verisini veriyor (~785 KB, og:title = görünen ad).
  Bilinen 5 hesapta 5/5 isabet; duvar varyantında hiç bilgi yok.
  Yani fark yaratan şey dil değil, TLS/HTTP2 parmak izi.
"""

import hashlib
import random
import time

import requests

from config.settings import get_settings
from utils.cache import DiskCache
from utils.logger import get_logger

try:                      # isteğe bağlı bağımlılık: kurulu olmayabilir
    from curl_cffi import requests as _curl_requests
except Exception:         # pragma: no cover - kurulum ortamına bağlı
    _curl_requests = None

log = get_logger("http")

# Chrome taklidi için kullanılan UA (taklit edilen TLS profiliyle UYUMLU
# olmalıdır: Firefox/Safari UA'sı + Chrome TLS parmak izi tutarsızlık olur).
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# Yeniden deneme sırasında yakalanacak istemci hataları.
_CLIENT_ERRORS = (requests.RequestException,)
if _curl_requests is not None:      # pragma: no cover - bağımlılığa bağlı
    _curl_error = getattr(getattr(_curl_requests, "errors", None),
                          "RequestsError", None)
    if isinstance(_curl_error, type) and issubclass(_curl_error, Exception):
        _CLIENT_ERRORS = _CLIENT_ERRORS + (_curl_error,)


def make_impersonating_session():
    """Chrome taklit eden bir oturum döndürür; mümkün değilse None."""
    if _curl_requests is None:
        return None
    try:
        return _curl_requests.Session(impersonate="chrome")
    except Exception as e:  # pragma: no cover - sürüm/ikili uyumsuzluğu
        log.debug("Chrome taklidi başlatılamadı: %s", e)
        return None


class HttpResponse:
    """`requests.Response`'un ihtiyaç duyulan alt kümesi + önbellek bilgisi."""

    def __init__(self, status_code: int, text: str, url: str, from_cache: bool = False):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.from_cache = from_cache

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def __repr__(self):
        return f"<HttpResponse {self.status_code} {self.url} cache={self.from_cache}>"


class HttpClient:
    """Yeniden denemeli, önbellekli HTTP GET istemcisi."""

    RETRY_STATUS = {429, 500, 502, 503, 504}

    # Çok büyük gövdeleri önbelleğe almayız (önbellek dosyası şişmesin).
    MAX_CACHE_BYTES = 512 * 1024

    def __init__(self, settings=None, cache: DiskCache = None, session=None,
                 allow_impersonation: bool = None):
        self.settings = settings or get_settings()
        self.session = session or requests.Session()
        # Özel (test) oturumu verildiğinde taklit KAPALIDIR: testler ağa
        # çıkmamalıdır. Üretimde ise taklitli oturum, kuruluysa hazırlanır.
        if allow_impersonation is None:
            allow_impersonation = session is None
        self.impersonating_session = (make_impersonating_session()
                                      if allow_impersonation and session is None else None)
        if cache is None:
            cache = DiskCache(ttl=self.settings.cache_ttl,
                              enabled=self.settings.cache_enabled)
        self.cache = cache

    # -- Yardımcılar --------------------------------------------------------

    @property
    def impersonation_available(self) -> bool:
        """Chrome taklidi kullanılabilir mi (curl_cffi kurulu mu)?"""
        return self.impersonating_session is not None

    def headers(self, impersonate: bool = False) -> dict:
        agents = self.settings.user_agents
        if impersonate:
            # Taklit edilen profil Chrome'dur: UA de Chrome olmalı.
            chrome_agents = [a for a in agents if "chrome" in a.lower()]
            agent = random.choice(chrome_agents or [CHROME_UA])
        else:
            agent = random.choice(agents)
        return {
            "User-Agent": agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
        }

    def _cache_key(self, url: str, extra_headers: dict = None,
                   impersonate: bool = False) -> str:
        key = url
        if extra_headers:
            # Ek başlıklar (ör. Instagram uygulama kimliği) yanıtı
            # değiştirebilir; önbellek anahtarına da yansımalıdır.
            key += "|" + "&".join(f"{k}={v}" for k, v in sorted(extra_headers.items()))
        if impersonate:
            # Taklitli ve taklitsiz yanıtlar AYNI DEĞİLDİR (Instagram'da biri
            # gerçek profil, diğeri duvar döner); önbellekte karışmamalıdır.
            key += "|impersonate=chrome"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()

    # -- Ana istek ----------------------------------------------------------

    def get(self, url: str, timeout: float = None, allow_redirects: bool = True,
            use_cache: bool = True, extra_headers: dict = None,
            impersonate: bool = False) -> HttpResponse:
        """GET isteği.

        `impersonate=True` -> Chrome TLS/HTTP2 parmak iziyle istek atılır
        (curl_cffi kuruluysa). Duvarlı platformlarda içerik farkı YARATIR;
        kurulu değilse sessizce normal oturuma düşer.
        """
        session = self.session
        if impersonate and self.impersonating_session is not None:
            session = self.impersonating_session
        impersonating = session is not self.session

        cache_key = self._cache_key(url, extra_headers, impersonate=impersonating)
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached:
                log.debug("cache hit: %s", url)
                return HttpResponse(cached["status"], cached["text"], url, from_cache=True)

        timeout = timeout or self.settings.timeout
        retries = int(self.settings.retries)
        backoff = float(self.settings.backoff)
        last_error = None

        headers = self.headers(impersonate=impersonating)
        if extra_headers:
            headers.update(extra_headers)

        for attempt in range(retries + 1):
            try:
                resp = session.get(url, headers=headers,
                                   timeout=timeout, allow_redirects=allow_redirects)
                if resp.status_code in self.RETRY_STATUS and attempt < retries:
                    time.sleep(backoff * (2 ** attempt))
                    continue
                result = HttpResponse(resp.status_code, resp.text or "",
                                      getattr(resp, "url", url))
                if result.ok and use_cache and len(result.text) <= self.MAX_CACHE_BYTES:
                    self.cache.set(cache_key,
                                   {"status": result.status_code, "text": result.text})
                return result
            except _CLIENT_ERRORS as e:
                last_error = e
                log.debug("istek hatası (%s/%s) %s: %s", attempt + 1, retries + 1, url, e)
                if attempt < retries:
                    time.sleep(backoff * (2 ** attempt))

        log.warning("istek başarısız: %s (%s)", url, last_error)
        return HttpResponse(0, "", url)

    def close(self):
        for session in (self.session, self.impersonating_session):
            if session is None:
                continue
            try:
                session.close()
            except Exception:
                pass
