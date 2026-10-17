"""
Merkezi konfigürasyon.

Önceden config/ altındaki settings.json, user_agents.txt ve
regex_signatures.json dosyaları hiç kullanılmıyordu (değerler koda gömülüydü).
Bu modül onları tek yerden, doğrulamalı biçimde yükler.

Kullanım:
    from config.settings import get_settings
    settings = get_settings()
    settings.timeout, settings.user_agents, settings.regex_signatures
"""

import json
import os

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(CONFIG_DIR)

# Kanonik varsayılanlar (dosya eksik/bozuk olsa bile uygulama çalışır).
DEFAULTS = {
    "app_name": "CHAZ OSINT Enterprise",
    "version": "4.4.0",
    "timeout": 12,           # HTTP okuma zaman aşımı (sn)
    "retries": 2,            # ağ hatalarında yeniden deneme
    "backoff": 0.8,          # üstel geri çekilme tabanı (sn)
    "cache_enabled": True,
    "cache_ttl": 900,        # önbellek ömrü (sn)
    "max_threads": 8,        # paralel iş parçacığı
    "rate_min": 0.35,        # platform istekleri arası min gecikme
    "rate_max": 0.9,
    "ai_threshold": 0.55,    # AI doğrulama karar eşiği
    "max_handle_variants": 12,  # platform başına denenecek kullanıcı adı sayısı
    "max_wall_candidates": 3,   # duvarlı platformda (IG/TikTok) sunulacak aday sayısı
    "loose_variants": True,     # kısaltma / ünlü düşürme / harf sırası varyasyonları
    # Duvarlı platformlar için HERKESE AÇIK doğrulama yolları (TikTok oEmbed,
    # TikTok embed sayfası, Instagram web API'si). Yalnızca pozitif kanıt üretir;
    # kanıt yokluğu "hesap yok" sayılmaz.
    "public_verifiers": True,
    # Instagram web uygulamasının herkese açık uygulama kimliği (gizli değildir).
    "instagram_app_id": "936619743392459",
    # Üçüncü taraf ön yüzler (ör. ["https://imginn.com/{h}/"]). Kapalı gelir:
    # kararlılıkları garanti değildir ve kullanım koşulları siteye bağlıdır.
    "alt_frontends": [],
    # KULLANICI İSTEĞE BAĞLI: kendi oturum çereziyle doğrulama.
    # Çerez gizli bir kimlik bilgisidir; dosyayı sürüm kontrolüne eklemeyin.
    "session_cookie_env": "OSINT_SESSION_COOKIES",
    "session_cookie_file": "config/session_cookies.txt",
    "log_level": "INFO",
}

FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


class Settings:
    """Değişmez (immutable benzeri) ayar nesnesi; özellik erişimi sunar."""

    def __init__(self, overrides: dict = None, config_dir: str = None):
        self.config_dir = config_dir or CONFIG_DIR
        self._data = dict(DEFAULTS)
        self._data.update(self._load_json("settings.json", {}))
        if overrides:
            self._data.update(overrides)
        self._user_agents = self._load_user_agents()
        self._regex_signatures = self._load_json("regex_signatures.json", {})

    # -- Yükleme ------------------------------------------------------------

    def _load_json(self, filename, default):
        path = os.path.join(self.config_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else default
        except Exception:
            return default

    def _load_user_agents(self):
        path = os.path.join(self.config_dir, "user_agents.txt")
        try:
            with open(path, "r", encoding="utf-8") as f:
                agents = [line.strip() for line in f if line.strip()]
            return agents or list(FALLBACK_USER_AGENTS)
        except Exception:
            return list(FALLBACK_USER_AGENTS)

    # -- Erişim -------------------------------------------------------------

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __getattr__(self, item):
        # __getattr__ yalnızca normal erişim başarısız olunca çağrılır.
        data = self.__dict__.get("_data", {})
        if item in data:
            return data[item]
        raise AttributeError(item)

    @property
    def user_agents(self):
        return list(self._user_agents)

    @property
    def regex_signatures(self):
        return dict(self._regex_signatures)

    def as_dict(self):
        return dict(self._data)

    def set(self, key, value):
        """Çalışma anında bir ayarı günceller (diske yazmak için `save()`)."""
        self._data[key] = value
        return value

    def save(self, path: str = None) -> bool:
        """Ayar verisini config/settings.json dosyasına kalıcı olarak yazar."""
        path = path or os.path.join(self.config_dir, "settings.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def describe(self) -> str:
        return (f"{self.get('app_name')} v{self.get('version')} | "
                f"timeout={self.timeout}s, threads={self.max_threads}, "
                f"retries={self.retries}, cache_ttl={self.cache_ttl}s")


_SETTINGS_SINGLETON = None


def get_settings() -> Settings:
    """Süreç genelinde tek ayar nesnesi."""
    global _SETTINGS_SINGLETON
    if _SETTINGS_SINGLETON is None:
        _SETTINGS_SINGLETON = Settings()
    return _SETTINGS_SINGLETON
