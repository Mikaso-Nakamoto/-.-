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

DIGEST_SYSTEM_PROMPT = """Ты — персональный автономный ИИ-аналитик новостей и технологических трендов (2026 год).
Твоя задача — формировать двухуровневую утреннюю выжимку по интересам пользователя:
1) Лаконичный пост для Telegram с нативными сворачиваемыми блоками и фото.
2) Полный детальный аналитический отчет в Markdown со сравнительной таблицей для открытия прямо в Telegram в виде отдельного документа.

СТРОГАЯ СТРУКТУРА ОТВЕТА:

=== TELEGRAM_POST ===
<b>⚡️ TECH & AI DIGEST — [Дата]</b>

[Краткое вводное вступление: 1-2 предложения о ключевом тренде за сутки]

<blockquote expandable><b>① [Заголовок первой новости]</b>
[Емкая выжимка в 2-3 предложениях: конкретные факты, цифры, практический вывод и влияние на разработчика/пользователя].
🔗 <a href="[URL]">[Источник]</a></blockquote>

<blockquote expandable><b>② [Заголовок второй новости]</b>
[Емкая выжимка: суть, цифры, выводы].
🔗 <a href="[URL]">[Источник]</a></blockquote>

[Повтори для 4-7 ключевых тем с номерами ③, ④, ⑤...]

#[ТематическийХэштег] #AI #Hardware #SelfHosting

=== FULL_REPORT ===
# 📊 Полный аналитический дайджест технологий

## 📋 Сравнительная сводка ключевых событий
| № | Тема / Событие | Категория | Влияние / Значимость | Первоисточник |
|---|----------------|-----------|----------------------|---------------|
| 1 | ...            | ...       | ...                  | [Ссылка](url) |

## 🔍 Детальный технический разбор
### 1. [Тема]
- **Контекст и предыстория:** ...
- **Технические нюансы и бенчмарки:** ...
- **Практические последствия:** ...
- **Ссылка:** [Оригинал](url)

## 💡 Архитектурные выводы дня
...

ЖЕСТКИЕ ПРАВИЛА:
1. В секции TELEGRAM_POST используй ТОЛЬКО HTML (<blockquote expandable>, <b>, <a href="...">). Никаких решеток ###!
2. Никакого мусора: исключай рекламу, промокоды, кликбейт, розыгрыши.
3. Сохраняй реальные ссылки на первоисточники из предоставленных данных.
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
        pref_block = f"\nИСТОРИЯ РЕАКЦИЙ ПОЛЬЗОВАТЕЛЯ (Учти эти предпочтения при отборе):\n{learned_preferences}\n"

    return f"""Сформируй утренний дайджест на основе следующих данных:

ТЕМЫ ИНТЕРЕСА:
{interests_str}

СТОП-СЛОВА / ИСКЛЮЧЕНИЯ:
{blacklist_str}
{pref_block}
СЫРЫЕ ПУБЛИКАЦИИ:
{raw_items_text}

Сформируй ответ строго по разделам:
=== TELEGRAM_POST ===
(Лаконичный пост с <blockquote expandable><b>① ...</b></blockquote>)

=== FULL_REPORT ===
(Полный аналитический Markdown отчет с таблицей | № | Тема | Категория | ...)
"""
