import logging
from typing import Dict, Any, List
from src.collectors.telegram_collector import TelegramWebCollector
from src.collectors.rss_collector import RSSCollector
from src.pipeline.storage import Storage
from src.llm.router import LLMRouter
from src.llm.prompts import DIGEST_SYSTEM_PROMPT, build_digest_user_prompt
from src.config import load_preferences, load_sources

logger = logging.getLogger(__name__)

class DigestBuilder:
    def __init__(self, storage: Storage, router: LLMRouter):
        self.storage = storage
        self.router = router

    def _is_blacklisted(self, text: str, blacklist: List[str]) -> bool:
        lower_text = text.lower()
        for word in blacklist:
            if word.lower() in lower_text:
                return True
        return False

    async def collect_fresh_news(self) -> List[Dict[str, Any]]:
        sources = load_sources().get("sources", {})
        tg_channels = sources.get("telegram_channels", [])
        rss_feeds = sources.get("rss_feeds", [])

        all_items = []

        if tg_channels:
            tg_collector = TelegramWebCollector(tg_channels)
            tg_items = await tg_collector.fetch_all(limit_per_channel=6)
            all_items.extend(tg_items)

        if rss_feeds:
            rss_collector = RSSCollector(rss_feeds)
            rss_items = await rss_collector.fetch_all(limit_per_feed=6)
            all_items.extend(rss_items)

        # Дедупликация через БД
        unseen = self.storage.filter_unseen_items(all_items)
        logger.info(f"Собрано {len(all_items)} публикаций, из них новых: {len(unseen)}")

        # Фильтрация по стоп-словам из preferences
        prefs = load_preferences()
        blacklist = prefs.get("filters", {}).get("blacklist", [])

        clean_items = [
            item for item in unseen
            if not self._is_blacklisted(item.get("content", ""), blacklist)
        ]
        return clean_items

    async def generate_digest(self, force_all: bool = False) -> Dict[str, Any]:
        items = await self.collect_fresh_news()

        if not items:
            return {
                "success": True,
                "text": "📭 Нет новых непрочитанных новостей по вашим темам.",
                "provider": "none",
                "items_count": 0
            }

        # Формируем сырой текст для LLM
        raw_chunks = []
        for i, it in enumerate(items[:25], 1): # Ограничиваем пачку 25 ключевыми новостями
            raw_chunks.append(
                f"[{i}] Источник: {it['channel']} ({it.get('category', 'Общее')})\n"
                f"Заголовок: {it['title']}\n"
                f"Текст: {it['content'][:350]}\n"
                f"Ссылка: {it['url']}\n"
            )

        raw_text = "\n---\n".join(raw_chunks)
        prefs = load_preferences()
        interests = prefs.get("interests", [])
        blacklist = prefs.get("filters", {}).get("blacklist", [])
        learned_prefs = self.storage.get_learned_preferences_summary()

        user_prompt = build_digest_user_prompt(
            raw_items_text=raw_text,
            user_interests=interests,
            blacklist=blacklist,
            learned_preferences=learned_prefs
        )

        logger.info("Отправка сформированного пакета новостей в LLM...")
        llm_res = await self.router.generate_response(task="digest", user_prompt=user_prompt)

        if not llm_res.get("success"):
            return {
                "success": False,
                "text": f"⚠️ Ошибка генерации дайджеста: {llm_res.get('content')}",
                "provider": "error",
                "items_count": len(items)
            }

        # Помечаем обработанные новости как прочитанные
        self.storage.mark_items_as_seen(items)

        provider_info = f"\n\n🤖 *Сгенерировано с помощью:* `{llm_res.get('provider')}` (`{llm_res.get('model')}`), за {llm_res.get('latency')}с."
        if llm_res.get("fallback_occurred"):
            provider_info += " *(сработал резервный шлюз)*"

        final_text = llm_res.get("content") + provider_info
        self.storage.save_digest(final_text, llm_res.get("provider", "unknown"), len(items))

        return {
            "success": True,
            "text": final_text,
            "provider": llm_res.get("provider"),
            "items_count": len(items)
        }
