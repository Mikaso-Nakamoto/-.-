import re
import html
from typing import List, Optional

CIRCLED_NUMS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩", "⑪", "⑫", "⑬", "⑭", "⑮", "⑯", "⑰", "⑱", "⑲", "⑳"]

def clean_telegram_markdown(text: str) -> str:
    """
    Ретро-совместимая очистка Markdown (заменяет ### на *).
    Для корректного рендеринга рекомендуется использовать markdown_to_telegram_html.
    """
    if not text:
        return ""
    cleaned = re.sub(r'(?m)^#{1,6}\s*(.+)$', r'*\1*', text)
    cleaned = re.sub(r'#{3,}', '', cleaned)
    return cleaned.strip()

def markdown_to_telegram_html(text: str) -> str:
    """
    Преобразует Markdown в валидный HTML для Telegram Bot API (parse_mode="HTML").
    Решает проблему падений парсера Telegram на вложенных звездочках (* **bold**),
    символах подчеркивания в идентификаторах и решетках.
    """
    if not text:
        return ""

    # 0. Очистка от шаблонных галлюцинаций моделей (заглушки @канал, [@канал](ссылка))
    if "@канал" in text or "ссылка" in text:
        text = re.sub(r'—?\s*Источники:\s*\[?@?канал\d*\]?\(?[^\)\n]*\)?', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\[@?канал\d*\]\((?:ссылка|url\d*)\)', '', text, flags=re.IGNORECASE)
        text = re.sub(r'—?\s*@канал\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\[ссылка\]\(ссылка\)', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\n{3,}', '\n\n', text)

    # 0.1 Очистка от пустых галлюцинированных ссылок и скобок (<>, &lt;&gt;)
    text = re.sub(r'(?:\s*&lt;&gt;\s*|\s*<>\s*)+', '\n', text)
    text = re.sub(r'\[([^\]]*)\]\((?:https?://)?\)', r'\1', text)

    # 0.2 Устранение двойного HTML-экранирования (&amp;amp; -> &, &amp;quot; -> ")
    text = text.replace("&amp;amp;", "&").replace("&amp;quot;", '"').replace("&amp;lt;", "<").replace("&amp;gt;", ">")

    # 1. Сохраняем блоки кода ```lang ... ```
    code_blocks = []
    def save_pre(m):
        idx = len(code_blocks)
        lang = m.group(1) or ""
        code = m.group(2)
        code_blocks.append(f"<pre><code class=\"{lang}\">{html.escape(code)}</code></pre>")
        return f"XZXCODEBLOCK{idx}XZX"

    text = re.sub(r'```([a-zA-Z0-9_-]*)\n?(.*?)```', save_pre, text, flags=re.DOTALL)

    # 2. Сохраняем Markdown таблицы в code_blocks как моноширинный преформатированный текст
    lines = text.split("\n")
    in_table = False
    table_lines = []
    processed_lines = []
    for line in lines:
        if re.match(r'^\s*\|.+\|\s*$', line):
            in_table = True
            table_lines.append(line)
        else:
            if in_table:
                idx = len(code_blocks)
                code_blocks.append(f"<pre><code>{html.escape(chr(10).join(table_lines))}</code></pre>")
                processed_lines.append(f"XZXCODEBLOCK{idx}XZX")
                table_lines = []
                in_table = False
            processed_lines.append(line)
    if in_table:
        idx = len(code_blocks)
        code_blocks.append(f"<pre><code>{html.escape(chr(10).join(table_lines))}</code></pre>")
        processed_lines.append(f"XZXCODEBLOCK{idx}XZX")
    text = "\n".join(processed_lines)

    # 3. Сохраняем инлайн-код `...`
    inline_codes = []
    def save_inline(m):
        idx = len(inline_codes)
        code = m.group(1)
        inline_codes.append(f"<code>{html.escape(code)}</code>")
        return f"XZXINLINECODE{idx}XZX"

    text = re.sub(r'`([^`]+)`', save_inline, text)

    # 4. Сохраняем уже присутствующие разрешенные теги Telegram
    allowed_tags = []
    def save_allowed(m):
        idx = len(allowed_tags)
        allowed_tags.append(m.group(0))
        return f"XZXALLOWEDTAG{idx}XZX"

    text = re.sub(r'<(/?(?:blockquote|b|i|u|s|a|pre|code|tg-spoiler)(?:\s+[^>]*)?)>', save_allowed, text, flags=re.IGNORECASE)

    # 4.1. Преобразуем цитаты Markdown (> цитата -> <blockquote>цитата</blockquote>)
    def save_md_quote(m):
        q_lines = [re.sub(r'^\s*>\s?', '', l) for l in m.group(0).split('\n') if l.strip()]
        q_text = "\n".join(q_lines).strip()
        if not q_text:
            return ""
        # Если блок содержит заголовки разделов (1. 📱, 2. 🌐, 3. 🧅, 4. 🎯) или длинный текст > 400 символов,
        # НЕ оборачиваем его в цитату целиком, а возвращаем обычным текстом, чтобы избежать цитатной рамки вокруг раздела
        if re.search(r'(?i)(?:[1-4]\.\s*(?:📱|🌐|🧅|🎯|По\s+данным)|Аналитическое\s+резюме)', q_text) or len(q_text) > 400:
            return q_text
        # 1. Обрабатываем ссылки внутри цитаты [текст](url)
        q_text = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2">\1</a>', q_text)
        # 2. Обрабатываем жирный шрифт **текст**
        q_text = re.sub(r'\*\*([^\*]+?)\*\*', r'<b>\1</b>', q_text)
        # 3. Экранируем спецсимволы вне сформированных тегов
        parts = re.split(r'(<[^>]+>)', q_text)
        safe_parts = []
        for p in parts:
            if re.match(r'^<[^>]+>$', p):
                safe_parts.append(p)
            else:
                safe_parts.append(html.escape(p))
        content = "".join(safe_parts).strip()
        if not content:
            return ""
        idx = len(allowed_tags)
        allowed_tags.append(f"<blockquote>{content}</blockquote>")
        return f"XZXALLOWEDTAG{idx}XZX"

    text = re.sub(r'(?m)^(?:>[^\n]*\n?)+', save_md_quote, text)

    # 5. Экранируем оставшиеся HTML-символы (<, >, &)
    text = html.escape(text)

    # 6. Преобразуем ссылки Markdown [текст](url)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2">\1</a>', text)

    # 7. Заголовки (### Заголовок -> <b>Заголовок</b>)
    text = re.sub(r'(?m)^#{1,6}\s*(.+)$', r'<b>\1</b>', text)

    # 8. Жирный шрифт: **текст** или __текст__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text, flags=re.DOTALL)

    # 9. Списки: * или - со звездочками (* **Заголовок:** -> • <b>Заголовок:</b>)
    text = re.sub(r'(?m)^\s*[\*\-]\s+', '• ', text)

    # 10. Курсив: *курсив* или _курсив_
    text = re.sub(r'(?<!\w)\*([^\*\n]+)\*(?!\w)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_([^_\n]+)_(?!\w)', r'<i>\1</i>', text)

    # 11. Возвращаем разрешенные теги
    for i, tag in enumerate(allowed_tags):
        text = text.replace(f"XZXALLOWEDTAG{i}XZX", tag)

    # 12. Возвращаем инлайн-код и блоки кода
    for i, code in enumerate(inline_codes):
        text = text.replace(f"XZXINLINECODE{i}XZX", code)
    for i, block in enumerate(code_blocks):
        text = text.replace(f"XZXCODEBLOCK{i}XZX", block)

    # 13. Распаковываем цитаты, если модель ошибочно обернула целый раздел в blockquote
    def unwrap_bad_blockquote(m):
        inner = m.group(1)
        if re.search(r'(?i)(?:[1-4]\.\s*(?:📱|🌐|🧅|🎯|По\s+данным)|Аналитическое\s+резюме)', inner) or len(inner) > 500:
            return inner
        return m.group(0)

    text = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', unwrap_bad_blockquote, text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<blockquote[^>]*>\s*</blockquote>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()

def format_digest_to_collapsible_html(raw_text: str) -> str:
    """
    Преобразует текст дайджеста в Telegram-сообщение с нативными сворачиваемыми блоками
    <blockquote expandable><b>① Заголовок</b>\nПодробности...</blockquote> (стиль Image 2).
    """
    if not raw_text:
        return ""

    # Если LLM уже сформировал <blockquote expandable>, просто чистим Markdown внутри
    if "<blockquote expandable>" in raw_text:
        return markdown_to_telegram_html(raw_text)

    # Отделяем хэштеги в конце текста, если они есть
    hashtags = ""
    ht_match = re.search(r'(\n+(?:#[a-zA-Z0-9а-яА-Я_]+\s*)+)$', raw_text.strip())
    if ht_match:
        hashtags = ht_match.group(1).strip()
        raw_text = raw_text[:ht_match.start()].strip()

    # Поиск нумерованных секций: 1. Заголовок или ① Заголовок
    pattern = r'(?m)^(?:📂\s*|📌\s*|⚡️\s*)?(?:[0-9]{1,2}[\.\)]|[①-⑳])\s*(?:[\*\_]{0,2})([^\n\*\_]+)(?:[\*\_]{0,2})\s*\n'
    parts = re.split(pattern, raw_text)

    if len(parts) >= 3:
        preamble = markdown_to_telegram_html(parts[0].strip())
        sections = []
        num_idx = 0
        for i in range(1, len(parts), 2):
            title = parts[i].strip()
            body = parts[i+1].strip() if i+1 < len(parts) else ""
            c_num = CIRCLED_NUMS[num_idx] if num_idx < len(CIRCLED_NUMS) else f"[{num_idx+1}]"
            num_idx += 1
            body_html = markdown_to_telegram_html(body)
            clean_title = html.escape(title.replace("*", "").replace("#", "").strip())
            sections.append(f"<blockquote expandable><b>{c_num} {clean_title}</b>\n{body_html}</blockquote>")

        result = (preamble + "\n\n" if preamble else "") + "\n\n".join(sections)
        if hashtags:
            result += f"\n\n{html.escape(hashtags)}"
        return result

    return markdown_to_telegram_html(raw_text)

def clean_markdown_for_document(text: str) -> str:
    """
    Очищает Markdown-документ от артефактов, вызывающих отображение
    черного фона (блоков кода) во встроенном ридере Telegram:
    - Удаляет обрамляющие тройные кавычки (```markdown ... ```).
    - Удаляет случайные блоки кода, в которые LLM мог завернуть обычные секции.
    - Убирает отступы в 4+ пробелов в начале абзацев (Markdown воспринимает их как <pre><code>).
    - Гарантирует аккуратный, чистый Markdown с нормальными шрифтами и заголовками.
    """
    if not text:
        return ""

    # 1. Удаляем обрамляющие ```markdown и завершающие ```
    cleaned = re.sub(r'^\s*```(?:markdown|md)?\s*\n', '', text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\n```\s*$', '', cleaned.strip())

    # 2. Удаляем системные теги промпта, если LLM их сгенерировал
    cleaned = re.sub(r'={2,}\s*(?:TELEGRAM_POST|FULL_REPORT)\s*={2,}', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'(?m)^(?:TELEGRAM_POST|FULL_REPORT)\s*$', '', cleaned, flags=re.IGNORECASE)

    # 3. Нормализуем строки: удаляем лишние 4-пробельные отступы перед обычным текстом
    # (так как в Markdown 4 пробела = блок кода с черным фоном)
    lines = cleaned.split("\n")
    fixed_lines = []
    in_code_block = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            fixed_lines.append(line)
            continue

        if not in_code_block:
            # Если строка начинается с 4+ пробелов и не является элементом вложенного списка
            if re.match(r'^\s{4,}', line) and not line.strip().startswith(("-", "*", "+", "|", "1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.")):
                fixed_lines.append(line.lstrip())
            else:
                fixed_lines.append(line)
        else:
            fixed_lines.append(line)

    return "\n".join(fixed_lines).strip()

def convert_markdown_tables_to_text(text: str) -> str:
    """
    Вместо грубого стирания Markdown-таблиц (что приводило к пустому телу дайджеста),
    конвертирует строки таблицы в аккуратные пункты со структурированными полями.
    """
    lines = text.split('\n')
    out_lines = []
    headers = []
    in_table = False

    for line in lines:
        s = line.strip()
        if s.startswith('|') and s.endswith('|') and len(s) > 2:
            cells = [c.strip() for c in s.strip('|').split('|')]
            if all(re.match(r'^:?-+:?$', c) for c in cells if c):
                continue
            if not in_table or not headers:
                headers = cells
                in_table = True
                continue

            parts = []
            for h, val in zip(headers, cells):
                if not val or val == '-':
                    continue
                h_clean = re.sub(r'[*#_]+', '', h).strip()
                val_clean = val.strip()
                if h_clean.lower() in ['№', 'id', 'номер']:
                    continue
                if h_clean:
                    parts.append(f"<b>{h_clean}:</b> {val_clean}")
                else:
                    parts.append(val_clean)
            if parts:
                out_lines.append("• " + " — ".join(parts))
        else:
            in_table = False
            headers = []
            out_lines.append(line)

    return '\n'.join(out_lines)


def extract_clean_digest_post(raw_text: str, date_str: str) -> str:
    """
    Формирует красивый, структурированный Telegram-пост из любого вывода LLM:
    - Никаких служебных меток TELEGRAM_POST, FULL_REPORT, ===.
    - Markdown-таблицы аккуратно конвертируются в читаемый список фактов.
    - Каждая новость оформляется с нативным выделением цитат <blockquote>...</blockquote>.
    - Никаких обрезок на 300 символов: полный охват ключевых событий суток.
    - Автоматическое разделение и ссылка на полный .MD документ при превышении лимита Telegram.
    """
    if not raw_text:
        return f"<b>⚡️ АНАЛИТИЧЕСКИЙ ДАЙДЖЕСТ — {date_str}</b>\n\nНет данных для отображения."

    cleaned = raw_text.replace("&lt;", "<").replace("&gt;", ">")
    cleaned = re.sub(r'={2,}\s*(?:TELEGRAM_POST|FULL_REPORT)\s*={2,}', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'(?m)^(?:TELEGRAM_POST|FULL_REPORT)\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\([Лл]аконичный пост[^\)]*\)', '', cleaned).strip()
    cleaned = re.sub(r'(?mi)^#*\s*⚡️?\s*АНАЛИТИЧЕСКИЙ ДАЙДЖЕСТ[^\n]*\n*', '', cleaned)
    cleaned = re.sub(r'(?mi)^Краткий обзор ключевых событий[^\n]*\n*', '', cleaned).strip()

    header = f"<b>⚡️ АНАЛИТИЧЕСКИЙ ДАЙДЖЕСТ — {date_str}</b>"
    lead = "Краткий обзор ключевых событий и трендов за прошедшие сутки:"

    # Преобразуем Markdown-таблицы в аккуратный список фактов (ни в коем случае не удаляем в ноль!)
    cleaned_no_tables = convert_markdown_tables_to_text(cleaned)

    # Поиск ссылок вида [1] https://...
    footnote_links = {}
    for m in re.finditer(r'\[(\d+)\]\s*(https?://[^\s\)]+)', cleaned_no_tables):
        footnote_links[int(m.group(1))] = m.group(2)

    # Точный сплиттер секций новостей: ### 1., ## 1., **1., 1. или ①
    section_split_pattern = r'(?m)^(?:#{1,4}\s*|\*{1,2}\s*\d{1,2}[\.\)]\s*|\d{1,2}[\.\)]\s*|[①-⑳]\s*)([^\n]{3,120})$'
    parts = re.split(section_split_pattern, cleaned_no_tables)

    items = []
    seen_bodies = set()
    if len(parts) >= 3:
        num = 0
        for i in range(1, len(parts), 2):
            sec_title = parts[i].strip()
            sec_body = parts[i+1].strip() if i+1 < len(parts) else ""
            sec_lower = sec_title.lower()

            # Пропускаем служебные заголовки
            if len(sec_title) < 3 or any(w in sec_lower for w in [
                "тема", "№", "категория", "сравнительн", "архитектурн", "таблиц", "вывод", "аналитическ", "содержание"
            ]):
                continue

            # Дедупликация: не допускаем повторного вывода одной и той же новости/цитаты в разных разделах
            norm_content = re.sub(r'[^а-яa-z0-9]+', '', sec_body.lower())
            if norm_content and len(norm_content) > 20:
                if norm_content in seen_bodies:
                    continue
                seen_bodies.add(norm_content)

            c_num = CIRCLED_NUMS[num] if num < len(CIRCLED_NUMS) else f"[{num+1}]"
            num += 1

            clean_title = re.sub(r'^\s*[\*#]+\s*', '', sec_title)
            clean_title = re.sub(r'\s*[\*#]+\s*$', '', clean_title)
            clean_title = re.sub(r'^\s*\d+[\.\)]\s*', '', clean_title).strip()

            # Форматируем текст секции через надежный markdown_to_telegram_html
            body_html = markdown_to_telegram_html(sec_body)
            items.append(f"<b>{c_num} {clean_title}</b>\n{body_html}")

    hashtags = "#Аналитика #Геополитика #СВО #Инфобез #ПолезныйСофт #Технологии"

    if items:
        post_parts = [header, lead] + items
        res = "\n\n".join(post_parts)
    else:
        # Если секции не разбились — преобразуем весь очищенный от таблиц текст без обрезки!
        full_html = markdown_to_telegram_html(cleaned_no_tables)
        if len(full_html.strip()) < 50:
            full_html = markdown_to_telegram_html(raw_text)
        res = f"{header}\n\n{lead}\n\n{full_html}"

    res = res.strip() + f"\n\n{hashtags}"

    # Расширенный объем дайджеста (до 2 полноценных сообщений Telegram ~7800 символов):
    # При отправке send_safe_reply и cron_scheduler автоматически разделят текст на 2 части по 3800 символов.
    # Обрезаем только если текст превышает 7800 символов:
    if len(res) > 7800:
        cutoff = res[:7750].rfind("\n\n")
        if cutoff > 1200:
            res = (
                res[:cutoff]
                + "\n\n<i>📑 Полная расширенная сводка доступна в прикрепленном .MD документе.</i>\n\n"
                + hashtags
            )

    return res

AI_CURATOR_SYSTEM_PROMPT = """Ты — опытный шеф-редактор и контент-куратор персонального технологического и аналитического дайджеста «Агент Сноуден» (2026 год).
Твоя задача — отобрать от 20 до 35 САМЫХ ЦЕННЫХ И ПОЛЕЗНЫХ публикаций из предоставленного пула кандидатов с акцентом на технологии, ИИ и полезный софт.

СТРОГИЕ ПРИОРИТЕТЫ ОТБОРА:
1. НОВОСТИ ИИ, НЕЙРОСЕТИ & MACHINE LEARNING (ВЫСШИЙ ПРИОРИТЕТ):
   - Новые модели (LLM, Qwen, DeepSeek, Llama, Gemini, Claude), открытые веса, инструменты инференса, локальный запуск, архитектурные прорывы и бенчмарки (@ai_newz, @ai_machinelearning_big_data, @habr_com).
2. ИНТЕРЕСНЫЕ GITHUB-РЕПОЗИТОРИИ, OPEN-SOURCE & ПОЛЕЗНЫЙ СОФТ (ВЫСШИЙ ПРИОРИТЕТ):
   - Релизы инструментов с открытым исходным кодом, трендовые репозитории GitHub, бесплатные аналоги платных программ (Adobe Photoshop, MS Office, Figma — например PhotoCraft, WordCraft, утилиты для разработчиков, DevOps, самохостинга, ОС из @github_radar, @it_shelter, @bugfeature, @prepodsteam).
   - Это ключевая прикладная ценность для пользователя!
3. ТЕХНОЛОГИИ, IT & HARDWARE:
   - Прорывы в GPU (Nvidia RTX 50-й серии, серверные ускорители), чипы, архитектура процессоров, IT-инфраструктура (@3dnews, @rozetked, @Dev_Bubble).
4. КИБЕРБЕЗОПАСНОСТЬ & ИНФОБЕЗ:
   - Критические уязвимости (0-day, CVE), взломы, утечки данных, методы защиты (@xakep_ru, @cyberpolice_rus).
5. ВОЕННАЯ ХРОНИКА, СВО & МИРОВАЯ ГЕОПОЛИТИКА:
   - Фронтовая хроника, продвижение, объективный контроль, заявления мировых лидеров (@militarysummary, @Voennaja_svodka, @ross_name, @Dyadyabatya0, @rybar, РИА, ТАСС).

ЧТО КАТЕГОРИЧЕСКИ ОТСЕИВАТЬ (ИНФОШУМ И МУСОР):
- Чистая реклама онлайн-казино, ставок, крипто-скама, платных сомнительных курсов без полезного инструмента.
- Пустые кликбейты («Вы будете в шоке, узнав...») без фактов.
- Розыгрыши призов, мемы без смысловой нагрузки.
- Дубликаты: если несколько каналов написали об одном и том же событии, выбери 1 лучший первоисточник.

ФОРМАТ ОТВЕТА:
Верни СТРОГО валидный JSON-массив с номерами отобранных публикаций, например:
[1, 3, 5, 8, 12, 14, 17, 19, 22, 25, 28, 30]
КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать любой вводный или завершающий текст вне JSON-массива.
"""

def build_curator_user_prompt(
    candidate_items: List[dict],
    user_interests: Optional[list] = None,
    learned_preferences: Optional[str] = None
) -> str:
    lines = []
    for idx, it in enumerate(candidate_items, 1):
        ch = it.get("channel", "Канал")
        cat = it.get("category", "Общее")
        title = it.get("title", "Публикация")
        content = it.get("content", "").replace("\n", " ").strip()
        snip = content[:220] + ("..." if len(content) > 220 else "")
        url = it.get("url", "")
        lines.append(f"[{idx}] {ch} ({cat}) — {title}\nСуть: {snip}\nСсылка: {url}")

    items_block = "\n---\n".join(lines)
    pref_block = f"\nПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ:\n{learned_preferences}\n" if learned_preferences else ""

    return f"""Оцени следующий пул кандидатов и отбери от 20 до 35 самых ценных публикаций.
Сделай максимальный упор на технологии, интересные репозитории GitHub, новости ИИ (LLM, модели), полезный Open-Source софт, а также ключевую геополитику/СВО и кибербезопасность.
{pref_block}
КАНДИДАТЫ:
{items_block}

Верни JSON-массив номеров выбранных статей:
[1, 2, ...]"""

def parse_curator_selection(response_text: str, total_items: int) -> List[int]:
    """
    Надежно парсит ответ ИИ-куратора в список номеров отобранных публикаций (1-based).
    Поддерживает чистый JSON, markdown-кодблоки, массивы словарей [{'id': 1}, ...] и текстовые списки.
    """
    import json
    if not response_text:
        return []

    # 1. Попытка извлечь JSON массив между [...]
    m = re.search(r'\[(.*?)\]', response_text, flags=re.DOTALL)
    if m:
        try:
            parsed = json.loads("[" + m.group(1).strip() + "]")
            if isinstance(parsed, list):
                result = []
                for elem in parsed:
                    if isinstance(elem, int):
                        result.append(elem)
                    elif isinstance(elem, dict) and "id" in elem and isinstance(elem["id"], int):
                        result.append(elem["id"])
                    elif isinstance(elem, str) and elem.strip().isdigit():
                        result.append(int(elem.strip()))
                valid_ids = [i for i in result if 1 <= i <= total_items]
                if valid_ids:
                    return list(dict.fromkeys(valid_ids))
        except Exception:
            pass

    # 2. Фолбэк: поиск всех чисел регулярным выражением
    raw_nums = re.findall(r'\b\d+\b', response_text)
    candidate_ids = []
    for n in raw_nums:
        val = int(n)
        if 1 <= val <= total_items and val not in candidate_ids:
            candidate_ids.append(val)
    return candidate_ids

DIGEST_SYSTEM_PROMPT = """Ты — персональный автономный шеф-редактор и аналитик ключевых событий, технологий, ИИ, открытого софта, военной хроники и геополитики «Агент Сноуден» (2026 год).
Твоя задача — формировать глубокий, насыщенный фактами и деталями аналитический дайджест в формате чистого Markdown.

РАСШИРЕННЫЙ ФОРМАТ (ДО 2 СООБЩЕНИЙ TELEGRAM, ~6000–7500 СИМВОЛОВ):
Дайджест транслируется пользователю в расширенном двухсообщенном формате. Не пытайся сжать новости в 1-2 строчки! Раскрывай каждую ключевую тему полно, содержательно и с конкретными деталями.

ПРИОРИТЕТНЫЕ ТЕМАТИЧЕСКИЕ НАПРАВЛЕНИЯ:
1. НОВОСТИ ИИ & НЕЙРОСЕТИ (ВЫСШИЙ ПРИОРИТЕТ):
   - Прорывы в LLM, открытые веса, новые модели (Qwen, DeepSeek, Llama, Gemini, Claude), квантование, локальный инференс, инструменты для разработчиков ИИ.
2. ИНТЕРЕСНЫЕ GITHUB-РЕПОЗИТОРИИ & ПОЛЕЗНЫЙ OPEN-SOURCE СОФТ (ВЫСШИЙ ПРИОРИТЕТ):
   - Трендовые и полезные репозитории на GitHub, открытые библиотеки, утилиты для разработчиков, DevOps, самохостинга, ОС, бесплатные аналоги тяжелого софта (Adobe, Office, Figma).
   - Обязательно укажи: что делает проект, какие задачи решает, ключевые фичи, стек технологий и прямую ссылку на репозиторий/источник.
3. ТЕХНОЛОГИИ, IT & HARDWARE:
   - Новинки GPU, процессоры, чипы, архитектурные прорывы, ключевые события IT-индустрии (@3dnews, @habr_com, @rozetked).
4. КИБЕРБЕЗОПАСНОСТЬ & ИНФОБЕЗ:
   - 0-day уязвимости, CVE, эксплойты, утечки, рекомендации по защите и расследования (@xakep_ru, @cyberpolice_rus).
5. ВОЕННАЯ ХРОНИКА, СВО & МИРОВАЯ ГЕОПОЛИТИКА:
   - Сводка с линии соприкосновения, хроника ударов, объективный контроль, тактическая обстановка, важные заявления лидеров (@militarysummary, @Voennaja_svodka, @ross_name, @Dyadyabatya0, @rybar, РИА, ТАСС).

СТРУКТУРА ОТВЕТА (MARKDOWN):
# ⚡️ АНАЛИТИЧЕСКИЙ ДАЙДЖЕСТ — [Дата]

[Вводный аналитический бриф: 2-3 плотных предложения о главных технологических релизах, ИИ-прорывах, обстановке на фронте и мировой дипломатии суток]

### 1. [Категория]: [Емкий и точный заголовок инфоповода / репозитория / модели]
- **Суть и факты:** [Развернутый синтез всех подробностей из всех источников в один плотный связный абзац без повторов. Для репозитория/софта — стек, задачи, возможности. Для ИИ — модель, параметры, возможности]
> «[Ключевая цитата разработчика, спикера, военкора или автора канала]» — [Источник]
- **Значимость и польза:** [Почему это важно, как применить на практике или как влияет на картину в мире]
- **Ссылка:** [Название источника / GitHub](url)

### 2. [Категория]: [Следующая тема / репозиторий / новость]
...

## 💡 Главные выводы и технологические тренды суток
[Синтез картины дня: ключевые тренды в ИИ и софте, выводы по безопасности и геополитике]

ЖЕСТКИЕ ПРАВИЛА:
1. МНОГОИСТОЧНИКОВЫЙ КРОСС-СИНТЕЗ (ДЕДУПЛИКАЦИЯ):
   Если о событии сообщили несколько каналов — ОБЪЕДИНИ их в ОДНУ подробную новость! Не делай дублирующих записей.
2. ВЫДЕЛЕНИЕ ЦИТАТ В BLOCKQUOTE:
   Комментарии разработчиков, спикеров или очевидцев оформляй через цитату с указанием конкретного имени или реального канала: `> «Цитата...» — @реальный_источник`. Категорически запрещено писать заглушки '@канал' или 'ссылка'.
3. МАКСИМАЛЬНЫЙ УПОР НА ТЕХНОЛОГИИ, ИИ И РЕПОЗИТОРИИ:
   Обязательно отбирай и подробно освещай все интересные проекты GitHub, утилиты и модели ИИ из предоставленной выборки.
4. ФОРМАТИРОВАНИЕ ДОКУМЕНТА (БЕЗ ЧЕРНОГО ФОНА И БЕЗ СЛОМАННЫХ ТАБЛИЦ):
   - КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО оборачивать весь документ или текст в тройные обратные кавычки (```markdown ... ```)!
   - Запрещены отступы в 4 пробела перед строками (в Markdown это превращает текст в программный код).
   - Пиши чистый текст Markdown с заголовками (###) и списками (-).
5. Исключай рекламу, партнерские промокоды, кликбейт, розыгрыши и крипто-скамы.
6. Сохраняй реальные ссылки на первоисточники и GitHub из предоставленных данных.
"""

def build_digest_user_prompt(
    raw_items_text: str,
    user_interests: list,
    blacklist: list,
    learned_preferences: Optional[str] = None
) -> str:
    interests_str = ", ".join([item.get("name", "") for item in user_interests if isinstance(item, dict)])
    blacklist_str = ", ".join(blacklist) if blacklist else "нет"

    pref_block = ""
    if learned_preferences:
        pref_block = f"\nИСТОРИЯ РЕАКЦИЙ ПОЛЬЗОВАТЕЛЯ:\n{learned_preferences}\n"

    return f"""Сформируй расширенный аналитический дайджест (объем до 2 сообщений Telegram, ~6000–7500 символов) на основе следующих данных:

ПРИОРИТЕТЫ И ИНТЕРЕСЫ (МАКСИМАЛЬНЫЙ УПОР НА ТЕХНОЛОГИИ, ИИ, GITHUB И СОФТ):
{interests_str}

СТОП-СЛОВА / ИСКЛЮЧЕНИЯ:
{blacklist_str}
{pref_block}
СЫРЫЕ ПУБЛИКАЦИИ:
{raw_items_text}

Сформируй структурированный расширенный дайджест с подробным разбором интересных репозиториев GitHub, новостей ИИ, полезного софта, кибербезопасности, а также СВО и геополитики. Цитаты оформляй через > «...», каждую тему раскрывай детально.
"""
