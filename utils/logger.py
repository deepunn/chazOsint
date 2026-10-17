"""
Merkezi loglama altyapısı.

Uygulama genelinde `get_logger(__name__)` ile kullanılır. Loglar
`logs/chaz.log` dosyasına yazılır (dönen dosya, 1 MB, 3 yedek).
Konsol çıktısı varsayılan olarak KAPALIDIR (GUI/CLI gürültüsünü önlemek için);
`CHAZ_LOG_CONSOLE=1` ortam değişkeni ile açılır.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "chaz.log")

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_configured = False


def _configure_root():
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger("chaz")
    root.setLevel(logging.INFO)
    root.propagate = False

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    except Exception:
        pass  # log yazılamasa da uygulama çalışmaya devam eder

    if os.environ.get("CHAZ_LOG_CONSOLE") == "1":
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(console)


def get_logger(name: str) -> logging.Logger:
    """`chaz.<name>` altında bir logger döndürür."""
    _configure_root()
    return logging.getLogger(f"chaz.{name}")
