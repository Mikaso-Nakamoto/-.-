import logging
import re
from typing import List, Dict, Any
from datetime import datetime
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Словарь известных инвайт-хэшей в публичные каналы
INVITE_MAP: Dict[str, str] = {
    "+ieit_mggnzzkmwiy": "it_shelter",
    "ieit_mggnzzkmwiy": "it_shelter",
    "+taijojwcparjotcy": "github_radar",
    "taijojwcparjotcy": "github_radar",
}

def resolve_channel_username(raw: str) -> str:
    """
    Приводит юзернейм или инвайт-ссылку к каноническому имени публичного канала.
    """
    if not raw:
        return ""
    cleaned = raw.strip()
    # Убираем ссылки с telega.in
    cleaned = re.sub(r'^(?:https?://)?(?:www\.)?telega\.in/channels/', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'/card/?$', '', cleaned, flags=re.IGNORECASE)
    # Убираем префиксы t.me, s/ и joinchat/
    cleaned = re.sub(r'^(?:https?://)?(?:www\.)?(?:t\.me/)?(?:s/)?(?:joinchat/)?', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.lstrip('@').strip().rstrip('/')
    cleaned = cleaned.split('?')[0].split('#')[0]

    cleaned_lower = cleaned.lower()
    if cleaned_lower in INVITE_MAP:
        return INVITE_MAP[cleaned_lower]
    if f"+{cleaned_lower}" in INVITE_MAP:
        return INVITE_MAP[f"+{cleaned_lower}"]
    return cleaned

class TelegramWebCollector:
    """
    Сборщик публичных сообщений из Telegram-каналов через официальный Web Preview (t.me/s/channel).
    Не требует регистрации сессии, номеров телефонов и API ID. Не приводит к блокировкам.
    """
    def __init__(self, channels: List[Dict[str, str]]):
        self.channels = channels
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
        }

    async def fetch_channel(self, client: httpx.AsyncClient, channel_info: Dict[str, str], limit: int = 10) -> List[Dict[str, Any]]:
        raw_user = channel_info.get("username", "").strip()
        username = resolve_channel_username(raw_user)
        category = channel_info.get("category", "Общее")
        if not username:
            return []

        target_user = username

        # Если передан иной инвайт-хэш (+...), пробуем разрешить его в публичный канал
        if target_user.startswith("+"):
            try:
                invite_url = f"https://t.me/{target_user}"
                r_invite = await client.get(invite_url, headers=self.headers, timeout=8.0, follow_redirects=True)
                if r_invite.status_code == 200:
                    m = re.search(r'tg://resolve\?domain=([a-zA-Z0-9_]+)', r_invite.text)
                    if m and not m.group(1).lower().startswith(("joinchat", "+")):
                        target_user = m.group(1)
                        INVITE_MAP[username.lower()] = target_user
                        logger.info(f"Инвайт {username} успешно разрешен в публичный канал @{target_user}")
                    else:
                        m2 = re.search(r'<meta property="og:url" content="https?://t\.me/([a-zA-Z0-9_]+)"', r_invite.text)
                        if m2 and not m2.group(1).lower().startswith(("joinchat", "+")):
                            target_user = m2.group(1)
                            INVITE_MAP[username.lower()] = target_user
                            logger.info(f"Инвайт {username} разрешен через og:url в @{target_user}")
                        else:
                            # Пробуем по заголовку канала (например IT_Shelter -> it_shelter)
                            m3 = re.search(r'<meta property="og:title" content="([^"]+)"', r_invite.text)
                            if m3:
                                cand = re.sub(r'[^a-zA-Z0-9_]', '', m3.group(1).strip().lower().replace(" ", "_"))
                                if 3 <= len(cand) <= 32:
                                    target_user = cand
                                    INVITE_MAP[username.lower()] = target_user
                                    logger.info(f"Инвайт {username} разрешен по названию страницы в @{target_user}")
            except Exception as e:
                logger.debug(f"Не удалось разрешить инвайт {username}: {e}")

        if target_user.startswith("+"):
            logger.info(f"Канал {username} является закрытой инвайт-ссылкой без публичного Web-превью.")
            return []

        url = f"https://t.me/s/{target_user}"
        items = []

        try:
            resp = await client.get(url, headers=self.headers, timeout=10.0)
            if resp.status_code != 200:
                logger.warning(f"Канал @{username} вернул HTTP {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            messages = soup.find_all("div", class_="tgme_widget_message_wrap")

            for wrap in messages[-limit:]:
                msg_div = wrap.find("div", class_="tgme_widget_message")
                if not msg_div:
                    continue

                # Ссылка на пост
                post_link = ""
                data_post = msg_div.get("data-post")
                if data_post:
                    post_link = f"https://t.me/{data_post}"

                # Дата публикации
                time_tag = msg_div.find("time", class_="time")
                published_at = time_tag.get("datetime") if time_tag else datetime.utcnow().isoformat()

                # Текст сообщения
                text_div = msg_div.find("div", class_="tgme_widget_message_text")
                if not text_div:
                    continue

                text = text_div.get_text(separator="\n").strip()
                if not text or len(text) < 15:
                    continue

                # 1. Поиск ссылок на YouTube в тексте публикации
                yt_match = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)[a-zA-Z0-9_-]+)', text)
                youtube_url = yt_match.group(1) if yt_match else ""

                # 2. Извлечение прикрепленного изображения или обложки видео
                image_url = ""
                video_url = ""

                # А. Поиск background-image: url(...) среди всех элементов блока (фото, видео-превью, превью ссылок)
                for elem in msg_div.find_all(style=True):
                    st = elem.get("style", "")
                    if "url(" in st:
                        m = re.search(r"url\(['\"]?(https?://[^'\"\)]+)['\"]?\)", st)
                        if m:
                            cand = m.group(1)
                            classes = elem.get("class", [])
                            cls_str = " ".join(classes) if isinstance(classes, list) else str(classes)
                            if "user_photo" not in cls_str and "avatar" not in cls_str:
                                image_url = cand
                                break

                # Б. Поиск тегов <img>
                if not image_url:
                    for img in msg_div.find_all("img"):
                        src = img.get("src", "")
                        classes = img.get("class", [])
                        cls_str = " ".join(classes) if isinstance(classes, list) else str(classes)
                        if src.startswith("http") and "user_photo" not in cls_str and "avatar" not in cls_str:
                            image_url = src
                            break

                # В. Поиск тегов <video>
                for vid in msg_div.find_all("video"):
                    if not image_url and vid.get("poster"):
                        image_url = vid["poster"]
                    if vid.get("src"):
                        video_url = vid["src"]

                items.append({
                    "source_type": "telegram",
                    "channel": f"@{username}",
                    "category": category,
                    "title": text[:80] + ("..." if len(text) > 80 else ""),
                    "content": text,
                    "url": post_link,
                    "image_url": image_url,
                    "video_url": video_url,
                    "youtube_url": youtube_url,
                    "published_at": published_at,
                    "guid": post_link or f"{username}_{published_at}"
                })

        except Exception as e:
            logger.error(f"Ошибка при сборе из @{username}: {e}")

        return items

    async def fetch_all(self, limit_per_channel: int = 8) -> List[Dict[str, Any]]:
        results = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for channel in self.channels:
                items = await self.fetch_channel(client, channel, limit=limit_per_channel)
                results.extend(items)
        return results
