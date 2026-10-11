import logging
import os
from typing import Dict, Any, List, Optional
import httpx

from src.pipeline.trust_manager import TrustManager

logger = logging.getLogger(__name__)

class GoogleSearchGroundingEngine:
    """
    «Режим ИИ» Google (Google Search Grounding via Gemini).
    Опрашивает поисковый индекс Google через официальный инструмент googleSearch в Google AI Studio.
    Получает самые актуальные мировые и российские факты с реальными ссылками на первоисточники.
    """

    def __init__(self, api_key: Optional[str] = None, proxy: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.proxy = proxy or os.getenv("WEB_SEARCH_PROXY") or os.getenv("HTTP_PROXY") or "http://127.0.0.1:10809"
        self.models_pool = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
        self.trust_manager = TrustManager()

    def is_available(self) -> bool:
        """Проверяет, задан ли API-ключ Gemini"""
        return bool(self.api_key and len(self.api_key) > 10 and not self.api_key.startswith("AIzaSy_placeholder"))

    def _parse_gemini_response(self, data: Dict[str, Any], query: str) -> List[Dict[str, Any]]:
        """Извлекает и верифицирует источники из ответа Gemini Grounding"""
        candidates = data.get("candidates", [])
        if not candidates:
            return []

        cand0 = candidates[0]
        grounding_meta = cand0.get("groundingMetadata", {})
        chunks = grounding_meta.get("groundingChunks", [])

        sources: List[Dict[str, Any]] = []
        seen_urls = set()
        for ch in chunks:
            web_info = ch.get("web", {})
            uri = web_info.get("uri", "")
            tit = web_info.get("title", "")
            if uri and uri not in seen_urls:
                seen_urls.add(uri)
                trust_info = self.trust_manager.verify_url(uri)
                sources.append({
                    "title": tit or "Источник Google",
                    "url": uri,
                    "snippet": f"Материал по теме: {tit}",
                    "source_type": "google_search_grounding",
                    "trust_badge": trust_info.get("badge", "🌐 [Google Режим ИИ]"),
                    "trust_score": trust_info.get("trust_score", 50)
                })
        return sources

    async def search_and_ground(self, query: str, timeout: float = 35.0) -> Optional[Dict[str, Any]]:
        """
        Выполняет поиск в Google через 'Режим ИИ' с автоматическим веб-граундингом.
        Возвращает:
        - text: подготовленный аналитический синтез от Google
        - sources: список найденных веб-страниц (заголовок, ссылка, аннотация)
        - queries: поисковые запросы, которые выполнял Google
        """
        if not self.is_available():
            logger.debug("Google Search Grounding: API ключ Gemini не настроен")
            return None

        client_kwargs: Dict[str, Any] = {"timeout": timeout, "follow_redirects": True}
        if self.proxy:
            client_kwargs["proxy"] = self.proxy

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                f"Проведи глубокое исследование по теме: «{query}».\n"
                                "Найди актуальные факты, последние события, новинки, модели или заявления.\n"
                                "Отвечай подробно, профессионально и фактологично на русском языке. "
                                "Приводи конкретные названия, цифры и ссылки на первоисточники."
                            )
                        }
                    ]
                }
            ],
            "tools": [
                {"googleSearch": {}}
            ]
        }

        for model in self.models_pool:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if not candidates:
                            continue

                        cand0 = candidates[0]
                        content = cand0.get("content", {})
                        parts = content.get("parts", [])
                        text = "".join(p.get("text", "") for p in parts if "text" in p).strip()

                        # Извлекаем ссылки и источники поиска
                        grounding_meta = cand0.get("groundingMetadata", {})
                        web_queries = grounding_meta.get("webSearchQueries", [])
                        sources = self._parse_gemini_response(data, query)

                        logger.info(f"✨ [Google Режим ИИ] Успешно получено {len(sources)} источников по теме «{query[:40]}» ({model})")
                        return {
                            "text": text,
                            "sources": sources,
                            "queries": web_queries,
                            "model": model
                        }
                    else:
                        logger.debug(f"Google Grounding error ({model}): HTTP {resp.status_code} - {resp.text[:100]}")
            except Exception as e:
                logger.debug(f"Google Grounding attempt error ({model}): {e}")

        return None
