import sqlite3
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

class Storage:
    def __init__(self, db_path: str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS seen_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_hash TEXT UNIQUE,
                    source_type TEXT,
                    channel TEXT,
                    url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS digest_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    provider_used TEXT,
                    items_count INTEGER,
                    digest_text TEXT
                );
            """)

    def _hash_item(self, item: Dict[str, Any]) -> str:
        unique_key = item.get("guid") or item.get("url") or item.get("title", "")
        return hashlib.sha256(unique_key.encode("utf-8")).hexdigest()

    def filter_unseen_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        unseen = []
        with self._get_connection() as conn:
            for item in items:
                h = self._hash_item(item)
                cur = conn.execute("SELECT 1 FROM seen_items WHERE item_hash = ?", (h,))
                if not cur.fetchone():
                    item["item_hash"] = h
                    unseen.append(item)
        return unseen

    def mark_items_as_seen(self, items: List[Dict[str, Any]]):
        with self._get_connection() as conn:
            for item in items:
                h = item.get("item_hash") or self._hash_item(item)
                conn.execute(
                    "INSERT OR IGNORE INTO seen_items (item_hash, source_type, channel, url) VALUES (?, ?, ?, ?)",
                    (h, item.get("source_type"), item.get("channel"), item.get("url"))
                )

    def save_digest(self, digest_text: str, provider: str, count: int):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO digest_history (provider_used, items_count, digest_text) VALUES (?, ?, ?)",
                (provider, count, digest_text)
            )

    def set_setting(self, key: str, value: str):
        with self._get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)", (key, value))

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT value FROM user_settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else default
