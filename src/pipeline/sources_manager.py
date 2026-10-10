import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple
import yaml

from src.collectors.telegram_collector import TelegramWebCollector
from src.collectors.rss_collector import RSSCollector

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

async def determine_channel_category_with_ai(username_or_url: str, router) -> str:
    """
    Анализирует последние публикации Telegram-канала через LLM и возвращает
    точную категорию (включая возможность создания новой категории ИИ).
    """
    clean_user = clean_channel_username(username_or_url)
    if not clean_user:
        return "Общее и Новости"

    items = []
    try:
        collector = TelegramWebCollector([{"username": clean_user}])
        items = await collector.fetch_all(limit_per_channel=4)
    except Exception as e:
        logger.warning(f"Ошибка сбора постов для анализа канала @{clean_user}: {e}")

    if not items:
        return "Общее и Новости"

    snippets = []
    for it in items[:4]:
        snippets.append(f"• {it.get('title', '')}\n{it.get('content', '')[:250]}")
    post_samples = "\n---\n".join(snippets)

    prompt = f"""Ты — классификатор контента.
Твоя задача — проанализировать недавние публикации Telegram-канала '@{clean_user}' и определить одну точную, емкую тематическую категорию (2-4 слова на русском языке).

Примеры стандартных категорий:
- Нейросети и ИИ
- DevOps & Self-Hosted
- Разработка и Кодинг
- Hardware & GPU
- Кибербезопасность & SecOps

ВАЖНО: Если контент канала посвящен другой теме, ты ОБЯЗАН создать НОВУЮ точную категорию (например: '3D-печать и DIY', 'GameDev & Unreal', 'Аниме & Мультипликация', 'Финансы и Крипта', 'Биотехнологии', 'Авто & Электрокары').

ПУБЛИКАЦИИ КАНАЛА:
{post_samples}

ОТВЕТ: Напиши ТОЛЬКО название категории (2-4 слова), без лишних слов, без кавычек и точек.
"""
    try:
        res = await router.generate_response(task="chat", user_prompt=prompt)
        if res.get("success"):
            line = res.get("content", "").strip().split("\n")[0].strip(' "\'«»`.*')
            line = re.sub(r'^(?:категория|ответ|тема|category):\s*', '', line, flags=re.IGNORECASE).strip()
            if 2 <= len(line) <= 40:
                return line
    except Exception as e:
        logger.warning(f"Ошибка классификации канала через ИИ: {e}")

    return "Общее и Новости"

async def determine_rss_category_with_ai(url: str, router) -> str:
    """
    Анализирует статьи из RSS через LLM и возвращает категорию.
    """
    clean_url = url.strip()
    items = []
    try:
        collector = RSSCollector([{"url": clean_url, "name": "feed"}])
        items = await collector.fetch_all(limit_per_feed=4)
    except Exception as e:
        logger.warning(f"Ошибка сбора RSS для классификации: {e}")

    if not items:
        return "Общее и Новости"

    snippets = []
    for it in items[:4]:
        snippets.append(f"• {it.get('title', '')}\n{it.get('content', '')[:250]}")
    post_samples = "\n---\n".join(snippets)

    prompt = f"""Ты — классификатор контента.
Проанализируй заголовки статей из ленты '{clean_url}' и определи одну точную категорию (2-4 слова на русском языке).
Ты МОЖЕШЬ придумать НОВУЮ категорию (например: 'Hardware & GPU', 'Нейросети и ИИ', 'DevOps & Linux', 'Наука и Космос').

СТАТЬИ ЛЕНТЫ:
{post_samples}

ОТВЕТ: Напиши ТОЛЬКО название категории (2-4 слова).
"""
    try:
        res = await router.generate_response(task="chat", user_prompt=prompt)
        if res.get("success"):
            line = res.get("content", "").strip().split("\n")[0].strip(' "\'«»`.*')
            line = re.sub(r'^(?:категория|ответ|тема|category):\s*', '', line, flags=re.IGNORECASE).strip()
            if 2 <= len(line) <= 40:
                return line
    except Exception as e:
        logger.warning(f"Ошибка классификации RSS через ИИ: {e}")

    return "Общее и Новости"
