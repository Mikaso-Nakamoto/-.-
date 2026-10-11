import re
import html
import logging
from typing import Dict, Any, List, Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger("military_summary")

MILITARY_SUMMARY_MAP_URL = "https://militarysummary.com/"
MILITARY_SUMMARY_CHANNEL = "https://t.me/militarysummaryru"

FRONTLINE_SECTORS = [
    {
        "name": "Покровско-Кураховское направление",
        "keywords": ["покровск", "красноармейск", "курахов", "селидов", "мирноград", "горняк", "цукурин"]
    },
    {
        "name": "Часов Яр & Торецк",
        "keywords": ["часов яр", "торецк", "дзержинск", "константиновк", "клещеевк"]
    },
    {
        "name": "Купянско-Лиманский сектор",
        "keywords": ["купянск", "оскол", "песчан", "кругляковк", "лиман", "кременн"]
    },
    {
        "name": "Южно-Донецкий сектор & Угледар",
        "keywords": ["угледар", "великая новоселк", "шахтерск", "богоявленк", "яснополян"]
    },
    {
        "name": "Курское приграничье",
        "keywords": ["курск", "судж", "коренев", "глушков", "любимовк", "зеленый шлях", "плехов"]
    },
    {
        "name": "Запорожский & Херсонский фронт",
        "keywords": ["запорож", "орехов", "работин", "гуляйпол", "херсон", "днепр", "крынк", "остров"]
    },
    {
        "name": "Ракетные удары & Тыловые объекты",
        "keywords": ["киев", "днепр", "харьков", "одесс", "полтав", "прилет", "циркон", "искандер", "герань"]
    }
]

def get_military_summary_keyboard() -> InlineKeyboardMarkup:
    """Формирует клавиатуру быстрого перехода к интерактивной карте и сводкам"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗺 Открыть интерактивную карту (militarysummary.com)", url=MILITARY_SUMMARY_MAP_URL)],
        [
            InlineKeyboardButton(text="⚔️ Сводка по фронту", callback_data="btn_ms_frontline_brief"),
            InlineKeyboardButton(text="🔄 Обновить ленту", callback_data="btn_ms_refresh")
        ],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
    ])

class MilitarySummaryEngine:
    def __init__(self):
        self.map_url = MILITARY_SUMMARY_MAP_URL
        self.channel_url = MILITARY_SUMMARY_CHANNEL

    async def get_frontline_overview(self, storage, llm_router=None) -> Dict[str, Any]:
        """
        Анализирует публикации из подключенных военных каналов пользователя
        (@militarysummaryru, @mod_russia, @Voennaja_svodka, @ross_name, @infomil_live, @rybar)
        и формирует оперативную карточку фронта со ссылкой на интерактивную карту.
        """
        recent_feed = storage.get_news_feed(limit=250)

        sector_matches: Dict[str, List[Dict[str, Any]]] = {s["name"]: [] for s in FRONTLINE_SECTORS}

        for item in recent_feed:
            text = f"{item.get('title', '')} {item.get('content', '')}".lower()
            for s in FRONTLINE_SECTORS:
                if any(kw in text for kw in s["keywords"]):
                    if len(sector_matches[s["name"]]) < 2:
                        sector_matches[s["name"]].append(item)

        lines = [
            "🗺 <b>ИНТЕРАКТИВНАЯ КАРТА СВО — MILITARY SUMMARY</b>\n",
            f"🌐 <b>Гео-портал карты:</b> <a href=\"{self.map_url}\">militarysummary.com</a>",
            f"📡 <b>Канал аналитики:</b> <a href=\"{self.channel_url}\">@militarysummaryru</a>\n",
            "⚔️ <b>Оперативная обстановка по секторам ЛБС (по добавленным источникам):</b>\n"
        ]

        active_sectors_found = 0
        for s in FRONTLINE_SECTORS:
            name = s["name"]
            items = sector_matches.get(name, [])
            if not items:
                continue

            active_sectors_found += 1
            lines.append(f"📍 <b>{name}:</b>")
            for it in items:
                title = it.get("title") or "Сводка с направления"
                ch = it.get("channel") or "Военкор"
                pub = (it.get("published_at") or "")[:16]
                url = it.get("url") or ""
                snippet = (it.get("content") or "").strip()[:180]
                if snippet:
                    snippet = re.sub(r'\s+', ' ', snippet)
                link_html = f"<a href=\"{url}\"><b>[{ch}]</b></a>" if url else f"<b>[{ch}]</b>"
                time_str = f" <i>({pub})</i>" if pub else ""
                lines.append(f"• {link_html}{time_str}: {html.escape(snippet)}...")
            lines.append("")

        if active_sectors_found == 0:
            lines.append("<i>В недавних публикациях подключенных каналов прямых упоминаний секторов за последние часы не обнаружено. Нажмите кнопку обновления или откройте интерактивную карту.</i>\n")

        lines.append("💡 <i>Интерактивная карта обновляется ежедневно: изменения контроля территорий, фортификации и линии продвижения доступны по кнопке ниже.</i>")

        return {
            "text": "\n".join(lines),
            "keyboard": get_military_summary_keyboard(),
            "map_url": self.map_url
        }
