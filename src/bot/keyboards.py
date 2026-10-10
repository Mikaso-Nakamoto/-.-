from typing import Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from src.llm.router import POPULAR_MODELS

def get_main_menu_keyboard(chat_mode: bool = False, web_app_url: Optional[str] = None) -> InlineKeyboardMarkup:
    chat_btn = (
        InlineKeyboardButton(text="🔴 Завершить режим чата", callback_data="btn_chat_stop")
        if chat_mode
        else InlineKeyboardButton(text="💬 Режим диалога (Чат)", callback_data="btn_chat_start")
    )

    first_row = [
        InlineKeyboardButton(text="📰 Дайджест сейчас", callback_data="btn_run_digest"),
        InlineKeyboardButton(text="⚡️ Статус нод", callback_data="btn_status")
    ]

    second_row = [
        InlineKeyboardButton(text="🧠 Выбор нейросети", callback_data="btn_select_provider"),
        InlineKeyboardButton(text="🎯 Модели", callback_data="btn_browse_models")
    ]

    third_row = [
        chat_btn,
        InlineKeyboardButton(text="📡 Источники", callback_data="btn_sources")
    ]

    rows = [first_row, second_row, third_row]

    if web_app_url and str(web_app_url).strip():
        clean_url = str(web_app_url).strip()
        if clean_url.lower().startswith("https://"):
            rows.append([InlineKeyboardButton(text="📱 Открыть окно в TG", web_app=WebAppInfo(url=clean_url))])
        elif clean_url.lower().startswith("http://"):
            rows.append([InlineKeyboardButton(text="🌐 Веб-панель (LAN)", url=clean_url)])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_chat_control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔴 Завершить диалог", callback_data="btn_chat_stop"),
            InlineKeyboardButton(text="🧹 Очистить память диалога", callback_data="btn_chat_clear")
        ],
        [
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")
        ]
    ])

def get_digest_feedback_keyboard(web_app_url: Optional[str] = None, md_file_id: Optional[str] = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="👍 Отличная подборка", callback_data="fb_like"),
            InlineKeyboardButton(text="👎 Слишком много мусора", callback_data="fb_dislike")
        ]
    ]

    action_row = [
        InlineKeyboardButton(text="💬 Обсудить с ИИ", callback_data="btn_chat_start")
    ]
    if web_app_url and str(web_app_url).strip():
        clean_url = str(web_app_url).strip()
        if clean_url.lower().startswith("https://"):
            action_row.append(InlineKeyboardButton(text="📱 Открыть окно в TG", web_app=WebAppInfo(url=clean_url)))
        elif clean_url.lower().startswith("http://"):
            action_row.append(InlineKeyboardButton(text="🌐 Веб-панель", url=clean_url))
    rows.append(action_row)

    if md_file_id:
        rows.append([
            InlineKeyboardButton(text="📄 Скачать исходный .MD", callback_data=f"getmd__{md_file_id}")
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_provider_selection_keyboard(current_provider: str) -> InlineKeyboardMarkup:
    providers = [
        ("🔄 Auto (Local -> Cloud Failover)", "prov_auto"),
        ("🖥 Локальный ПК (LM Studio)", "prov_local"),
        ("⚡️ GroqCloud (Ultra-Fast LPU)", "prov_groq"),
        ("🌐 Google Gemini (Flash / Pro)", "prov_gemini"),
        ("🔀 OpenRouter (Free Pool)", "prov_openrouter")
    ]

    buttons = []
    for label, callback_data in providers:
        prov_key = callback_data.replace("prov_", "")
        check = " ✅" if prov_key == current_provider else ""
        buttons.append([InlineKeyboardButton(text=f"{label}{check}", callback_data=callback_data)])

    buttons.append([InlineKeyboardButton(text="🔙 Главное меню", callback_data="btn_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_models_keyboard(provider: str, current_model: str) -> InlineKeyboardMarkup:
    buttons = []
    models_list = POPULAR_MODELS.get(provider, [])

    for label, model_id in models_list:
        check = " ✅" if model_id == current_model else ""
        cb = f"setm_{provider}__" + model_id.replace("/", "--").replace(":", "==")
        buttons.append([InlineKeyboardButton(text=f"{label}{check}", callback_data=cb)])

    buttons.append([
        InlineKeyboardButton(text="⬅️ К провайдерам", callback_data="btn_select_provider"),
        InlineKeyboardButton(text="🏠 Меню", callback_data="btn_menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_provider_browser_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔀 Бесплатные модели OpenRouter", callback_data="viewmods_openrouter")],
        [InlineKeyboardButton(text="⚡️ Модели GroqCloud", callback_data="viewmods_groq")],
        [InlineKeyboardButton(text="🌐 Модели Google Gemini", callback_data="viewmods_gemini")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="btn_menu")]
    ])

def get_sources_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Добавить TG-канал", callback_data="btn_add_channel"),
            InlineKeyboardButton(text="🗑 Удалить канал", callback_data="btn_del_channel_menu")
        ],
        [
            InlineKeyboardButton(text="➕ Добавить RSS", callback_data="btn_add_rss"),
            InlineKeyboardButton(text="🗑 Удалить RSS", callback_data="btn_del_rss_menu")
        ],
        [
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")
        ]
    ])

def get_delete_channels_keyboard(channels: list) -> InlineKeyboardMarkup:
    buttons = []
    for ch in channels:
        user = ch.get("username", "")
        cat = ch.get("category", "")
        label = f"❌ @{user}" + (f" ({cat[:14]})" if cat else "")
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"delch__{user}")])

    buttons.append([InlineKeyboardButton(text="🔙 Назад к источникам", callback_data="btn_sources")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_delete_rss_keyboard(feeds: list) -> InlineKeyboardMarkup:
    buttons = []
    for i, feed in enumerate(feeds):
        name = feed.get("name") or feed.get("url", "")
        buttons.append([InlineKeyboardButton(text=f"❌ {name[:28]}", callback_data=f"delrss__{i}")])

    buttons.append([InlineKeyboardButton(text="🔙 Назад к источникам", callback_data="btn_sources")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_category_selection_keyboard(clean_username: str, ai_category: str = "") -> InlineKeyboardMarkup:
    buttons = []
    if ai_category and ai_category.strip() and ai_category.strip() != "Общее и Новости":
        clean_ai_label = ai_category.strip()[:28]
        buttons.append([InlineKeyboardButton(text=f"🤖 Принять: {clean_ai_label}", callback_data=f"setchcat__{clean_username}__ai_detected")])

    categories = [
        ("🧠 Нейросети и ИИ", "ai"),
        ("🛠 DevOps & Self-Host", "devops"),
        ("💻 Разработка и Кодинг", "dev"),
        ("🎮 Hardware & GPU", "gpu"),
        ("🌐 Общее и Новости", "general")
    ]
    for label, slug in categories:
        cb = f"setchcat__{clean_username}__{slug}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=cb)])

    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="btn_sources")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_cancel_keyboard(callback_data: str = "btn_sources") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=callback_data)]
    ])
