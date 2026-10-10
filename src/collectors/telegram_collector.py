import logging
import re
from typing import List, Dict, Any
from datetime import datetime
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

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
        username = channel_info.get("username", "").lstrip("@").strip()
        category = channel_info.get("category", "Общее")
        if not username:
            return []

        target_user = username
        # Если передан инвайт-хэш (+...), пробуем разрешить его в публичный канал
        if target_user.startswith("+"):
            try:
                invite_url = f"https://t.me/{target_user}"
                r_invite = await client.get(invite_url, headers=self.headers, timeout=8.0, follow_redirects=True)
                if r_invite.status_code == 200:
                    m = re.search(r'tg://resolve\?domain=([a-zA-Z0-9_]+)', r_invite.text)
                    if m and not m.group(1).lower().startswith(("joinchat", "+")):
                        target_user = m.group(1)
                        logger.info(f"Инвайт {username} успешно разрешен в публичный канал @{target_user}")
                    else:
                        m2 = re.search(r'<meta property="og:url" content="https?://t\.me/([a-zA-Z0-9_]+)"', r_invite.text)
                        if m2 and not m2.group(1).lower().startswith(("joinchat", "+")):
                            target_user = m2.group(1)
                            logger.info(f"Инвайт {username} разрешен через og:url в @{target_user}")
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

                # Извлечение прикрепленного изображения
                image_url = ""
                photo_wrap = msg_div.find("a", class_="tgme_widget_message_photo_wrap")
                if photo_wrap and photo_wrap.get("style"):
                    m = re.search(r"url\(['\"]?(https?://[^'\"]+)['\"]?\)", photo_wrap["style"])
                    if m:
                        image_url = m.group(1)

                if not image_url:
                    video_thumb = msg_div.find(class_=re.compile(r"tgme_widget_message.*thumb"))
                    if video_thumb and video_thumb.get("style"):
                        m = re.search(r"url\(['\"]?(https?://[^'\"]+)['\"]?\)", video_thumb["style"])
                        if m:
                            image_url = m.group(1)

                if not image_url:
                    img_tag = msg_div.find("img")
                    if img_tag and img_tag.get("src"):
                        image_url = img_tag["src"]

                items.append({
                    "source_type": "telegram",
                    "channel": f"@{username}",
                    "category": category,
                    "title": text[:80] + ("..." if len(text) > 80 else ""),
                    "content": text,
                    "url": post_link,
                    "image_url": image_url,
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
