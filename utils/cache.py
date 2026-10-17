"""
Basit, iş parçacığı güvenli, TTL'li disk önbelleği.

Aynı URL'nin kısa süre içinde tekrar tekrar çekilmesini engeller; tekrarlı
taramaları belirgin biçimde hızlandırır ve platformları gereksiz yormaz.
"""

import json
import os
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CACHE_PATH = os.path.join(_ROOT, "database", "http_cache.json")


class DiskCache:
    """Anahtar -> değer eşlemesini TTL ile diskte tutar."""

    def __init__(self, path: str = None, ttl: int = 900, enabled: bool = True):
        self.path = path or DEFAULT_CACHE_PATH
        self.ttl = int(ttl)
        self.enabled = bool(enabled)
        self._lock = threading.Lock()
        self._data = self._load()

    # -- Dosya işlemleri ----------------------------------------------------

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _flush(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    # -- API ----------------------------------------------------------------

    def get(self, key: str):
        if not self.enabled:
            return None
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            if time.time() - entry.get("t", 0) > self.ttl:
                self._data.pop(key, None)
                return None
            return entry.get("v")

    def set(self, key: str, value):
        if not self.enabled:
            return
        with self._lock:
            self._data[key] = {"t": time.time(), "v": value}
            self._flush()

    def clear(self):
        with self._lock:
            self._data = {}
            self._flush()

    def prune(self):
        """Süresi geçmiş kayıtları temizler."""
        now = time.time()
        with self._lock:
            stale = [k for k, e in self._data.items() if now - e.get("t", 0) > self.ttl]
            for k in stale:
                self._data.pop(k, None)
            if stale:
                self._flush()
        return len(stale)

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._data), "ttl": self.ttl, "enabled": self.enabled,
                    "path": self.path}
