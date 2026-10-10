import logging
import os
import re
import html
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

from src.collectors.telegram_collector import TelegramWebCollector
from src.collectors.rss_collector import RSSCollector
from src.pipeline.storage import Storage
from src.llm.router import LLMRouter
from src.llm.prompts import (
    DIGEST_SYSTEM_PROMPT,
    build_digest_user_prompt,
    format_digest_to_collapsible_html,
    markdown_to_telegram_html
)
from src.config import load_preferences, load_sources

logger = logging.getLogger(__name__)

class DigestBuilder:
    def __init__(self, storage: Storage, router: LLMRouter):
        self.storage = storage
        self.router = router
        self.digests_dir = Path("data/digests")
        self.digests_dir.mkdir(parents=True, exist_ok=True)

    def _is_blacklisted(self, text: str, blacklist: List[str]) -> bool:
        lower_text = text.lower()
        # Если в публикации содержатся явные признаки полезного софта / open-source утилит / репозиториев,
        # защищаем её от случайного отсечения по словам вроде "реклама", "промокод" или "ссылка"
        software_indicators = [
            "github.com", "open source", "релиз", "утилита", "софт", "аналог",
            "бесплатный", "репозиторий", "версия", "photocraft", "wordcraft", "инструмент", "soft"
        ]
        if any(ind in lower_text for ind in software_indicators):
            # Проверяем только жесткий скам/казино
            hard_scam = ["казино", "ставки на спорт", "1win", "1xbet", "порно", "крипто-скам"]
            return any(scam in lower_text for scam in hard_scam)

        for word in blacklist:
            if word.lower() in lower_text:
                return True
        return False

    def _stratified_balance_items(self, items: List[Dict[str, Any]], target_count: int = 20) -> List[Dict[str, Any]]:
        """
        Стратифицированная балансировка выборки новостей по ключевым доменам:
        - Полезный софт & OpenSource (IT_Shelter, GitHub Radar, OpenNet)
        - Геополитика & СВО (Дядя Батя, Росснейм, Рыбарь, РИА, ТАСС)
        - Кибербезопасность & IT (Хакер, CVE, OSINT)
        - Нейросети & Железо (AI, GPU, чипы)
        - Регионы и Экономика
        """
        categorized: Dict[str, List[Dict[str, Any]]] = {}
        for it in items:
            cat = it.get("category", "Общее")
            categorized.setdefault(cat, []).append(it)

        priority_keywords = [
            "софт", "soft", "opensource", "open-source", "утилит", "github", "разработк",
            "сво", "политик", "украин", "юг", "регион", "кибер", "безопасн", "ии", "нейро", "аналитик"
        ]
        sorted_cats = sorted(
            categorized.keys(),
            key=lambda c: any(pk in c.lower() for pk in priority_keywords),
            reverse=True
        )

        balanced: List[Dict[str, Any]] = []
        for round_idx in range(4):
            for cat in sorted_cats:
                cat_items = categorized[cat]
                if round_idx < len(cat_items) and len(balanced) < target_count:
                    balanced.append(cat_items[round_idx])

        if len(balanced) < target_count:
            for it in items:
                if it not in balanced and len(balanced) < target_count:
                    balanced.append(it)

        return balanced

    async def curate_items_with_ai(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Этап 1: Быстрый ИИ-Куратор (AI Content Curator).
        Оценивает пул собранных публикаций и отбирает от 12 до 20 самых ценных материалов,
        гарантируя присутствие полезного софта/утилит, геополитики/СВО, кибербезопасности и ИИ,
        и отсекая инфошум, рекламу и дубликаты.
        """
        if len(items) <= 15:
            return items

        from src.llm.prompts import (
            AI_CURATOR_SYSTEM_PROMPT,
            build_curator_user_prompt,
            parse_curator_selection
        )

        prefs = load_preferences()
        interests = prefs.get("interests", [])
        learned_prefs = self.storage.get_learned_preferences_summary()

        curator_user_prompt = build_curator_user_prompt(
            candidate_items=items,
            user_interests=interests,
            learned_preferences=learned_prefs
        )

        try:
            logger.info(f"Запуск ИИ-куратора для пула из {len(items)} публикаций...")
            res = await self.router.generate_response(
                task="curator",
                user_prompt=curator_user_prompt,
                override_system_prompt=AI_CURATOR_SYSTEM_PROMPT
            )

            if res.get("success"):
                content = res.get("content", "")
                selected_ids = parse_curator_selection(content, len(items))
                selected_items = [items[i - 1] for i in selected_ids if 1 <= i <= len(items)]

                # Если куратор отобрал достаточное число качественных статей (>= 10)
                if len(selected_items) >= 10:
                    logger.info(f"ИИ-куратор успешно отобрал {len(selected_items)} публикаций.")
                    return selected_items
                elif len(selected_items) > 0:
                    logger.info(f"ИИ-куратор отобрал {len(selected_items)} публикаций. Дополняем стратифицированной выборкой...")
                    fallback_pool = self._stratified_balance_items(items, target_count=20)
                    combined = list(selected_items)
                    seen_urls = {it.get("url") for it in combined if it.get("url")}
                    for it in fallback_pool:
                        u = it.get("url")
                        if u not in seen_urls:
                            combined.append(it)
                            if u:
                                seen_urls.add(u)
                        if len(combined) >= 20:
                            break
                    return combined
        except Exception as e:
            logger.warning(f"Ошибка вызова ИИ-куратора: {e}")

        # Фолбэк на стратифицированный отбор при сбое или недоступности ИИ
        logger.info("Применение резервного стратифицированного отбора публикаций...")
        return self._stratified_balance_items(items, target_count=20)

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

        # Сохраняем свежие публикации в ленту новостей для Android / Web API
        if unseen:
            self.storage.save_news_items(unseen)

        # Фильтрация по стоп-словам из preferences
        prefs = load_preferences()
        blacklist = prefs.get("filters", {}).get("blacklist", [])

        clean_items = [
            item for item in unseen
            if not self._is_blacklisted(item.get("content", ""), blacklist)
        ]
        return clean_items

    def _convert_md_to_html_body(self, text: str) -> str:
        lines = text.strip().split("\n")
        output = []
        in_table = False
        table_lines = []
        in_code = False
        code_lines = []
        code_lang = ""

        def flush_table(tbl):
            if not tbl:
                return ""
            def split_row(l):
                return [c.strip() for c in l.strip("|").split("|")]
            hdrs = split_row(tbl[0])
            rows = [split_row(l) for l in tbl[2:]] if len(tbl) > 2 else []
            res = ["<div class='table-wrapper'><table><thead><tr>"]
            for h in hdrs:
                res.append(f"<th>{html.escape(h)}</th>")
            res.append("</tr></thead><tbody>")
            for r in rows:
                res.append("<tr>")
                for c in r:
                    c_html = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2" target="_blank" rel="noopener">\1</a>', c)
                    c_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', c_html)
                    res.append(f"<td>{c_html}</td>")
                res.append("</tr>")
            res.append("</tbody></table></div>")
            return "".join(res)

        for line in lines:
            if line.startswith("```"):
                if not in_code:
                    in_code = True
                    code_lang = line.replace("```", "").strip()
                    code_lines = []
                else:
                    in_code = False
                    code_content = html.escape("\n".join(code_lines))
                    output.append(f"<pre class='code-block'><code class='{code_lang}'>{code_content}</code></pre>")
                continue

            if in_code:
                code_lines.append(line)
                continue

            if re.match(r'^\s*\|.+\|\s*$', line):
                in_table = True
                table_lines.append(line)
                continue
            elif in_table:
                output.append(flush_table(table_lines))
                table_lines = []
                in_table = False

            m_h = re.match(r'^(#{1,6})\s+(.+)$', line)
            if m_h:
                lvl = len(m_h.group(1))
                h_text = m_h.group(2)
                output.append(f"<h{lvl}>{html.escape(h_text)}</h{lvl}>")
                continue

            if not line.strip():
                output.append("")
                continue

            m_li = re.match(r'^\s*[\*\-]\s+(.+)$', line)
            if m_li:
                item_text = m_li.group(1)
                item_text = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2" target="_blank" rel="noopener">\1</a>', item_text)
                item_text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', item_text)
                output.append(f"<li>{item_text}</li>")
                continue

            p_text = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2" target="_blank" rel="noopener">\1</a>', line)
            p_text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', p_text)
            output.append(f"<p>{p_text}</p>")

        if in_table:
            output.append(flush_table(table_lines))

        return "\n".join(output)

    def _generate_standalone_html(
        self,
        report_md: str,
        date_str: str,
        provider: str,
        model: str,
        items_count: int,
        latency: float
    ) -> str:
        body_html = self._convert_md_to_html_body(report_md)

        html_template = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Технологический дайджест — {date_str}</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #0b0c10;
      --card-bg: #14151c;
      --border: #232530;
      --border-focus: #3b3e52;
      --accent-cyan: #06b6d4;
      --accent-amber: #f59e0b;
      --accent-green: #10b981;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: 'Inter', -apple-system, sans-serif;
      padding: 16px;
      line-height: 1.6;
      max-width: 860px;
      margin: 0 auto;
    }}
    .header-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px 20px;
      margin-bottom: 20px;
    }}
    .header-title {{
      font-size: 1.35rem;
      font-weight: 700;
      color: #fff;
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.78rem;
    }}
    .badge {{
      background: #1c1e28;
      border: 1px solid var(--border);
      padding: 4px 10px;
      border-radius: 6px;
      color: var(--text-muted);
    }}
    .badge.cyan {{ color: var(--accent-cyan); border-color: rgba(6, 182, 212, 0.3); }}
    .badge.amber {{ color: var(--accent-amber); border-color: rgba(245, 158, 11, 0.3); }}
    .badge.green {{ color: var(--accent-green); border-color: rgba(16, 185, 129, 0.3); }}

    .content {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 24px;
    }}
    h1, h2, h3, h4 {{
      color: #fff;
      font-weight: 600;
      margin: 22px 0 12px;
      line-height: 1.3;
    }}
    h1 {{ font-size: 1.3rem; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}
    h2 {{ font-size: 1.15rem; color: var(--accent-amber); }}
    h3 {{ font-size: 1.02rem; color: var(--accent-cyan); }}
    p {{ margin-bottom: 12px; font-size: 0.94rem; color: #cbd5e1; }}
    li {{ margin-left: 20px; margin-bottom: 6px; font-size: 0.92rem; color: #cbd5e1; }}
    strong {{ color: #fff; font-weight: 600; }}
    a {{ color: var(--accent-cyan); text-decoration: none; word-break: break-all; }}
    a:hover {{ text-decoration: underline; }}

    /* Таблицы */
    .table-wrapper {{
      width: 100%;
      overflow-x: auto;
      margin: 18px 0;
      border-radius: 8px;
      border: 1px solid var(--border);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
      text-align: left;
    }}
    th {{
      background: #1b1d28;
      color: var(--accent-amber);
      font-family: 'JetBrains Mono', monospace;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }}
    td {{
      padding: 10px 12px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      color: #cbd5e1;
    }}
    tr:nth-child(even) {{ background: rgba(255, 255, 255, 0.02); }}
    tr:hover {{ background: rgba(255, 255, 255, 0.04); }}

    /* Блоки кода */
    .code-block {{
      background: #090a0f;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      overflow-x: auto;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.82rem;
      color: #38bdf8;
      margin: 14px 0;
    }}

    .footer {{
      text-align: center;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.75rem;
      color: var(--text-muted);
      padding: 16px 0 30px;
    }}
  </style>
</head>
<body>
  <div class="header-card">
    <div class="header-title">⚡️ AI & TECH ANALYTICAL DIGEST</div>
    <div class="badges">
      <div class="badge green">🗓 {date_str}</div>
      <div class="badge cyan">🧠 {provider.upper()} ({model})</div>
      <div class="badge amber">📡 Публикаций: {items_count}</div>
      <div class="badge">⏱ {latency} сек.</div>
    </div>
  </div>

  <main class="content">
    {body_html}
  </main>

  <footer class="footer">
    Автономный ИИ-хаб • 2026 • Сгенерировано на ноутбуке-сервере
  </footer>

  <script>
    if (window.Telegram && window.Telegram.WebApp) {{
      window.Telegram.WebApp.ready();
      window.Telegram.WebApp.expand();
    }}
  </script>
</body>
</html>
"""
        return html_template

    async def generate_digest(self, force_all: bool = False) -> Dict[str, Any]:
        items = await self.collect_fresh_news()

        if not items:
            return {
                "success": True,
                "text": "📭 Нет новых непрочитанных новостей по вашим темам.",
                "telegram_post_html": "📭 Нет новых непрочитанных новостей по вашим темам.",
                "provider": "none",
                "items_count": 0,
                "lead_image_url": ""
            }

        # ЭТАП 1: ИИ-Куратор (AI Content Curator)
        # Отбирает топ-15–20 самых ценных материалов (включая полезный софт, утилиты, фронт, геополитику, ИИ)
        curated_items = await self.curate_items_with_ai(items)
        logger.info(f"Выборка сформирована: {len(curated_items)} публикаций передаются генератору дайджеста.")

        # Извлекаем разнообразные изображения/обложки видео из отобранных куратором публикаций
        source_images = []
        seen_channels = set()
        for it in curated_items:
            img = it.get("image_url")
            ch = it.get("channel", "")
            if img and img.startswith("http") and img not in source_images:
                # Отдаем приоритет разнообразию источников
                if ch not in seen_channels or len(seen_channels) >= 5:
                    source_images.append(img)
                    seen_channels.add(ch)
                    if len(source_images) >= 6:
                        break

        # Добираем до 6 изображений из общего пула, если из кураторской выборки набралось меньше
        if len(source_images) < 4:
            for it in items:
                img = it.get("image_url")
                if img and img.startswith("http") and img not in source_images:
                    source_images.append(img)
                    if len(source_images) >= 6:
                        break

        lead_image_url = source_images[0] if source_images else ""

        # ЭТАП 2: LLM Синтезатор аналитического дайджеста
        # Формируем сырой текст для LLM с полной фактурой
        raw_chunks = []
        for i, it in enumerate(curated_items, 1):
            raw_chunks.append(
                f"[{i}] Источник: {it.get('channel', 'Канал')} ({it.get('category', 'Общее')})\n"
                f"Заголовок: {it.get('title', 'Новость / Релиз')}\n"
                f"Текст публикации: {it.get('content', '')[:900]}\n"
                f"Ссылка: {it.get('url', '')}\n"
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

        logger.info("Отправка сформированного пакета новостей и софта в LLM...")
        llm_res = await self.router.generate_response(task="digest", user_prompt=user_prompt)

        if not llm_res.get("success"):
            return {
                "success": False,
                "text": f"⚠️ Ошибка генерации дайджеста: {llm_res.get('content')}",
                "telegram_post_html": f"⚠️ Ошибка генерации дайджеста: {llm_res.get('content')}",
                "provider": "error",
                "items_count": len(items),
                "lead_image_url": lead_image_url
            }

        content = llm_res.get("content", "").strip()
        provider = llm_res.get("provider", "unknown")
        model = llm_res.get("model", "unknown")
        latency = llm_res.get("latency", 0.0)

        now_dt = datetime.now()
        timestamp_str = now_dt.strftime("%Y-%m-%d_%H%M%S")
        date_display = now_dt.strftime("%d.%m.%Y")

        # 1. Извлекаем лаконичный Telegram-пост с нативными сворачиваемыми блоками <blockquote expandable>
        from src.llm.prompts import extract_clean_digest_post, clean_markdown_for_document
        telegram_post_html = extract_clean_digest_post(content, date_display)

        # Очищаем Markdown-документ от обрамляющих ``` и лишних отступов (убираем черный фон в Telegram)
        clean_md = clean_markdown_for_document(content)

        # Добавляем футер с моделью и временем
        provider_footer = f"\n\n🤖 <b>{provider.upper()}</b> (<code>{model}</code>) • ⏱ {latency} сек."
        if llm_res.get("fallback_occurred"):
            provider_footer += " <i>(резервный шлюз)</i>"
        telegram_post_html += provider_footer

        # 2. Генерируем полные файлы отчета (.md и .html)
        md_filename = f"Digest_{date_display.replace('.', '-')}_{timestamp_str}.md"
        html_filename = f"Digest_{date_display.replace('.', '-')}_{timestamp_str}.html"

        md_path = self.digests_dir / md_filename
        html_path = self.digests_dir / html_filename

        # Записываем чистый Markdown отчет (без черных блоков кода)
        md_path.write_text(clean_md, encoding="utf-8")

        # Записываем автономный HTML документ для архива
        html_content = self._generate_standalone_html(
            report_md=clean_md,
            date_str=date_display,
            provider=provider,
            model=model,
            items_count=len(items),
            latency=latency
        )
        html_path.write_text(html_content, encoding="utf-8")

        # Сохраняем в SQLite
        self.storage.save_full_digest(
            date_str=date_display,
            provider=provider,
            model=model,
            post_html=telegram_post_html,
            report_md=clean_md,
            report_html_path=str(html_path),
            report_md_path=str(md_path),
            lead_image_url=lead_image_url
        )
        self.storage.save_digest(telegram_post_html, provider, len(items))
        self.storage.mark_items_as_seen(items)

        return {
            "success": True,
            "text": telegram_post_html,
            "telegram_post_html": telegram_post_html,
            "full_report_md": clean_md,
            "full_report_html_path": str(html_path),
            "full_report_md_path": str(md_path),
            "lead_image_url": lead_image_url,
            "source_images": source_images,
            "provider": provider,
            "model": model,
            "latency": latency,
            "items_count": len(items)
        }
