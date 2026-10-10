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

def extract_clean_digest_post(raw_text: str, date_str: str) -> str:
    """
    Формирует лаконичный, аккуратный Telegram-пост из любого вывода LLM:
    - Никаких служебных меток TELEGRAM_POST, FULL_REPORT, ===.
    - Никаких сырых Markdown-таблиц внутри сообщения (они идут в .md файл).
    - Каждая новость оформляется как <blockquote expandable><b>① Заголовок</b>\n• Тезис...\n• Важность...\n🔗 Ссылка</blockquote>.
    - Плотность текста достаточна (4-5 строк), чтобы Telegram гарантированно показал нативную стрелку сворачивания ∨.
    - Только реальные факты из публикаций, без воды.
    """
    if not raw_text:
        return f"<b>⚡️ АНАЛИТИЧЕСКИЙ ДАЙДЖЕСТ — {date_str}</b>\n\nНет данных для отображения."

    # Очистка от экранирования и артефактов
    cleaned = raw_text.replace("&lt;", "<").replace("&gt;", ">")
    cleaned = re.sub(r'={2,}\s*(?:TELEGRAM_POST|FULL_REPORT)\s*={2,}', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'(?m)^(?:TELEGRAM_POST|FULL_REPORT)\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\([Лл]аконичный пост[^\)]*\)', '', cleaned)
    cleaned = cleaned.strip()

    header = f"<b>⚡️ АНАЛИТИЧЕСКИЙ ДАЙДЖЕСТ — {date_str}</b>"

    # Поиск ссылок вида [1] https://...
    footnote_links = {}
    for m in re.finditer(r'\[(\d+)\]\s*(https?://[^\s\)]+)', cleaned):
        footnote_links[int(m.group(1))] = m.group(2)

    # Поиск строк таблицы
    table_rows = []
    for line in cleaned.split("\n"):
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 3 and cells[0].isdigit():
            num_idx = int(cells[0])
            theme = cells[1]
            body = cells[3] if len(cells) > 3 else (cells[2] if len(cells) > 2 else "")
            cat = cells[2] if len(cells) > 3 else "Общее"
            link = ""
            for c in cells:
                m_url = re.search(r'https?://[^\s\)]+', c)
                if m_url:
                    link = m_url.group(0)
                    break
            if not link and num_idx in footnote_links:
                link = footnote_links[num_idx]
            table_rows.append((theme, body, cat, link))

    items = []

    # 1. Поиск детальных секций разбора
    # Поддерживаем любые стили разметки моделей (### 1. Тема, *Разработка и GameDev*, **1. Категория**)
    section_split_pattern = r'(?m)^(?:#{1,4}\s*|\*{1,2}|\d{1,2}[\.\)]\s*)([A-Za-zА-Яа-я0-9\s&_\-—/]{3,65})(?:\*{1,2})?\s*$'
    parts = re.split(section_split_pattern, cleaned)

    if len(parts) >= 3:
        num = 0
        for i in range(1, len(parts), 2):
            sec_title = parts[i].strip()
            sec_body = parts[i+1].strip() if i+1 < len(parts) else ""
            if len(sec_title) < 3 or sec_title.lower().startswith((
                "тема", "№", "категория", "сравнительная", "архитектурные", "детальный", "список", "таблица"
            )):
                continue

            c_num = CIRCLED_NUMS[num] if num < len(CIRCLED_NUMS) else f"[{num+1}]"
            num += 1

            # Извлечение ссылки на источник
            link_html = ""
            m_l = re.search(r'\[([^\]]+)\]\((https?://[^\)]+)\)', sec_body)
            if m_l:
                link_html = f'\n🔗 <a href="{m_l.group(2)}">{html.escape(m_l.group(1))}</a>'
                sec_body = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', '', sec_body)
            elif num in footnote_links:
                link_html = f'\n🔗 <a href="{footnote_links[num]}">Первоисточник</a>'

            # Парсинг содержательных тезисов новости
            headline = ""
            lines = []
            for raw_line in sec_body.split("\n"):
                l = raw_line.strip()
                if not l or l.startswith("|") or l.startswith("#"):
                    continue
                # Если первая строчка — заголовок новости без двоеточия
                if not headline and re.match(r'^[\*\-•]\s+[^:]{5,120}$', l):
                    headline = re.sub(r'^[\*\-•]\s+', '', l).strip()
                    continue
                # Очищаем маркер списка, сохраняя форматирование жирного текста
                clean_l = re.sub(r'^\s*(?:[\*\-•]|\d+[\.\)])\s*', '', l).strip()
                clean_l = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2">\1</a>', clean_l)
                clean_l = re.sub(r'\*\*([^\*]+?)\*\*', r'<b>\1</b>', clean_l)
                if clean_l and len(clean_l) > 10 and not clean_l.startswith("http"):
                    lines.append(f"• {clean_l}")

            # Если явных маркеров не было — разбиваем сырой текст абзаца на содержательные предложения!
            if not lines:
                sentences = re.split(r'(?<=[.!?])\s+', sec_body)
                for s in sentences:
                    s_clean = s.strip()
                    s_clean = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'<a href="\2">\1</a>', s_clean)
                    s_clean = re.sub(r'\*\*([^\*]+?)\*\*', r'<b>\1</b>', s_clean)
                    if len(s_clean) > 15 and not s_clean.startswith("http") and not s_clean.startswith("#"):
                        lines.append(f"• {s_clean}")
                    if len(lines) >= 3:
                        break

            # Формируем заголовок блока
            full_title = sec_title
            if headline and headline.lower() not in sec_title.lower():
                full_title = f"{sec_title}: {headline}"
            full_title = html.unescape(full_title).replace("*", "").replace("#", "").strip()

            # Сохраняем 2-4 конкретных содержательных пункта (Тезис, Важность, Детали)
            body_bullets = "\n".join(lines[:4]) if lines else "• <b>Факты:</b> Подробности события опубликованы в источнике."

            # Формируем чистую текстовую карточку новости (без цитат и без полос)
            block = f"<b>{c_num} {full_title}</b>\n{body_bullets}{link_html}"
            items.append(block)

    # 2. Если секций нет, формируем из строк таблицы с плотной структурой
    if not items and table_rows:
        for num, (theme, body, cat, link) in enumerate(table_rows[:7]):
            c_num = CIRCLED_NUMS[num] if num < len(CIRCLED_NUMS) else f"[{num+1}]"
            link_html = f'\n🔗 <a href="{link}">Первоисточник</a>' if link else ""
            clean_theme = html.unescape(theme.replace("**", "").replace("*", "").strip())
            clean_body = html.unescape(body.replace("**", "").replace("*", "").strip())
            clean_cat = html.unescape(cat.replace("**", "").replace("*", "").strip())

            items.append(
                f"<b>{c_num} {clean_cat}: {clean_theme}</b>\n"
                f"• <b>Факты:</b> {clean_body}\n"
                f"• <b>Значимость:</b> Важное событие в категории «{clean_cat}».{link_html}"
            )

    # 3. Если в тексте присутствовали теги blockquote — полностью удаляем их
    if not items and "<blockquote" in cleaned:
        for bq in re.findall(r'<blockquote[^>]*>(.*?)</blockquote>', cleaned, flags=re.DOTALL):
            clean_bq = re.sub(r'</?blockquote[^>]*>', '', bq).strip()
            if clean_bq:
                items.append(clean_bq)

    lead = "Краткий обзор ключевых событий и трендов за прошедшие сутки:"

    post_parts = [header, lead]
    if items:
        post_parts.extend(items)
    else:
        post_parts.append(html.escape(cleaned[:300]))

    post_parts.append("#Аналитика #Геополитика #СВО #Инфобез #Технологии")
    return "\n\n".join(post_parts)

DIGEST_SYSTEM_PROMPT = """Ты — персональный автономный аналитик ключевых событий, мировой геополитики, безопасности и технологий (2026 год).
Твоя задача — формировать глубокий, взвешенный и структурированный аналитический дайджест в формате чистого Markdown.

ТЕМАТИЧЕСКИЕ НАПРАВЛЕНИЯ:
- Геополитика & Международные отношения (переговоры, заявления мировых лидеров, США, РФ, Китай, ЕС, Ближний Восток)
- Ситуация на Украине & Военно-политическая хроника (СВО, фронт, дипломатия, вооружения)
- Кибербезопасность & Инфобез (уязвимости, атаки, эксплоиты, OSINT)
- Технологии, Hardware & ИИ (чипы, модели, разработка, софт)
- Российские регионы и экономика

СТРУКТУРА ОТВЕТА (MARKDOWN):
# ⚡️ АНАЛИТИЧЕСКИЙ ДАЙДЖЕСТ — [Дата]

[Краткое вводное резюме дня: 1-2 предложения о ключевой картине суток: геополитический сдвиг, безопасность, технологии]

## 📋 Сравнительная таблица событий
| № | Тема / Событие | Категория | Влияние / Значимость | Первоисточник |
|---|----------------|-----------|----------------------|---------------|
| 1 | ...            | ...       | ...                  | [Источник](url)|

## 🔍 Детальный разбор ключевых событий
### 1. [Категория]: [Название темы]
- **Что произошло:** [Конкретные факты, заявления лидеров, фронтовая обстановка, релизы, события строго из текста]
- **Суть и подробности:** [Фактура, цифры, стороны конфликта, цитаты, технические или политические нюансы]
- **Значимость и влияние:** [К чему это ведет, геополитические/отраслевые последствия, влияние на ситуацию]
- **Ссылка:** [Источник](url)

### 2. [Категория]: [Название темы]
...

## 💡 Главные выводы и тренды суток
[Синтез картины дня: геополитический сдвиг, киберугрозы, технологические тренды]

ЖЕСТКИЕ ПРАВИЛА:
1. ИСПОЛЬЗУЙ ТОЛЬКО РЕАЛЬНЫЕ ФАКТЫ ИЗ ТЕКСТА: категорически запрещена вода, общие фразы или галлюцинации.
2. ФОРМАТИРОВАНИЕ ДОКУМЕНТА (БЕЗ ЧЕРНОГО ФОНА):
   - КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО оборачивать весь документ или текст в тройные обратные кавычки (```markdown ... ```)!
   - Запрещены отступы в 4 пробела перед строками (в Markdown это превращает текст в программный код).
   - Пиши чистый текст: заголовки (#, ##, ###), таблицы (|) и списки (-).
3. Исключай рекламу, партнерские промокоды, кликбейт, розыгрыши и крипто-скамы.
4. Сохраняй реальные ссылки на первоисточники из предоставленных данных.
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

    return f"""Сформируй утренний аналитический дайджест на основе следующих данных:

ТЕМЫ ИНТЕРЕСА:
{interests_str}

СТОП-СЛОВА / ИСКЛЮЧЕНИЯ:
{blacklist_str}
{pref_block}
СЫРЫЕ ПУБЛИКАЦИИ:
{raw_items_text}

Сформируй Markdown-отчет со сравнительной таблицей и детальным разбором каждого события.
"""
