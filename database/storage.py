"""
Yerel SQLite depolama katmanı.

İçerik:
  * investigations     -> tarama geçmişi (hedef, rapor, güven, doğrulanan sayısı)
  * image_fingerprints -> görsel algısal hash (dHash) kayıtları; aynı görselin
                          farklı hedeflerde tekrar kullanımını tespit eder.

Eski veritabanı dosyalarıyla uyumludur: eksik sütunlar çalışma anında
otomatik eklenir (basit şema migrasyonu).
"""

import os
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(_ROOT, "database", "osint_storage.db")


class DatabaseManager:
    """Yerel SQLite tabanlı arama/rapor ve görsel parmak izi arşivi."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    # -- Bağlantı / şema ----------------------------------------------------

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS investigations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_name TEXT,
                    report_data TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Şema migrasyonu: eksik sütunları ekle.
            self._ensure_column(cur, "investigations", "confidence", "REAL DEFAULT 0")
            self._ensure_column(cur, "investigations", "verified_count", "INTEGER DEFAULT 0")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS image_fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phash TEXT NOT NULL,
                    target TEXT,
                    path TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_fp_phash ON image_fingerprints(phash)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_inv_target ON investigations(target_name)")
            conn.commit()

    @staticmethod
    def _ensure_column(cur, table: str, column: str, decl: str):
        cur.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cur.fetchall()}
        if column not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # -- Tarama geçmişi -----------------------------------------------------

    def save_scan(self, target_name: str, report_data: str,
                  confidence: float = 0.0, verified_count: int = 0) -> int:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO investigations
                   (target_name, report_data, confidence, verified_count)
                   VALUES (?, ?, ?, ?)""",
                (target_name, report_data, float(confidence), int(verified_count)))
            conn.commit()
            return cur.lastrowid

    # Geriye dönük uyumluluk
    def save_report(self, target_name: str, report_data: str):
        return self.save_scan(target_name, report_data)

    def recent_scans(self, limit: int = 20) -> list:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT id, target_name, confidence, verified_count, timestamp
                   FROM investigations ORDER BY id DESC LIMIT ?""", (int(limit),))
            return [{"id": r[0], "target": r[1], "confidence": r[2],
                     "verified_count": r[3], "timestamp": r[4]} for r in cur.fetchall()]

    def get_scan(self, scan_id: int):
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT id, target_name, report_data, confidence,
                                  verified_count, timestamp
                           FROM investigations WHERE id = ?""", (int(scan_id),))
            row = cur.fetchone()
            if not row:
                return None
            return {"id": row[0], "target": row[1], "report": row[2],
                    "confidence": row[3], "verified_count": row[4], "timestamp": row[5]}

    # -- Görsel parmak izleri ----------------------------------------------

    def save_fingerprint(self, phash: str, target: str, path: str = None) -> int:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO image_fingerprints (phash, target, path) VALUES (?, ?, ?)",
                (phash, target, path))
            conn.commit()
            return cur.lastrowid

    def find_fingerprint(self, phash: str) -> list:
        """Aynı görselin daha önce hangi hedeflerle görüldüğünü döndürür."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT target, path, timestamp FROM image_fingerprints
                   WHERE phash = ? ORDER BY id DESC""", (phash,))
            return [{"target": r[0], "path": r[1], "timestamp": r[2]}
                    for r in cur.fetchall()]

    def stats(self) -> dict:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM investigations")
            scans = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM image_fingerprints")
            fingerprints = cur.fetchone()[0]
        return {"scans": scans, "fingerprints": fingerprints, "path": self.db_path}

    # -- Bakım / temizlik ---------------------------------------------------

    def delete_scan(self, scan_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM investigations WHERE id = ?", (int(scan_id),))
            conn.commit()
            return cur.rowcount > 0

    def clear_history(self) -> int:
        """Tüm tarama geçmişini siler; silinen kayıt sayısını döndürür."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM investigations")
            conn.commit()
            return cur.rowcount

    def clear_fingerprints(self) -> int:
        """Tüm görsel parmak izlerini siler; silinen kayıt sayısını döndürür."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM image_fingerprints")
            conn.commit()
            return cur.rowcount
