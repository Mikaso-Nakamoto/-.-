import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple
import yaml

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SOURCES_FILE = BASE_DIR / "config" / "sources.yaml"
SOURCES_EXAMPLE = BASE_DIR / "config" / "sources.example.yaml"

DEFAULT_CATEGORIES = [
    "Нейросети и ИИ",
    "DevOps & Self-Hosted",
    "Разработка и Кодинг",
    "Hardware & GPU",
    "Общее и Новости"
]

def clean_channel_username(raw: str) -> str:
    """
    Очищает введенную пользователем строку (ссылку или юзернейм) до чистого имени канала.
    Примеры:
      @neuralmeduza -> neuralmeduza
      https://t.me/ai_newz -> ai_newz
      https://t.me/s/habr_com/ -> habr_com
      t.me/proglib -> proglib
    """
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r'^(?:https?://)?(?:www\.)?(?:t\.me/)?(?:s/)?', '', s, flags=re.IGNORECASE)
    s = s.lstrip('@').strip().rstrip('/')
    # Убираем возможные GET-параметры
    s = s.split('?')[0].split('#')[0]
    return s.strip()

def load_sources_data() -> Dict[str, Any]:
    if not SOURCES_FILE.exists():
        if SOURCES_EXAMPLE.exists():
            try:
                content = SOURCES_EXAMPLE.read_text(encoding="utf-8")
                SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
                SOURCES_FILE.write_text(content, encoding="utf-8")
            except Exception as e:
                logger.error(f"Не удалось инициализировать sources.yaml: {e}")
                return {"sources": {"telegram_channels": [], "rss_feeds": []}}
        else:
            return {"sources": {"telegram_channels": [], "rss_feeds": []}}

    try:
        with open(SOURCES_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            if "sources" not in data:
                data["sources"] = {}
            if "telegram_channels" not in data["sources"]:
                data["sources"]["telegram_channels"] = []
            if "rss_feeds" not in data["sources"]:
                data["sources"]["rss_feeds"] = []
            return data
    except Exception as e:
        logger.error(f"Ошибка чтения sources.yaml: {e}")
        return {"sources": {"telegram_channels": [], "rss_feeds": []}}

def save_sources_data(data: Dict[str, Any]) -> bool:
    try:
        SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SOURCES_FILE, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения sources.yaml: {e}")
        return False

def get_telegram_channels() -> List[Dict[str, str]]:
    data = load_sources_data()
    return data.get("sources", {}).get("telegram_channels", [])

def add_telegram_channel(username_or_url: str, category: str = "Общее и Новости") -> Tuple[bool, str]:
    clean_user = clean_channel_username(username_or_url)
    if not clean_user:
        return False, "Некорректный юзернейм или ссылка канала."

    # Проверка формата юзернейма Telegram (буквы, цифры, подчеркивания, от 3 до 35 символов)
    if not re.match(r'^[a-zA-Z0-9_]{3,35}$', clean_user):
        return False, f"Имя канала '<b>{clean_user}</b>' содержит недопустимые символы. Допустимы буквы A-Z, 0-9 и _."

    data = load_sources_data()
    channels = data.setdefault("sources", {}).setdefault("telegram_channels", [])

    # Проверяем на дубликаты
    for ch in channels:
        if ch.get("username", "").lower() == clean_user.lower():
            return False, f"Канал <b>@{clean_user}</b> уже есть в вашем списке источников!"

    channels.append({
        "username": clean_user,
        "category": category.strip() or "Общее и Новости"
    })

    if save_sources_data(data):
        return True, f"Канал <b>@{clean_user}</b> успешно добавлен в категорию «<i>{category}</i>»!"
    else:
        return False, "Ошибка сохранения конфигурации на диске."

def remove_telegram_channel(username_or_url: str) -> Tuple[bool, str]:
    clean_user = clean_channel_username(username_or_url)
    data = load_sources_data()
    channels = data.get("sources", {}).get("telegram_channels", [])

    initial_len = len(channels)
    updated_channels = [ch for ch in channels if ch.get("username", "").lower() != clean_user.lower()]

    if len(updated_channels) == initial_len:
        return False, f"Канал <b>@{clean_user}</b> не найден в списке источников."

    data["sources"]["telegram_channels"] = updated_channels
    if save_sources_data(data):
        return True, f"Канал <b>@{clean_user}</b> успешно удален из источников!"
    else:
        return False, "Ошибка записи конфигурации."

def get_rss_feeds() -> List[Dict[str, str]]:
    data = load_sources_data()
    return data.get("sources", {}).get("rss_feeds", [])

def add_rss_feed(url: str, name: str = "", category: str = "Общее и Новости") -> Tuple[bool, str]:
    clean_url = url.strip()
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        return False, "URL RSS-ленты должен начинаться с http:// или https://"

    data = load_sources_data()
    feeds = data.setdefault("sources", {}).setdefault("rss_feeds", [])

    for f in feeds:
        if f.get("url", "").lower() == clean_url.lower():
            return False, f"RSS-лента <code>{clean_url}</code> уже добавлена!"

    feed_name = name.strip() or clean_url.replace("https://", "").replace("http://", "").split("/")[0]

    feeds.append({
        "url": clean_url,
        "name": feed_name,
        "category": category.strip() or "Общее и Новости"
    })

    if save_sources_data(data):
        return True, f"RSS-лента <b>{feed_name}</b> добавлена в категорию «<i>{category}</i>»!"
    else:
        return False, "Ошибка сохранения конфигурации."

def remove_rss_feed(url_or_name: str) -> Tuple[bool, str]:
    query = url_or_name.strip().lower()
    data = load_sources_data()
    feeds = data.get("sources", {}).get("rss_feeds", [])

    initial_len = len(feeds)
    updated_feeds = [f for f in feeds if f.get("url", "").lower() != query and f.get("name", "").lower() != query]

    if len(updated_feeds) == initial_len:
        return False, f"RSS-лента не найдена."

    data["sources"]["rss_feeds"] = updated_feeds
    if save_sources_data(data):
        return True, "RSS-лента успешно удалена!"
    else:
        return False, "Ошибка сохранения конфигурации."
