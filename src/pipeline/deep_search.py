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
    3. Контур 3: Теневой сегмент сети Tor (.onion / Darknet) — опционально, с возможностью включения/выключения.
    4. Верификация через Траствики (TrustManager): сверка с PGP-реестрами зеркал Tor (OnionTree/Daunt),
       базами эксплойтов CVE и базами объективного контроля OSINT (LostArmour).
    5. Синтез ИИ: сопоставление всех контуров в единый структурированный аналитический отчет.
    """

    def __init__(self, semantic_engine: Optional[SemanticSearchEngine] = None, trust_manager: Optional[TrustManager] = None):
        self.semantic_engine = semantic_engine or SemanticSearchEngine()
        self.trust_manager = trust_manager or TrustManager()
        self.tor_socks_proxy = os.getenv("TOR_SOCKS_PROXY", "socks5://127.0.0.1:9050")
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

        for cfg in configs:
            try:
                async with httpx.AsyncClient(follow_redirects=True, headers=hdrs, **cfg) as client:
                    resp = await client.get(news_url)
                    if resp.status_code == 200 and resp.text:
                        root = ET.fromstring(resp.text)
                        for item in root.findall(".//item")[:limit]:
                            tit = item.findtext("title", "")
                            link = item.findtext("link", "")
                            source_tag = item.find("source")
                            source_name = source_tag.text if source_tag is not None else "СМИ"
                            pub_date = item.findtext("pubDate", "")
                            if tit and link:
                                results.append({
                                    "title": f"{source_name}: {tit}",
                                    "url": link,
                                    "snippet": f"Материал издания {source_name} ({pub_date}): {tit}",
                                    "source_type": "web",
                                    "trust_badge": f"🌐 [{source_name}]",
                                    "trust_score": 75
                                })
                        if results:
                            break
            except Exception as e:
                logger.debug(f"Google News RSS search error ({cfg}): {e}")

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
    def generate_darknet_search_terms(query: str) -> List[str]:
        """
        Генерирует специализированные поисковые термы для сети Tor (Ahmia):
        1. Очищает от русских разговорных стоп-слов ('кто такой', 'что такое', 'расскажи про', 'за последний месяц').
        2. Извлекает латинские термины, аббревиатуры (0-day, CVE, RCE, Linux, SSH, Chrome, Python).
        3. Подбирает английские эквиваленты ключевых понятий для охвата англоязычных onion-ресурсов.
        """
        terms = []
        clean_q = query.strip()
        clean_without_stops = re.sub(
            r'(?i)\b(?:кто такой|кто такая|что такое|расскажи про|найди мне|найди|поиск|в браузере|в системе|в сети|информация о|информация про|сведения о|заявление|заявления|относительно|по поводу|за последний месяц|за последнюю неделю|за последнее время)\b',
            '',
            clean_q
        ).strip()
        clean_without_stops = re.sub(r'\s{2,}', ' ', clean_without_stops)

        latin_words = re.findall(r'[a-zA-Z0-9_-]+', clean_q)
        if len(latin_words) >= 1:
            terms.append(" ".join(latin_words))

        trans_map = {
            "гугл": "google chrome",
            "хром": "chrome",
            "браузер": "browser",
            "уязвимост": "vulnerability exploit 0-day",
            "эксплойт": "exploit 0-day",
            "взлом": "hack breach",
            "хакер": "hacker forum",
            "утечк": "leak database dump",
            "баз данных": "database breach",
            "парол": "passwords credentials dump",
            "владимир путин": "Vladimir Putin",
            "путин": "Putin",
            "трамп": "Trump",
            "иран": "Iran",
            "войн": "war",
            "украин": "Ukraine",
            "войск": "Russian army",
            "продвижен": "offensive advance",
            "сво": "Ukraine war",
            "дрон": "drone uav",
            "бпла": "uav drone",
            "оружи": "weapons",
            "росси": "Russia",
            "сша": "USA",
            "китай": "China",
            "санкци": "sanctions",
        }
        q_lower = clean_q.lower()
        translated_tokens = []
        for ru_stem, en_eq in trans_map.items():
            if ru_stem in q_lower:
                for tok in en_eq.split():
                    if tok not in translated_tokens:
                        translated_tokens.append(tok)

        if translated_tokens:
            terms.append(" ".join(translated_tokens[:4]))

        if clean_without_stops and clean_without_stops not in terms:
            terms.insert(0, clean_without_stops)
        elif clean_q not in terms:
            terms.insert(0, clean_q)

        return list(dict.fromkeys([t for t in terms if t.strip()]))[:3]

    async def _search_duckduckgo_onion(self, term: str, limit: int = 4, timeout: float = 12.0) -> List[Dict[str, Any]]:
        """Поиск ресурсов теневого сегмента .onion через поисковый индекс DuckDuckGo"""
        results = []
        queries_to_try = [f"{term} site:onion", f"\"{term}\" .onion", f"{term} .onion"]
        headers = {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ru,en;q=0.9",
        }
        client_configs = []
        if self.xray_http_proxy:
            client_configs.append({"proxy": self.xray_http_proxy, "timeout": timeout})
        client_configs.append({"timeout": timeout})

        for q in queries_to_try:
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(q)}"
            for cfg in client_configs:
                try:
                    async with httpx.AsyncClient(follow_redirects=True, headers=headers, **cfg) as client:
                        resp = await client.get(url)
                        if resp.status_code == 200 and resp.text:
                            soup = BeautifulSoup(resp.text, "html.parser")
                            for el in soup.find_all("div", class_="result"):
                                a = el.find("a", class_="result__a") or el.find("a")
                                snip = el.find("a", class_="result__snippet") or el.find("div", class_="result__snippet")
                                if a:
                                    href = a.get("href", "")
                                    if "uddg=" in href:
                                        m = re.search(r'uddg=([^&]+)', href)
                                        if m:
                                            href = urllib.parse.unquote(m.group(1))
                                    title = a.get_text(strip=True)
                                    snippet = snip.get_text(strip=True) if snip else ""
                                    if ".onion" in href or ".onion" in snippet or ".onion" in title:
                                        results.append({
                                            "title": title,
                                            "url": href,
                                            "snippet": snippet,
                                            "source_type": "darknet_onion"
                                        })
                                        if len(results) >= limit:
                                            break
                    if results:
                        break
                except Exception as e:
                    logger.debug(f"DuckDuckGo onion search ({cfg}) error: {e}")
            if results:
                break
        return results

    async def search_darknet(self, query: str, limit: int = 4, timeout: float = 45.0) -> List[Dict[str, Any]]:
        """
        Контур 3: Поиск в скрытом сегменте сети Tor (.onion / Darknet).
        Использует поисковую систему Ahmia (индексатор ресурсов .onion):
        - Сначала пробует через локальный Tor SOCKS5-прокси нативный .onion адрес Ahmia без Cloudflare (таймаут 45s).
        - Затем опрашивает безопасный шлюз ahmia.fi через Xray HTTP-прокси.
        - При блокировках или нуле находок опрашивает .onion индекс DuckDuckGo.
        - Использует умную трансляцию запросов на английский для англоязычного сегмента Tor.
        """
        results: List[Dict[str, Any]] = []
        search_terms = self.generate_darknet_search_terms(query)
        logger.info(f"🧅 [Контур 3 Tor] Запуск поиска в .onion по термам: {search_terms}")

        for term in search_terms:
            # 1. Попытка через Tor SOCKS5 прокси к официальному скрытому сервису Ahmia .onion
            if self.tor_socks_proxy:
                try:
                    logger.info(f"🧅 [Контур 3 Tor] Опрос скрытого сервиса Ahmia .onion (терм: '{term}', таймаут {timeout}s)...")
                    onion_search_url = f"http://juhanurmih5wuwwkgfte6nxha5f47vvai7bop5nm3uzrny3wfoisgd.onion/search/?q={urllib.parse.quote(term)}"
                    async with httpx.AsyncClient(proxy=self.tor_socks_proxy, timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
                        resp = await client.get(onion_search_url)
                        if resp.status_code == 200 and resp.text:
                            parsed = self._parse_ahmia_results(resp.text, limit=limit)
                            if parsed:
                                logger.info(f"🧅 [Контур 3 Tor] Успешно получено {len(parsed)} .onion сервисов через Tor SOCKS5")
                                results.extend(parsed)
                                break
                except Exception as e:
                    logger.warning(f"🧅 [Контур 3 Tor] Опрос .onion Ahmia через Tor ({term}): {e}")

            # 2. Если через Tor .onion не вышло или 0 находок — опрашиваем клирнет-индекс Ahmia через Xray HTTP-прокси
            if not results:
                ahmia_url = f"https://ahmia.fi/search/?q={urllib.parse.quote(term)}"
                headers = {
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
                client_configs = []
                if self.xray_http_proxy:
                    client_configs.append({"proxy": self.xray_http_proxy, "timeout": min(timeout, 12.0)})
                client_configs.append({"timeout": min(timeout, 12.0)})

                for cfg in client_configs:
                    try:
                        async with httpx.AsyncClient(follow_redirects=True, headers=headers, **cfg) as client:
                            resp = await client.get(ahmia_url)
                            if resp.status_code == 200 and resp.text:
                                parsed = self._parse_ahmia_results(resp.text, limit=limit)
                                if parsed:
                                    logger.info(f"🧅 [Контур 3 Tor] Успешно получено {len(parsed)} .onion ресурсов через клирнет Ahmia ({cfg})")
                                    results.extend(parsed)
                                    break
                    except Exception as e:
                        logger.debug(f"Direct Ahmia .onion search error ({cfg}): {e}")
                if results:
                    break

            # 3. Гарантированный фоллбэк: поиск .onion страниц через DuckDuckGo индекс
            if not results:
                try:
                    logger.info(f"🧅 [Контур 3 Tor] Запасной опрос через DuckDuckGo .onion индекс по терму: '{term}'")
                    ddg_onion = await self._search_duckduckgo_onion(term, limit=limit, timeout=12.0)
                    if ddg_onion:
                        logger.info(f"🧅 [Контур 3 Tor] Найдено {len(ddg_onion)} .onion ресурсов через DuckDuckGo индекс")
                        results.extend(ddg_onion)
                        break
                except Exception as ddg_err:
                    logger.debug(f"DDG onion fallback error: {ddg_err}")

        # Дедупликация по url
        seen = set()
        deduped = []
        for it in results:
            u = it.get("url")
            if u and u not in seen:
                seen.add(u)
                deduped.append(it)

        # Обогащаем результаты проверки по Траствикам (OnionTree, Daunt, The Hidden Wiki)
        return self.trust_manager.enrich_items(deduped[:limit])

    def _parse_ahmia_results(self, html_text: str, limit: int = 4) -> List[Dict[str, Any]]:
        """Парсинг результатов поиска по .onion страницам из разметки Ahmia"""
        results = []
        soup = BeautifulSoup(html_text, "html.parser")
        items = soup.find_all("li", class_="result") or soup.find_all("li")
        for it in items[:limit * 2]:
            a_tag = None
            h4 = it.find("h4")
            if h4:
                a_tag = h4.find("a")
            if not a_tag:
                a_tag = it.find("a")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            # Ahmia redirect url: /search/redirect?redirect_url=http://...onion/
            if "redirect_url=" in href:
                m = re.search(r'redirect_url=([^&]+)', href)
                if m:
                    href = urllib.parse.unquote(m.group(1))

            p_tag = it.find("p")
            snippet = p_tag.get_text(strip=True) if p_tag else ""

            # Извлекаем cite с .onion адресом если есть
            cite_tag = it.find("cite")
            if cite_tag and not href.endswith(".onion"):
                cite_text = cite_tag.get_text(strip=True)
                if ".onion" in cite_text:
                    href = cite_text

            if ".onion" in href or ".onion" in title or ".onion" in snippet:
                results.append({
                    "title": title or "Скрытый ресурс .onion",
                    "url": href,
                    "snippet": snippet,
                    "source_type": "darknet_onion"
                })
            if len(results) >= limit:
                break
        return results

    async def execute_deep_search(
        self,
        query: str,
        storage: Storage,
        llm_router: LLMRouter,
        include_darknet: Optional[bool] = None,
        refresh_collector_func=None
    ) -> Dict[str, Any]:
        """
        Главный оркестратор основательного поиска:
        - Опрашивает все 3 контура (Свои источники, Внешний Web, Darknet при включении).
        - Формирует развернутый контекст для ИИ.
        - Получает структурированный аналитический ответ с четким разделением по разделам.
        """
        if include_darknet is None:
            include_darknet = storage.is_darknet_search_enabled()

        use_google_ai = storage.is_google_ai_search_enabled()
        if llm_router and llm_router.config.gemini.api_key:
            self.google_grounding.api_key = llm_router.config.gemini.api_key

        clean_query = query.strip()
        search_core, target_channels = self.semantic_engine.extract_search_intent(clean_query)

        # Фаза 1: Декомпозиция запроса (Qwen / LLM Планировщик выявляет конкретные модели и системы)
        decomp = await self.decomposer.decompose(clean_query, llm_router=llm_router)
        specific_entities = decomp.get("specific_entities", [])
        search_queries = decomp.get("search_queries", [clean_query])

        # 1. Запуск поиска по всем контурам параллельно
        user_sources_task = self.search_user_sources(
            search_core,
            storage,
            limit=5,
            target_channels=target_channels,
            specific_entities=specific_entities
        )
        # Выбор наиболее релевантного поискового запроса для Web (сохраняющего ключевые токены)
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
        darknet_task = self.search_darknet(search_core, limit=4) if include_darknet else None

        tasks = [user_sources_task, web_sources_task]
        if darknet_task:
            tasks.append(darknet_task)

        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        user_sources = gathered[0] if isinstance(gathered[0], list) else []
        web_sources = gathered[1] if isinstance(gathered[1], list) else []
        darknet_sources = (gathered[2] if len(gathered) > 2 and isinstance(gathered[2], list) else []) if include_darknet else []

        # Если в собственных источниках совсем пусто — пробуем оперативно собрать свежие новости из каналов и повторить
        if len(user_sources) == 0 and refresh_collector_func:
            try:
                await refresh_collector_func()
                user_sources = await self.search_user_sources(
                    search_core,
                    storage,
                    limit=5,
                    target_channels=target_channels,
                    specific_entities=specific_entities
                )
            except Exception as ref_err:
                logger.warning(f"Refresh before deep search failed: {ref_err}")

        # 2. Формируем сырой фактологический контекст для LLM
        user_sources_text = ""
        if user_sources:
            lines = []
            for i, it in enumerate(user_sources, 1):
                ch = it.get("channel", "Канал")
                tit = it.get("title", "")
                cnt = it.get("content", "")
                url = it.get("url", "")
                pub = it.get("published_at", "")
                lines.append(f"{i}. [{ch}] ({pub}) {tit}\nТекст: {cnt}\nСсылка: {url}")
            user_sources_text = "\n\n".join(lines)
        else:
            user_sources_text = "ПОЛНОЕ ОТСУТСТВИЕ СОВПАДЕНИЙ: В подключенных каналах и лентах публикаций по теме запроса не найдено (база проверена)."

        web_sources_text = ""
        if web_sources:
            lines = []
            for i, it in enumerate(web_sources, 1):
                badge = it.get("trust_badge", "🌐 [Открытый Web]")
                lines.append(f"{i}. {badge} Заголовок: {it.get('title')}\nВыдержка: {it.get('snippet')}\nСсылка: {it.get('url')}")
            web_sources_text = "\n\n".join(lines)
        else:
            web_sources_text = "Внешний открытый веб-поиск не вернул прямых релевантных страниц."

        darknet_sources_text = ""
        if include_darknet:
            if darknet_sources:
                lines = []
                for i, it in enumerate(darknet_sources, 1):
                    badge = it.get("trust_badge", "🧅 [Скрытый сервис]")
                    warn = f"ВНИМАНИЕ: {it.get('trust_warning')}\n" if it.get("trust_warning") else ""
                    lines.append(f"{i}. {badge} Ресурс: {it.get('title')}\n{warn}Выдержка: {it.get('snippet')}\nOnion-адрес: {it.get('url')}")
                darknet_sources_text = "\n\n".join(lines)
            else:
                darknet_sources_text = "В проиндексированном теневом сегменте (.onion / Darknet) прямых упоминаний и активных утечек по теме не обнаружено."

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
        )

        if include_darknet:
            ai_prompt += (
                f"=== КОНТУР 3: ТЕНЕВОЙ СЕГМЕНТ СЕТИ TOR (.ONION / DARKNET) ===\n"
                f"{darknet_sources_text}\n\n"
            )

        ai_prompt += (
            "ИНСТРУКЦИЯ ПО ОФОРМЛЕНИЮ ОТВЕТА (ФОРМАТ OSINT-ДОСЬЕ И АНАЛИТИЧЕСКОГО СИНТЕЗА):\n"
            "Сформируй исчерпывающий, профессиональный OSINT-ответ в HTML-формате Telegram уровня Google AI Overviews / Perplexity Pro.\n\n"
            "СТРУКТУРА ОТВЕТА:\n"
            "1. 🎖 <b>[ФИО персоны / Название темы / Организация] — Аналитическое досье:</b>\n"
            "   - Четкое фактологическое определение: официальный статус, организация, государственная или военная должность, роль строго по теме запроса пользователя.\n\n"
            "2. 📌 <b>Ключевые этапы, факты или хроника событий:</b>\n"
            "   - Структурированные пункты проверенных фактов: официальные заявления ведомств, хроника событий, результаты расследований или ТТХ техники.\n\n"
            "3. 📍 <b>Текущий статус и официальная позиция (2024–2026):</b>\n"
            "   - Актуальное положение дел, последние публичные заявления руководства, ход расследования или текущая диспозиция.\n\n"
            "4. 📱 <b>Оперативный мониторинг (Telegram и СМИ):</b>\n"
            "   - Если в подключенных Telegram-каналах найдены публикации — приведи конкретные факты и ссылки на посты.\n"
            "   - Если совпадений в каналах нет — напиши строго одну фразу: <i>В недавних публикациях подключенных Telegram-каналов прямых упоминаний за последние дни не зафиксировано.</i> (Категорически запрещено выводить нерелевантные новости других тем!).\n"
            "   - Что сообщают внешние авторитетные медиа и пресса.\n\n"
            "5. 🔗 <b>Верифицированные источники:</b>\n"
            "   - Список источников с активными гиперссылками: <a href=\"URL\">Википедия: Название статьи</a>, <a href=\"URL\">Название СМИ</a>.\n"
            "   (Если в запросе исследовался теневой сегмент Tor — отдельным коротким пунктом 🧅 <b>Теневой сегмент (.onion):</b> укажи статус утечек).\n\n"
            "СТРОЖАЙШИЕ ПРАВИЛА И ЗАПРЕТЫ:\n"
            "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО путать персоналий и приписывать чужие псевдонимы или биографии (например: «Генерал Армагеддон» — это Сергей Суровикин, генерал армии ВС РФ, он не имеет отношения к ФСБ; главой ФСБ России с 2008 года является генерал армии Александр Бортников)!\n"
            "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выдумывать фиктивные имена людей или ложные ссылки на Википедию (вроде несуществующих статей)! Ссылайся ТОЛЬКО на реальные факты и предоставленные источники.\n"
            "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО заявлять, что «в интернете и мировых СМИ нет информации» о реальных известных военачальниках, политиках, исторических событиях или образцах техники! Используй предоставленные источники и свои фундаментальные экспертные знания ИИ для составления детального досье.\n"
            "- Используй ТОЛЬКО разрешенный Telegram HTML (<b>, <i>, <code>, <blockquote>, <a href=\"...\">).\n"
            "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕН Markdown: никаких **звездочек**, никаких ```кавычек```!\n"
            "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО оборачивать целые разделы, списки или пункты новостей в <blockquote> целиком! Весь текст пиши обычным текстом с <b>заголовками</b>.\n"
            "- Тег <blockquote> разрешен ИСКЛЮЧИТЕЛЬНО для коротких прямых цитат конкретных спикеров в 1-2 строки (например: <blockquote>«Цитата» — Спикер</blockquote>).\n"
            "- Категорически запрещено использовать шаблоны-заглушки вида '— @канал' или '[@канал](ссылка)'.\n"
            "- Ответ должен быть содержательным, плотным и фактологическим, без пустой воды."
        )

        prompts = load_prompts().get("system_prompts", {})
        system_prompt = prompts.get("deep_search") or (
            "Ты — ведущий OSINT-аналитик разведцентра «Агент Сноуден» (2026 год). "
            "Твоя задача — проводить глубокий многоконтурный поиск информации, сопоставлять данные "
            "из личных закрытых/отслеживаемых каналов пользователя с открытым вебом, поисковым индексом Google (Gemini Grounding) "
            "и теневым сегментом (.onion), и выдавать безупречно структурированный, доказательный отчет с первоисточниками."
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
            # Резервный формат если ИИ временно недоступен
            ai_text = self._build_fallback_summary(clean_query, user_sources, web_sources, darknet_sources, include_darknet, web_query=web_query)

        if search_banner and not ai_text.startswith("🌐 <b>Поиск в сети:"):
            ai_text = search_banner + ai_text

        return {
            "query": clean_query,
            "text": ai_text,
            "user_sources": user_sources,
            "web_sources": web_sources,
            "darknet_sources": darknet_sources,
            "include_darknet": include_darknet,
            "total_found": len(user_sources) + len(web_sources) + len(darknet_sources)
        }

    def _build_fallback_summary(
        self,
        query: str,
        user_sources: List[Dict[str, Any]],
        web_sources: List[Dict[str, Any]],
        darknet_sources: List[Dict[str, Any]],
        include_darknet: bool,
        web_query: Optional[str] = None
    ) -> str:
        """Резервный структурированный генератор если нейросеть недоступна"""
        lines = []
        if web_query and web_query != query:
            lines.append(f"🌐 <b>Поиск в сети:</b> <code>{html.escape(web_query)}</code>\n")
        lines.append(f"🔍 <b>Аналитическое досье по запросу:</b> <i>«{html.escape(query)}»</i>\n")

        # Если среди найденных источников есть Википедия или энциклопедическая справка — выводим фактологическое досье
        wiki_items = [it for it in web_sources if "википед" in it.get("title", "").lower()]
        other_web = [it for it in web_sources if it not in wiki_items]

        if wiki_items:
            w = wiki_items[0]
            w_tit = html.escape(w.get("title", ""))
            w_snip = html.escape(w.get("snippet", ""))
            w_url = w.get("url", "")
            lines.append("🎖 <b>Фактологическая справка:</b>")
            lines.append(f"<a href=\"{w_url}\"><b>{w_tit}</b></a>\n<blockquote>{w_snip}</blockquote>\n")

        # 1. Каналы
        lines.append("📱 <b>1. По данным ваших каналов и мониторинга:</b>")
        if user_sources:
            for it in user_sources:
                ch = html.escape(it.get("channel", "Канал"))
                pub = it.get("published_at", "")
                tit = html.escape(it.get("title", ""))
                cnt = html.escape(it.get("content", "")[:250])
                url = it.get("url", "")
                link_html = f' — <a href="{url}">Перейти к посту</a>' if url else ""
                lines.append(f"• <b>[{ch}]</b> {f'({pub})' if pub else ''}: {tit}\n<blockquote>{cnt}...</blockquote>{link_html}\n")
        else:
            lines.append("<i>В недавних публикациях подключенных Telegram-каналов прямых упоминаний не зафиксировано.</i>\n")

        # 2. Web
        lines.append("🌐 <b>2. Внешние авторитетные источники и пресса (Web):</b>")
        display_web = (wiki_items[1:] + other_web) if wiki_items else web_sources
        if display_web:
            for it in display_web:
                tit = html.escape(it.get("title", ""))
                snip = html.escape(it.get("snippet", ""))
                url = it.get("url", "")
                badge = html.escape(it.get("trust_badge", "🌐 [Web]"))
                lines.append(f"• {badge} <a href=\"{url}\"><b>{tit}</b></a>\n<blockquote>{snip}</blockquote>\n")
        else:
            lines.append("<i>Открытый веб-поиск не вернул прямых релевантных страниц.</i>\n")

        # 3. Darknet
        if include_darknet:
            lines.append("🧅 <b>3. Теневой сегмент (.onion / Darknet):</b>")
            if darknet_sources:
                for it in darknet_sources:
                    tit = html.escape(it.get("title", ""))
                    snip = html.escape(it.get("snippet", ""))
                    url = it.get("url", "")
                    badge = html.escape(it.get("trust_badge", "🧅 [Onion]"))
                    warn = f"\n⚠️ <i>{html.escape(it.get('trust_warning'))}</i>" if it.get("trust_warning") else ""
                    lines.append(f"• {badge} <b>{tit}</b>\n<code>{url}</code>{warn}\n<blockquote>{snip}</blockquote>\n")
            else:
                lines.append("<i>Сигналов и утечек в проиндексированном сегменте Tor не зафиксировано.</i>\n")

        return "\n".join(lines)
