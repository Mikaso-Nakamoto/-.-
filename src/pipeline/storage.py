import sqlite3
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import os

class Storage:
    def __init__(self, db_path: str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.learned_pref_file = self.path.parent.parent / "config" / "user_learned_preferences.txt"
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

                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reaction TEXT,
                    context TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS news_feed (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_hash TEXT UNIQUE,
                    source_type TEXT,
                    channel TEXT,
                    category TEXT,
                    title TEXT,
                    content TEXT,
                    url TEXT,
                    published_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    def save_news_items(self, items: List[Dict[str, Any]]):
        with self._get_connection() as conn:
            for it in items:
                h = it.get("item_hash") or self._hash_item(it)
                conn.execute("""
                    INSERT OR IGNORE INTO news_feed 
                    (item_hash, source_type, channel, category, title, content, url, published_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    h,
                    it.get("source_type", "unknown"),
                    it.get("channel", ""),
                    it.get("category", "Общее"),
                    it.get("title", ""),
                    it.get("content", ""),
                    it.get("url", ""),
                    it.get("published_at", "")
                ))

    def get_news_feed(self, limit: int = 40) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT id, channel, category, title, content, url, published_at, created_at FROM news_feed ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            rows = cur.fetchall()
            return [dict(row) for row in rows]

    def save_digest(self, digest_text: str, provider: str, count: int):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO digest_history (provider_used, items_count, digest_text) VALUES (?, ?, ?)",
                (provider, count, digest_text)
            )

    def get_latest_digest(self) -> Optional[str]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT digest_text FROM digest_history ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            return row["digest_text"] if row else None

    # --------------------------------------------------------------------------
    # Настройки и состояние (включая режим чата)
    # --------------------------------------------------------------------------
    def set_setting(self, key: str, value: str):
        with self._get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)", (key, value))

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT value FROM user_settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

    def is_chat_mode_active(self) -> bool:
        return self.get_setting("chat_mode_active", "false") == "true"

    def set_chat_mode(self, active: bool):
        self.set_setting("chat_mode_active", "true" if active else "false")

    # --------------------------------------------------------------------------
    # История диалога
    # --------------------------------------------------------------------------
    def add_chat_message(self, role: str, content: str):
        with self._get_connection() as conn:
            conn.execute("INSERT INTO chat_history (role, content) VALUES (?, ?)", (role, content))

    def get_chat_history(self, limit: int = 8) -> List[Dict[str, str]]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT role, content FROM chat_history ORDER BY id DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
            messages = [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
            return messages

    def clear_chat_history(self):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM chat_history")

    # --------------------------------------------------------------------------
    # Обратная связь и обучаемый профиль предпочтений
    # --------------------------------------------------------------------------
    def record_feedback(self, reaction: str, context: str):
        with self._get_connection() as conn:
            conn.execute("INSERT INTO user_feedback (reaction, context) VALUES (?, ?)", (reaction, context[:400]))

        # Запись в файл user_learned_preferences.txt на сервере
        try:
            self.learned_pref_file.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            sentiment = "НРАВИТСЯ (👍)" if reaction in ["like", "positive", "👍", "🔥", "❤️"] else "НЕ НРАВИТСЯ (👎)"
            clean_context = context.replace("\n", " ")[:200]
            with open(self.learned_pref_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {sentiment}: {clean_context}\n")
        except Exception as e:
            pass

    def get_learned_preferences_summary(self) -> str:
        if not self.learned_pref_file.exists():
            return ""
        try:
            with open(self.learned_pref_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if not lines:
                return ""
            # Берем последние 15 записей
            recent = lines[-15:]
            return "".join(recent).strip()
        except Exception:
            return ""
