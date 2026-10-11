import asyncio
import html
import logging
import os
import re
import urllib.parse
from typing import List, Dict, Any, Optional

import httpx
from bs4 import BeautifulSoup

from src.pipeline.semantic_search import SemanticSearchEngine
from src.llm.router import LLMRouter
from src.pipeline.storage import Storage
from src.pipeline.trust_manager import TrustManager

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

class DeepSearchEngine:
    """
    Движок многоконтурного глубокого поиска («Агент Сноуден»):
    1. Контур 1: Собственные подключенные источники пользователя (Telegram-каналы, RSS) с семантическим ранжированием.
    2. Контур 2: Внешние авторитетные мировые и профильные источники (Web-поиск).
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

    async def search_user_sources(self, query: str, storage: Storage, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Контур 1: Поиск по базе собственных каналов и лент пользователя.
        Использует семантический векторизованный поиск + полнотекстовое совпадение.
        """
        try:
            # Выбираем базу недавних публикаций
            feed_items = storage.get_news_feed(limit=500)
            search_items = storage.search_news_feed(query, limit=50)

            # Объединяем без дубликатов по url / item_hash
            seen = set()
            combined_candidates = []
            for it in list(search_items) + list(feed_items):
                key = it.get("url") or it.get("item_hash") or it.get("title")
                if key and key not in seen:
                    seen.add(key)
                    combined_candidates.append(it)

            if not combined_candidates:
                return []

            # Семантическое ранжирование
            ranked = await self.semantic_engine.search_posts(query, combined_candidates, limit=limit)
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

    async def search_web(self, query: str, limit: int = 4, timeout: float = 8.0) -> List[Dict[str, Any]]:
        """
        Контур 2: Внешний поиск по открытым авторитетным источникам (Web).
        Опрашивает поисковые шлюзы DuckDuckGo (HTML / Lite) без API-ключей.
        Использует локальный прокси Xray (127.0.0.1:10809) для надежного обхода блокировок.
        """
        results: List[Dict[str, Any]] = []
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        # 1. Пробуем DuckDuckGo HTML интерфейс (сначала через Xray-прокси, затем напрямую)
        ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        client_configs = []
        if self.xray_http_proxy:
            client_configs.append({"proxy": self.xray_http_proxy, "timeout": timeout})
        client_configs.append({"timeout": timeout})

        for cfg in client_configs:
            try:
                async with httpx.AsyncClient(follow_redirects=True, headers=headers, **cfg) as client:
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
            except Exception as e:
                logger.debug(f"Web search attempt ({cfg}) error: {e}")

        # 2. Если DuckDuckGo HTML не вернул результат, пробуем Lite версию
        if not results:
            lite_url = "https://lite.duckduckgo.com/lite/"
            data = {"q": query}
            for cfg in client_configs:
                try:
                    async with httpx.AsyncClient(follow_redirects=True, headers=headers, **cfg) as client:
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
                except Exception as e2:
                    logger.debug(f"DuckDuckGo Lite search attempt ({cfg}) error: {e2}")

        # Обогащаем результаты метаданными Траствиков (CVE, Exploit-DB, LostArmour)
        return self.trust_manager.enrich_items(results)

    async def search_darknet(self, query: str, limit: int = 4, timeout: float = 8.0) -> List[Dict[str, Any]]:
        """
        Контур 3: Поиск в скрытом сегменте сети Tor (.onion / Darknet).
        Использует поисковую систему Ahmia (индексатор ресурсов .onion):
        - Сначала пробует через локальный Tor SOCKS5-прокси (socks5://127.0.0.1:9050).
        - Если Tor-демон не поднят, опрашивает безопасный шлюз ahmia.fi через Xray-прокси.
        """
        results: List[Dict[str, Any]] = []

        # 1. Попытка через Tor SOCKS5 прокси (если поднят демон Tor на хосте или в Docker)
        socks_tried = False
        if self.tor_socks_proxy:
            try:
                onion_search_url = f"https://ahmia.fi/search/?q={urllib.parse.quote(query)}"
                async with httpx.AsyncClient(proxy=self.tor_socks_proxy, timeout=2.5, headers={"User-Agent": USER_AGENT}) as client:
                    resp = await client.get(onion_search_url)
                    if resp.status_code == 200 and resp.text:
                        results = self._parse_ahmia_results(resp.text, limit=limit)
                        socks_tried = True
            except Exception as e:
                logger.debug(f"Tor SOCKS5 proxy check/fetch ({self.tor_socks_proxy}): {e}")

        # 2. Если через SOCKS5 не вышло — опрашиваем индекс Ahmia через Xray HTTP-прокси (или напрямую)
        if not results and not socks_tried:
            ahmia_url = f"https://ahmia.fi/search/?q={urllib.parse.quote(query)}"
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            client_configs = []
            if self.xray_http_proxy:
                client_configs.append({"proxy": self.xray_http_proxy, "timeout": timeout})
            client_configs.append({"timeout": timeout})

            for cfg in client_configs:
                try:
                    async with httpx.AsyncClient(follow_redirects=True, headers=headers, **cfg) as client:
                        resp = await client.get(ahmia_url)
                        if resp.status_code == 200 and resp.text:
                            results = self._parse_ahmia_results(resp.text, limit=limit)
                            if results:
                                break
                except Exception as e:
                    logger.debug(f"Direct Ahmia .onion search error ({cfg}): {e}")

        # Обогащаем результаты проверки по Траствикам (OnionTree, Daunt, The Hidden Wiki)
        return self.trust_manager.enrich_items(results)

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

        clean_query = query.strip()

        # 1. Запуск поиска по всем контурам параллельно
        user_sources_task = self.search_user_sources(clean_query, storage, limit=5)
        web_sources_task = self.search_web(clean_query, limit=4)
        darknet_task = self.search_darknet(clean_query, limit=4) if include_darknet else None

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
                user_sources = await self.search_user_sources(clean_query, storage, limit=5)
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
            user_sources_text = "Прямых упоминаний в подключенных каналах и лентах не найдено (база просмотрена)."

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
        ai_prompt = (
            f"Тема поискового запроса: «{clean_query}»\n\n"
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
            "ИНСТРУКЦИЯ ПО ОФОРМЛЕНИЮ ОТВЕТА:\n"
            "Сформируй исчерпывающий, профессиональный OSINT-ответ в HTML-формате Telegram.\n"
            "Строго соблюдай следующую последовательность разделов:\n\n"
            "1. 📱 <b>По данным ваших каналов и мониторинга:</b>\n"
            "Сначала подробно распиши всё, что зафиксировано в собственных источниках пользователя. "
            "Выделяй важные формулировки и цитаты в <blockquote>...</blockquote>. "
            "Указывай названия каналов и оформляй ссылки на них: <a href=\"URL\">Название источника</a>. "
            "Если в собственных источниках ничего не найдено — прямо и кратко укажи это.\n\n"
            "2. 🌐 <b>По данным внешних авторитетных источников:</b>\n"
            "Что известно из внешнего интернета, мировых СМИ, профильных блогов или официальных сводок. "
            "Приводи факты с активными ссылками на первоисточники <a href=\"URL\">Источник</a>. "
            "Если источник верифицирован в Траствике (LostArmour, CVE, Exploit-DB), обязательно подчеркни его надежность.\n\n"
        )

        if include_darknet:
            ai_prompt += (
                "3. 🧅 <b>По данным мониторинга теневого сегмента (.onion):</b>\n"
                "Что обнаружено в сети Tor (форумы, утечки, теневые посты, зеркала). "
                "Если ресурс имеет пометку ⚠️ [Неверифицированный Onion] — обязательно предупреди пользователя о риске фишинга/скама. "
                "Если сигналов нет — честно отметь, что в даркнете следов не зафиксировано.\n\n"
            )

        ai_prompt += (
            "4. 🎯 <b>Аналитическое резюме:</b>\n"
            "Краткий синтез: сходятся ли данные из ваших каналов с внешней картиной, "
            "какова достоверность, есть ли признаки дезинформации или информационных вбросов.\n\n"
            "ВАЖНО:\n"
            "- Используй ТОЛЬКО разрешенный Telegram HTML (<b>, <i>, <code>, <blockquote>, <a href=\"...\">).\n"
            "- НИКОГДА не используй Markdown (никаких **звездочек** или ```тройных кавычек```).\n"
            "- Категорически запрещено использовать шаблоны-заглушки вида '— @канал' или '[@канал](ссылка)'.\n"
            "- Ответ должен быть содержательным, плотным и фактологическим, без пустой воды."
        )

        system_prompt = (
            "Ты — ведущий OSINT-аналитик разведцентра «Агент Сноуден». "
            "Твоя задача — проводить глубокий многоконтурный поиск информации, сопоставлять данные "
            "из личных закрытых/отслеживаемых каналов пользователя с открытым вебом и теневым сегментом (.onion), "
            "и выдавать безупречно структурированный, доказательный отчет с первоисточниками."
        )

        ai_res = await llm_router.generate_response(
            task="chat",
            user_prompt=ai_prompt,
            override_system_prompt=system_prompt
        )

        ai_text = ai_res.get("content", "").strip() if ai_res.get("success") else ""
        if not ai_text:
            # Резервный формат если ИИ временно недоступен
            ai_text = self._build_fallback_summary(clean_query, user_sources, web_sources, darknet_sources, include_darknet)

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
        include_darknet: bool
    ) -> str:
        """Резервный структурированный генератор если нейросеть недоступна"""
        lines = [f"🔍 <b>Результаты основательного поиска по запросу:</b> <i>«{html.escape(query)}»</i>\n"]

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
            lines.append("<i>В подключенных Telegram-каналах совпадений не обнаружено.</i>\n")

        # 2. Web
        lines.append("🌐 <b>2. Внешние авторитетные источники (Web):</b>")
        if web_sources:
            for it in web_sources:
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
