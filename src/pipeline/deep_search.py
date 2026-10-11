import asyncio
import html
import logging
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional

import httpx
from bs4 import BeautifulSoup

from src.pipeline.semantic_search import SemanticSearchEngine
from src.llm.router import LLMRouter
from src.pipeline.storage import Storage
from src.pipeline.trust_manager import TrustManager
from src.pipeline.google_grounding import GoogleSearchGroundingEngine
from src.pipeline.query_decomposer import QueryDecomposer
from src.config import load_prompts

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

class DeepSearchEngine:
    """
    Движок многоконтурного глубокого поиска («Агент Сноуден»):
    1. Контур 1: Собственные подключенные источники пользователя (Telegram-каналы, RSS) с семантическим ранжированием.
    2. Контур 2: Внешние авторитетные мировые и профильные источники (Web-поиск, Google Режим ИИ).
    3. Верификация через Траствики (TrustManager): сверка с доверенными реестрами,
       базами эксплойтов CVE и базами объективного контроля OSINT (LostArmour).
    4. Синтез ИИ: сопоставление контуров в единый структурированный аналитический отчет.
    """

    def __init__(self, semantic_engine: Optional[SemanticSearchEngine] = None, trust_manager: Optional[TrustManager] = None):
        self.semantic_engine = semantic_engine or SemanticSearchEngine()
        self.trust_manager = trust_manager or TrustManager()
        self.xray_http_proxy = os.getenv("HTTP_PROXY") or os.getenv("WEB_SEARCH_PROXY") or "http://127.0.0.1:10809"
        self.google_grounding = GoogleSearchGroundingEngine(proxy=self.xray_http_proxy)
        self.decomposer = QueryDecomposer()

    async def search_user_sources(self, query: str, storage: Storage, limit: int = 5, target_channels: Optional[List[str]] = None, specific_entities: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Контур 1: Поиск по базе собственных каналов и лент пользователя.
        Использует семантический векторизованный поиск + полнотекстовое совпадение + декомпозицию сущностей.
        """
        try:
            clean_q, auto_targets = self.semantic_engine.extract_search_intent(query)
            targets = target_channels or auto_targets

            # Выбираем базу недавних публикаций
            feed_items = storage.get_news_feed(limit=500)
            search_items = storage.search_news_feed(clean_q, limit=50)

            # Объединяем без дубликатов по url / item_hash
            seen = set()
            combined_candidates = []
            for it in list(search_items) + list(feed_items):
                key = it.get("url") or it.get("item_hash") or it.get("title")
                if key and key not in seen:
                    seen.add(key)
                    combined_candidates.append(it)

            # Поиск по конкретным сущностям из декомпозиции (например 'Герань-5', 'Князь Вандал', 'Охотник')
            if specific_entities:
                for ent in specific_entities[:6]:
                    ent_items = storage.search_news_feed(ent, limit=15)
                    for it in ent_items:
                        key = it.get("url") or it.get("item_hash") or it.get("title")
                        if key and key not in seen:
                            seen.add(key)
                            combined_candidates.append(it)
                            search_items.append(it)

            if not combined_candidates:
                return []

            # Семантическое ранжирование с учетом целевых каналов и выявленных сущностей
            ranked = await self.semantic_engine.search_posts(
                clean_q,
                combined_candidates,
                limit=limit,
                target_channels=targets,
                specific_entities=specific_entities
            )
            if not ranked and search_items:
                ranked = search_items[:limit]

            results = []
            for it in ranked:
                ch = it.get("channel", "Собственный источник")
                title = it.get("title", "")
                content = (it.get("content") or "").strip()
                pub = (it.get("published_at") or "")[:16]
                url = it.get("url", "")
                results.append({
                    "channel": ch,
                    "title": title,
                    "content": content[:400],
                    "published_at": pub,
                    "url": url,
                    "source_type": "user_source"
                })
            return results
        except Exception as e:
            logger.error(f"Ошибка при поиске по собственным источникам: {e}")
            return []

    async def _search_wikipedia(
        self,
        query: str,
        limit: int = 3,
        client_configs: Optional[List[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Полнотекстовый поиск по Википедии (Wikipedia Full-Text API) с извлечением вводного абзаца статьи (Lead Extract).
        Находит реальные статьи даже по косвенным запросам (например: «Генерал Суровикин» -> «Суровикин, Сергей Владимирович»).
        """
        configs = client_configs or [{}]
        hdrs = headers or {"User-Agent": USER_AGENT}
        results = []

        # 1. Полнотекстовый поиск в русской Википедии
        search_url = f"https://ru.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(query)}&utf8=&format=json"
        search_items = []
        for cfg in configs:
            try:
                async with httpx.AsyncClient(follow_redirects=True, headers=hdrs, **cfg) as client:
                    resp = await client.get(search_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        search_items = data.get("query", {}).get("search", [])
                        if search_items:
                            break
            except Exception as e:
                logger.debug(f"Wikipedia search error ({cfg}): {e}")

        if not search_items:
            # Резервный OpenSearch (префиксный)
            open_url = f"https://ru.wikipedia.org/w/api.php?action=opensearch&search={urllib.parse.quote(query)}&limit={limit}&namespace=0&format=json"
            for cfg in configs:
                try:
                    async with httpx.AsyncClient(follow_redirects=True, headers=hdrs, **cfg) as client:
                        resp = await client.get(open_url)
                        if resp.status_code == 200:
                            data = resp.json()
                            if len(data) >= 4 and data[1]:
                                for t, d, u in zip(data[1], data[2], data[3]):
                                    if u and u.startswith("http"):
                                        results.append({
                                            "title": f"Википедия: {t}",
                                            "url": u,
                                            "snippet": d or f"Энциклопедические сведения о {t}",
                                            "source_type": "web",
                                            "trust_badge": "🟢 [Траствик / Википедия]",
                                            "trust_score": 90
                                        })
                                        if len(results) >= limit:
                                            break
                            if results:
                                return results
                except Exception:
                    pass
            return results

        # 2. Получение подробного вводного абзаца (Extract)
        top_titles = [it.get("title", "") for it in search_items[:limit] if it.get("title")]
        titles_param = "|".join(top_titles)
        extract_url = f"https://ru.wikipedia.org/w/api.php?action=query&prop=extracts&exintro=1&explaintext=1&titles={urllib.parse.quote(titles_param)}&format=json"
        extracts_map = {}
        for cfg in configs:
            try:
                async with httpx.AsyncClient(follow_redirects=True, headers=hdrs, **cfg) as client:
                    ext_resp = await client.get(extract_url)
                    if ext_resp.status_code == 200:
                        pages = ext_resp.json().get("query", {}).get("pages", {})
                        for pid, pdata in pages.items():
                            t = pdata.get("title")
                            ex = pdata.get("extract", "")
                            if t and ex:
                                extracts_map[t] = ex
                        break
            except Exception:
                pass

        for it in search_items[:limit]:
            title = it.get("title", "")
            raw_snippet = it.get("snippet", "")
            clean_snippet = re.sub(r'<[^>]+>', '', raw_snippet).strip()
            lead_extract = extracts_map.get(title, clean_snippet)
            page_url = f"https://ru.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
            results.append({
                "title": f"Википедия: {title}",
                "url": page_url,
                "snippet": lead_extract[:500] if lead_extract else clean_snippet,
                "source_type": "web",
                "trust_badge": "🟢 [Траствик / Википедия]",
                "trust_score": 90
            })

        return results

    async def _search_google_news_rss(
        self,
        query: str,
        limit: int = 3,
        client_configs: Optional[List[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Поиск свежих новостей через открытый RSS-поток Google News.
        Надежно работает через прокси, возвращает реальные заголовки и ссылки СМИ (РИА Новости, ТАСС, TOPNews и др.).
        """
        configs = client_configs or [{}]
        hdrs = headers or {"User-Agent": USER_AGENT}
        news_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=ru&gl=RU&ceid=RU:ru"
        results = []
        q_tokens = [w.lower() for w in re.findall(r'[а-яА-Яa-zA-Z0-9]+', query) if len(w) > 3]

        for cfg in configs:
            try:
                async with httpx.AsyncClient(follow_redirects=True, headers=hdrs, **cfg) as client:
                    resp = await client.get(news_url)
                    if resp.status_code == 200 and resp.text:
                        root = ET.fromstring(resp.text)
                        for item in root.findall(".//item")[:limit * 3]:
                            tit = item.findtext("title", "")
                            link = item.findtext("link", "")
                            source_tag = item.find("source")
                            source_name = source_tag.text if source_tag is not None else "СМИ"
                            pub_date = item.findtext("pubDate", "")
                            if tit and link:
                                # Исключаем дефолтные общие сводки Google News, если в новости нет ни одного ключевого слова запроса
                                if q_tokens and not any(t in tit.lower() for t in q_tokens):
                                    continue
                                results.append({
                                    "title": f"{source_name}: {tit}",
                                    "url": link,
                                    "snippet": f"Материал издания {source_name} ({pub_date}): {tit}",
                                    "source_type": "web",
                                    "trust_badge": f"🌐 [{source_name}]",
                                    "trust_score": 75
                                })
                                if len(results) >= limit:
                                    break
                        if results:
                            break
            except Exception as e:
                logger.debug(f"Google News RSS search error ({cfg}): {e}")

        return results

    async def _search_github_repos(
        self,
        query: str,
        limit: int = 4,
        client_configs: Optional[List[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Поиск открытых репозиториев, библиотек и инструментов на GitHub через официальный Search API.
        """
        q_low = query.lower()
        terms = []
        if any(w in q_low for w in ["вирус", "вредонос", "malware", "троян", "малвар"]):
            terms.append("malware samples OR malware analysis")
        if any(w in q_low for w in ["эксплойт", "exploit", "cve", "0-day", "poc"]):
            terms.append("exploit OR poc")
        if any(w in q_low for w in ["библиотек", "library", "коллекци"]):
            terms.append("library OR collection")
        if any(w in q_low for w in ["osint", "разведк"]):
            terms.append("osint tool")
        if any(w in q_low for w in ["парсер", "scraper", "crawler"]):
            terms.append("scraper OR parser")

        latin_words = [w for w in re.findall(r'[a-zA-Z0-9_-]+', query) if w.lower() not in ["github", "git", "repo", "repository"]]
        if latin_words:
            gh_term = " ".join(latin_words)
        elif terms:
            gh_term = " ".join(terms)
        else:
            stop_ru = {"гитхаб", "github", "репозиторий", "репозитории", "репо", "библиотека", "библиотеки", "с", "для", "по", "на", "про"}
            ru_words = [w for w in re.findall(r'[а-яА-Яa-zA-Z0-9]+', query) if w.lower() not in stop_ru]
            gh_term = " ".join(ru_words) if ru_words else query

        api_url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(gh_term)}&sort=stars&order=desc&per_page={limit}"
        configs = client_configs or [{"verify": False}, {}]
        hdrs = {
            "User-Agent": "Snowden-Agent/1.0",
            "Accept": "application/vnd.github.v3+json"
        }

        results = []
        for cfg in configs:
            try:
                async with httpx.AsyncClient(timeout=8.0, headers=hdrs, **cfg) as client:
                    resp = await client.get(api_url)
                    items = resp.json().get("items", []) if resp.status_code == 200 else []

                    # Если по сложной фразе 0 находок — пробуем по первому ключевому латинскому термину
                    if not items and len(gh_term.split()) > 1:
                        first_term = gh_term.split()[0]
                        alt_url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(first_term)}&sort=stars&order=desc&per_page={limit}"
                        alt_resp = await client.get(alt_url)
                        if alt_resp.status_code == 200:
                            items = alt_resp.json().get("items", [])

                    for it in items[:limit]:
                        full_name = it.get("full_name", "")
                        html_url = it.get("html_url", "")
                        desc = it.get("description") or "Описание отсутствует"
                        stars = it.get("stargazers_count", 0)
                        lang = it.get("language") or "Multi"
                        results.append({
                            "title": f"GitHub: {full_name} (⭐ {stars:,})",
                            "url": html_url,
                            "snippet": f"Репозиторий {full_name} [{lang}] (⭐ {stars:,}): {desc}",
                            "source_type": "github_repo",
                            "trust_badge": "📦 [GitHub Repo / Open Source]",
                            "trust_score": 90
                        })
                    if results:
                        break
            except Exception as e:
                logger.debug(f"GitHub search error ({cfg}): {e}")

        return results

    async def _search_duckduckgo_api(
        self,
        query: str,
        client_configs: Optional[List[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """DuckDuckGo Instant Answer API (быстрое определение без капчи)"""
        configs = client_configs or [{}]
        hdrs = headers or {"User-Agent": USER_AGENT}
        ddg_api_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
        for cfg in configs:
            try:
                async with httpx.AsyncClient(follow_redirects=True, headers=hdrs, **cfg) as client:
                    resp = await client.get(ddg_api_url)
                    if resp.status_code == 200 and resp.text:
                        data = resp.json()
                        abstract = data.get("AbstractText", "")
                        heading = data.get("Heading", "")
                        abs_url = data.get("AbstractURL", "")
                        if abstract:
                            return [{
                                "title": heading or query,
                                "url": abs_url or f"https://duckduckgo.com/?q={urllib.parse.quote(query)}",
                                "snippet": abstract[:450],
                                "source_type": "web",
                                "trust_badge": "🌐 [DuckDuckGo Справочник]",
                                "trust_score": 80
                            }]
            except Exception:
                pass
        return []

    async def _scrape_duckduckgo(
        self,
        query: str,
        limit: int = 4,
        client_configs: Optional[List[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        """Парсинг DuckDuckGo HTML / Lite"""
        configs = client_configs or [{}]
        hdrs = headers or {"User-Agent": USER_AGENT}
        results = []

        ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        for cfg in configs:
            try:
                async with httpx.AsyncClient(follow_redirects=True, headers=hdrs, **cfg) as client:
                    resp = await client.get(ddg_url)
                    if resp.status_code == 200 and resp.text:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        links = soup.find_all("div", class_="result") or soup.find_all("div", class_="web-result")
                        for el in links[:limit * 2]:
                            title_tag = el.find("a", class_="result__a") or el.find("a")
                            if not title_tag:
                                continue
                            title = title_tag.get_text(strip=True)
                            raw_href = title_tag.get("href", "")
                            clean_url = raw_href
                            if "uddg=" in raw_href:
                                m = re.search(r'uddg=([^&]+)', raw_href)
                                if m:
                                    clean_url = urllib.parse.unquote(m.group(1))

                            snippet_tag = el.find("a", class_="result__snippet") or el.find("div", class_="result__snippet")
                            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

                            if clean_url and clean_url.startswith("http") and "duckduckgo.com" not in clean_url:
                                results.append({
                                    "title": title,
                                    "url": clean_url,
                                    "snippet": snippet,
                                    "source_type": "web"
                                })
                                if len(results) >= limit:
                                    break
                if results:
                    break
            except Exception:
                pass

        if not results:
            lite_url = "https://lite.duckduckgo.com/lite/"
            data = {"q": query}
            for cfg in configs:
                try:
                    async with httpx.AsyncClient(follow_redirects=True, headers=hdrs, **cfg) as client:
                        resp = await client.post(lite_url, data=data)
                        if resp.status_code == 200 and resp.text:
                            soup = BeautifulSoup(resp.text, "html.parser")
                            rows = soup.find_all("tr")
                            for i in range(0, len(rows) - 1, 2):
                                tr_title = rows[i]
                                tr_snip = rows[i + 1] if i + 1 < len(rows) else None
                                a = tr_title.find("a", class_="result-link") or tr_title.find("a")
                                if a:
                                    title = a.get_text(strip=True)
                                    href = a.get("href", "")
                                    if "uddg=" in href:
                                        m = re.search(r'uddg=([^&]+)', href)
                                        if m:
                                            href = urllib.parse.unquote(m.group(1))
                                    snip = tr_snip.find("td", class_="result-snippet") if tr_snip else None
                                    snippet = snip.get_text(strip=True) if snip else ""
                                    if href.startswith("http"):
                                        results.append({
                                            "title": title,
                                            "url": href,
                                            "snippet": snippet,
                                            "source_type": "web"
                                        })
                                        if len(results) >= limit:
                                            break
                    if results:
                        break
                except Exception:
                    pass

        return results

    async def search_web(
        self,
        query: str,
        limit: int = 4,
        timeout: float = 12.0,
        use_google_ai: bool = True,
        llm_router: Optional[LLMRouter] = None,
        fallback_queries: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Контур 2: Многоуровневый интеллектуальный веб-поиск.
        1. Google Search Grounding (Gemini).
        2. Полнотекстовый поиск Википедии + Lead Extracts (биографии, ТТХ, исторические вехи).
        3. Google News RSS (свежие публикации федеральных и мировых СМИ).
        4. DuckDuckGo Instant Answer API.
        5. DuckDuckGo HTML / Lite scraping.
        6. Каскадный поиск по декомпозированным подзапросам fallback_queries.
        """
        results: List[Dict[str, Any]] = []

        # 1. Google 'Режим ИИ' (Gemini Search Grounding)
        if llm_router and llm_router.config.gemini.api_key:
            self.google_grounding.api_key = llm_router.config.gemini.api_key
        elif not self.google_grounding.api_key:
            try:
                cfg = load_app_config()
                self.google_grounding.api_key = cfg.llm.gemini.api_key or os.getenv("GEMINI_API_KEY")
            except Exception:
                pass

        if use_google_ai and self.google_grounding.is_available():
            try:
                g_res = await self.google_grounding.search_and_ground(query, timeout=min(timeout, 30.0))
                if g_res and g_res.get("sources"):
                    results.extend(g_res["sources"][:limit])
            except Exception as ge:
                logger.debug(f"Google Grounding error, fallback to multi-engine: {ge}")

        if len(results) >= limit:
            return self.trust_manager.enrich_items(results[:limit])

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        client_configs = []
        if self.xray_http_proxy:
            client_configs.append({"proxy": self.xray_http_proxy, "timeout": timeout})
        client_configs.append({"timeout": timeout})

        # 1.5. Поиск по GitHub Repositories (если запрос касается GitHub, репозиториев, кода, библиотек)
        is_gh_intent = any(w in query.lower() for w in ["github", "гитхаб", "репозитори", "репо", "библиотек", "исходн", "source code", "open source", "скрипт", "tools", "утилит"])
        if is_gh_intent and len(results) < limit:
            try:
                gh_res = await self._search_github_repos(query, limit=limit, client_configs=client_configs, headers=headers)
                if gh_res:
                    results.extend(gh_res)
            except Exception as ge:
                logger.debug(f"GitHub repos collector error: {ge}")

        # 2. Полнотекстовый поиск Википедии + Lead Extracts
        try:
            wiki_res = await self._search_wikipedia(query, limit=2, client_configs=client_configs, headers=headers)
            if wiki_res:
                results.extend(wiki_res)
        except Exception as we:
            logger.debug(f"Wikipedia collector error: {we}")

        # 3. Google News RSS (свежие статьи прессы)
        if len(results) < limit:
            try:
                news_res = await self._search_google_news_rss(query, limit=2, client_configs=client_configs, headers=headers)
                if news_res:
                    results.extend(news_res)
            except Exception as ne:
                logger.debug(f"News RSS collector error: {ne}")

        # 4. DuckDuckGo Instant Answer API
        if len(results) < limit:
            try:
                ddg_api_res = await self._search_duckduckgo_api(query, client_configs=client_configs, headers=headers)
                if ddg_api_res:
                    results.extend(ddg_api_res)
            except Exception:
                pass

        # 5. DuckDuckGo HTML / Lite
        if len(results) < limit:
            ddg_html_res = await self._scrape_duckduckgo(query, limit=limit, client_configs=client_configs, headers=headers)
            if ddg_html_res:
                results.extend(ddg_html_res)

        # 6. Каскадный поиск по декомпозированным подзапросам
        if len(results) < limit and fallback_queries:
            for alt_q in fallback_queries[:2]:
                try:
                    alt_res = await self.search_web(
                        alt_q,
                        limit=limit - len(results),
                        timeout=timeout,
                        use_google_ai=use_google_ai,
                        llm_router=llm_router,
                        fallback_queries=None
                    )
                    if alt_res:
                        results.extend(alt_res)
                        if len(results) >= limit:
                            break
                except Exception:
                    pass

        return self.trust_manager.enrich_items(results[:limit])

    @staticmethod
    async def execute_deep_search(
        self,
        query: str,
        storage: Storage,
        llm_router: LLMRouter,
        refresh_collector_func=None
    ) -> Dict[str, Any]:
        """
        Главный оркестратор основательного сетевого поиска:
        - Опрашивает контур источников пользователя (Telegram-каналы, RSS).
        - Опрашивает открытый веб (Google Search Grounding / DuckDuckGo / GitHub / Wikipedia).
        - Формирует развернутый контекст для ИИ.
        - Получает структурированный аналитический ответ с первоисточниками.
        """
        use_google_ai = storage.is_google_ai_search_enabled()
        if llm_router and llm_router.config.gemini.api_key:
            self.google_grounding.api_key = llm_router.config.gemini.api_key

        clean_query = query.strip()
        search_core, target_channels = self.semantic_engine.extract_search_intent(clean_query)

        # Фаза 1: Декомпозиция запроса (Qwen / LLM Планировщик выявляет конкретные модели и системы)
        decomp = await self.decomposer.decompose(clean_query, llm_router=llm_router)
        specific_entities = decomp.get("specific_entities", [])
        search_queries = decomp.get("search_queries", [clean_query])

        # 1. Запуск поиска по контурам параллельно
        user_sources_task = self.search_user_sources(
            search_core,
            storage,
            limit=8,
            target_channels=target_channels,
            specific_entities=specific_entities
        )
        primary_tokens = [w for w in re.findall(r'[а-яА-Яa-zA-Z0-9]+', search_core.lower()) if len(w) > 3]
        best_web_query = search_queries[0] if search_queries else search_core
        if len(search_queries) > 1:
            q1_low = search_queries[1].lower()
            if primary_tokens and any(t in q1_low for t in primary_tokens):
                best_web_query = search_queries[1]
        web_query = best_web_query

        fallback_queries = [q for q in search_queries if q != web_query]
        web_sources_task = self.search_web(
            web_query,
            limit=4,
            use_google_ai=use_google_ai,
            llm_router=llm_router,
            fallback_queries=fallback_queries
        )

        tasks = [user_sources_task, web_sources_task]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        user_sources = gathered[0] if isinstance(gathered[0], list) else []
        web_sources = gathered[1] if isinstance(gathered[1], list) else []

        # Если в собственных источниках совсем пусто — пробуем оперативно собрать свежие новости из каналов и повторить
        if len(user_sources) == 0 and refresh_collector_func:
            try:
                await refresh_collector_func()
                user_sources = await self.search_user_sources(
                    search_core,
                    storage,
                    limit=8,
                    target_channels=target_channels,
                    specific_entities=specific_entities
                )
            except Exception as ref_err:
                logger.warning(f"Refresh before deep search failed: {ref_err}")

        # 2. Формируем сырой фактологический контекст для LLM
        user_sources_text = ""
        if user_sources:
            lines_list = []
            for idx, it in enumerate(user_sources, 1):
                ch = it.get("channel", "Канал")
                tit = it.get("title", "")
                cnt = it.get("content", "")
                url = it.get("url", "")
                pub = it.get("published_at", "")
                lines_list.append(f"{idx}. [{ch}] ({pub}) {tit}\nТекст: {cnt}\nСсылка: {url}")
            user_sources_text = "\n\n".join(lines_list)
        else:
            user_sources_text = "ПОЛНОЕ ОТСУТСТВИЕ СОВПАДЕНИЙ: В подключенных каналах и лентах публикаций по теме запроса не найдено (база проверена)."

        web_sources_text = ""
        if web_sources:
            lines_list = []
            for idx, it in enumerate(web_sources, 1):
                badge = it.get("trust_badge", "🌐 [Открытый Web]")
                lines_list.append(f"{idx}. {badge} Заголовок: {it.get('title')}\nВыдержка: {it.get('snippet')}\nСсылка: {it.get('url')}")
            web_sources_text = "\n\n".join(lines_list)
        else:
            web_sources_text = "Внешний открытый веб-поиск не вернул прямых релевантных страниц."

        # 3. Подготовка промпта для ИИ-аналитика
        entities_header = f"ВЫЯВЛЕННЫЕ КЛЮЧЕВЫЕ МОДЕЛИ И СИСТЕМЫ ДЛЯ АНАЛИЗА: {', '.join(specific_entities)}\n\n" if specific_entities else ""
        ai_prompt = (
            f"Тема поискового запроса: «{clean_query}»\n\n"
            f"{entities_header}"
            f"Ниже представлены собранные разведданные по контурам:\n\n"
            f"=== КОНТУР 1: ИСТОЧНИКИ ПОЛЬЗОВАТЕЛЯ (Telegram-каналы, авторские каналы, RSS) ===\n"
            f"{user_sources_text}\n\n"
            f"=== КОНТУР 2: ВНЕШНИЕ АВТОРИТЕТНЫЕ ИСТОЧНИКИ (Web, открытые медиа, профильные ресурсы) ===\n"
            f"{web_sources_text}\n\n"
            "ИНСТРУКЦИЯ ПО ОФОРМЛЕНИЮ ОТВЕТА (ФОРМАТ OSINT-ДОСЬЕ И АНАЛИТИЧЕСКОГО СИНТЕЗА):\n"
            "Сформируй исчерпывающий, профессиональный ответ в HTML-формате Telegram уровня Google AI Overviews / Perplexity Pro.\n\n"
            "СТРУКТУРА ОТВЕТА:\n"
            "1. 🎖 <b>[ФИО персоны / Название темы / Организация] — Аналитическое досье:</b>\n"
            "   - Четкое фактологическое определение: официальный статус, организация, государственная или военная должность, роль строго по теме запроса пользователя.\n\n"
            "2. 📌 <b>Ключевые этапы, факты или хроника событий:</b>\n"
            "   - Структурированные пункты проверенных фактов: официальные заявления ведомств, хроника событий, результаты расследований или ТТХ техники.\n\n"
            "3. 📍 <b>Текущий статус и официальная позиция (2024–2026):</b>\n"
            "   - Актуальное положение дел, последние публичные заявления руководства, ход расследования или текущая диспозиция.\n\n"
            "4. 📱 <b>Оперативный мониторинг (Telegram и профильные медиа):</b>\n"
            "   - Если в КОНТУРЕ 1 (Источники пользователя) есть публикации (@Voennaja_svodka, @ross_name, @infomil_live и др.) — ОБЯЗАТЕЛЬНО начни с них! Приведи конкретные факты и прямые ссылки на посты: <a href=\"URL\">[Канал]: Суть публикации</a>.\n"
            "   - Фразу «В недавних публикациях подключенных Telegram-каналов прямых упоминаний за последние дни не зафиксировано» пиши ИСКЛЮЧИТЕЛЬНО тогда, когда КОНТУР 1 полностью пуст!\n"
            "   - Далее укажи, что сообщают внешние профильные медиа, порталы безопасности или пресса строго по теме запроса.\n\n"
            "5. 🔗 <b>Верифицированные источники и репозитории:</b>\n"
            "   - Список источников с активными гиперссылками: <a href=\"URL\">Название статьи</a>.\n"
            "   - Если в контексте найдены репозитории GitHub — обязательно выведи каждый репозиторий: <a href=\"URL\"><b>Владелец/Репозиторий (⭐ Stars)</b></a> с кратким описанием функционала.\n\n"
            "СТРОЖАЙШИЕ ПРАВИЛА И ЗАПРЕТЫ:\n"
            "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО путать персоналий и приписывать чужие псевдонимы или биографии (например: «Генерал Армагеддон» — это Сергей Суровикин, генерал армии ВС РФ, он не имеет отношения к ФСБ; главой ФСБ России с 2008 года является генерал армии Александр Бортников)!\n"
            "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выдумывать фиктивные имена людей или ложные ссылки на Википедию! Ссылайся ТОЛЬКО на реальные факты и предоставленные источники.\n"
            "- Используй ТОЛЬКО разрешенный Telegram HTML (<b>, <i>, <code>, <blockquote>, <a href=\"...\">).\n"
            "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕН Markdown: никаких **звездочек**, никаких кавычек в коде!\n"
            "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО оборачивать целые разделы, списки или пункты новостей в <blockquote> целиком! Весь текст пиши обычным текстом с <b>заголовками</b>.\n"
            "- Тег <blockquote> разрешен ИСКЛЮЧИТЕЛЬНО для коротких прямых цитат конкретных спикеров в 1-2 строки (например: <blockquote>«Цитата» — Спикер</blockquote>).\n"
            "- Категорически запрещено использовать шаблоны-заглушки вида '— @канал' или '[@канал](ссылка)'.\n"
            "- Ответ должен быть содержательным, плотным и фактологическим, без пустой воды."
        )

        prompts = load_prompts().get("system_prompts", {})
        system_prompt = prompts.get("deep_search") or (
            "Ты — ведущий аналитик разведцентра «Агент Сноуден» (2026 год). "
            "Твоя задача — сопоставлять данные из личных закрытых/отслеживаемых каналов пользователя с открытым вебом "
            "и поисковым индексом Google (Gemini Grounding), и выдавать безупречно структурированный, доказательный отчет с первоисточниками."
        )

        ai_res = await llm_router.generate_response(
            task="deep_search",
            user_prompt=ai_prompt,
            override_system_prompt=system_prompt
        )

        search_banner = ""
        if web_query and web_query != clean_query:
            search_banner = f"🌐 <b>Поиск в сети:</b> <code>{html.escape(web_query)}</code>\n\n"

        ai_text = ai_res.get("content", "").strip() if ai_res.get("success") else ""
        if not ai_text:
            ai_text = self._build_fallback_summary(clean_query, user_sources, web_sources, web_query=web_query)

        if search_banner and not ai_text.startswith("🌐 <b>Поиск в сети:"):
            ai_text = search_banner + ai_text

        return {
            "query": clean_query,
            "text": ai_text,
            "user_sources": user_sources,
            "web_sources": web_sources,
            "total_found": len(user_sources) + len(web_sources)
        }

    def _build_fallback_summary(
        self,
        query: str,
        user_sources: List[Dict[str, Any]],
        web_sources: List[Dict[str, Any]],
        web_query: Optional[str] = None
    ) -> str:
        """Резервный структурированный генератор если нейросеть недоступна"""
        lines_out = []
        if web_query and web_query != query:
            lines_out.append(f"🌐 <b>Поиск в сети:</b> <code>{html.escape(web_query)}</code>\n")
        lines_out.append(f"🔍 <b>Аналитическое досье по запросу:</b> <i>«{html.escape(query)}»</i>\n")

        wiki_items = [it for it in web_sources if "википед" in it.get("title", "").lower()]
        other_web = [it for it in web_sources if it not in wiki_items]

        if wiki_items:
            w = wiki_items[0]
            w_tit = html.escape(w.get("title", ""))
            w_snip = html.escape(w.get("snippet", ""))
            w_url = w.get("url", "")
            lines_out.append("🎖 <b>Фактологическая справка:</b>")
            lines_out.append(f"<a href=\"{w_url}\"><b>{w_tit}</b></a>\n<blockquote>{w_snip}</blockquote>\n")

        # 1. Каналы
        lines_out.append("📱 <b>1. По данным ваших каналов и мониторинга:</b>")
        if user_sources:
            for it in user_sources:
                ch = html.escape(it.get("channel", "Канал"))
                pub = it.get("published_at", "")
                tit = html.escape(it.get("title", ""))
                cnt = html.escape(it.get("content", "")[:250])
                url = it.get("url", "")
                link_html = f' — <a href="{url}">Перейти к посту</a>' if url else ""
                lines_out.append(f"• <b>[{ch}]</b> {f'({pub})' if pub else ''}: {tit}\n<blockquote>{cnt}...</blockquote>{link_html}\n")
        else:
            lines_out.append("<i>В недавних публикациях подключенных Telegram-каналов прямых упоминаний не зафиксировано.</i>\n")

        # 2. Web
        lines_out.append("🌐 <b>2. Внешние авторитетные источники и пресса (Web):</b>")
        display_web = (wiki_items[1:] + other_web) if wiki_items else web_sources
        if display_web:
            for it in display_web:
                tit = html.escape(it.get("title", ""))
                snip = html.escape(it.get("snippet", ""))
                url = it.get("url", "")
                badge = html.escape(it.get("trust_badge", "🌐 [Web]"))
                lines_out.append(f"• {badge} <a href=\"{url}\"><b>{tit}</b></a>\n<blockquote>{snip}</blockquote>\n")
        else:
            lines_out.append("<i>Открытый веб-поиск не вернул прямых релевантных страниц.</i>\n")

        return "\n".join(lines_out)
