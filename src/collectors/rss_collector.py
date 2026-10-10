import logging
from typing import List, Dict, Any
import asyncio
from datetime import datetime
import feedparser
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

class RSSCollector:
    """
    Сборщик стандартных RSS/Atom лент с автоматической очисткой HTML-тегов.
    """
    def __init__(self, feeds: List[Dict[str, str]]):
        self.feeds = feeds

    def _clean_html(self, raw_html: str) -> str:
        if not raw_html:
            return ""
        soup = BeautifulSoup(raw_html, "html.parser")
        return soup.get_text(separator=" ").strip()

    def _parse_feed_sync(self, feed_info: Dict[str, str], limit: int = 10) -> List[Dict[str, Any]]:
        url = feed_info.get("url")
        category = feed_info.get("category", "Новости")
        feed_name = feed_info.get("name", url)

        items = []
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:limit]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "") or entry.get("description", "")
                clean_summary = self._clean_html(summary)

                content = f"{title}\n{clean_summary}"
                guid = entry.get("id", link)

                published_at = entry.get("published", "") or entry.get("updated", "") or datetime.utcnow().isoformat()

                # Извлечение прикрепленного изображения из RSS
                image_url = ""
                if "media_content" in entry and entry.media_content:
                    for media in entry.media_content:
                        if media.get("url"):
                            image_url = media["url"]
                            break
                if not image_url and "enclosures" in entry and entry.enclosures:
                    for enc in entry.enclosures:
                        enc_type = enc.get("type", "")
                        enc_href = enc.get("href", "")
                        if enc_type.startswith("image/") or enc_href.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                            image_url = enc_href
                            break
                if not image_url and "media_thumbnail" in entry and entry.media_thumbnail:
                    image_url = entry.media_thumbnail[0].get("url", "")
                if not image_url:
                    raw_html = entry.get("summary", "") or entry.get("description", "")
                    if "<img" in raw_html:
                        img_soup = BeautifulSoup(raw_html, "html.parser")
                        img_tag = img_soup.find("img")
                        if img_tag and img_tag.get("src"):
                            image_url = img_tag["src"]

                items.append({
                    "source_type": "rss",
                    "channel": feed_name,
                    "category": category,
                    "title": title,
                    "content": content,
                    "url": link,
                    "image_url": image_url,
                    "published_at": published_at,
                    "guid": guid
                })
        except Exception as e:
            logger.error(f"Ошибка при парсинге RSS {url}: {e}")

        return items

    async def fetch_all(self, limit_per_feed: int = 8) -> List[Dict[str, Any]]:
        loop = asyncio.get_running_loop()
        all_items = []
        for feed in self.feeds:
            # feedparser синхронный, выполняем в пуле потоков
            items = await loop.run_in_executor(None, self._parse_feed_sync, feed, limit_per_feed)
            all_items.extend(items)
        return all_items
