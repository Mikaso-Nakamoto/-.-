from typing import Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from src.llm.router import POPULAR_MODELS

def get_main_menu_keyboard(chat_mode: bool = False, web_app_url: Optional[str] = None) -> InlineKeyboardMarkup:
    first_row = [
        InlineKeyboardButton(text="⚡️ Сводка", callback_data="btn_run_digest"),
        InlineKeyboardButton(text="🗺 Карта СВО", callback_data="btn_military_summary")
    ]

    second_row = [
        InlineKeyboardButton(text="🔄 Обновить ленту", callback_data="btn_refresh_now")
    ]

    rows = [first_row, second_row]

    if web_app_url and str(web_app_url).strip():
        clean_url = str(web_app_url).strip()
        if clean_url.lower().startswith("https://"):
            rows.append([InlineKeyboardButton(text="📱 Открыть терминал (Web)", web_app=WebAppInfo(url=clean_url))])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_chat_control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🧹 Очистить память диалога", callback_data="btn_chat_clear"),
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")
        ]
    ])

def get_search_settings_keyboard(is_google_ai: bool = True) -> InlineKeyboardMarkup:
    google_label = "🌐 Google AI Search: ВКЛ" if is_google_ai else "🌐 Google AI Search: ВЫКЛ"
    google_cb = "btn_google_off" if is_google_ai else "btn_google_on"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=google_label, callback_data=google_cb)],
        [InlineKeyboardButton(text="🗺 Карта СВО", callback_data="btn_military_summary")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
    ])

def get_search_result_keyboard(is_google_ai_enabled: bool = False) -> InlineKeyboardMarkup:
    google_label = "🌐 Google AI: ВКЛ" if is_google_ai_enabled else "🌐 Google AI: ВЫКЛ"
    google_cb = "btn_google_off" if is_google_ai_enabled else "btn_google_on"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=google_label, callback_data=google_cb)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="btn_menu")]
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
        ("🔄 Auto (Local -> Резерв в облако)", "prov_auto"),
        ("🖥 Локальный ПК (LM Studio Qwen 2.5)", "prov_local"),
        ("🌐 Google Gemini (Flash / Pro)", "prov_gemini"),
        ("⚡️ GroqCloud (Llama 3.3 70B)", "prov_groq"),
        ("🔀 OpenRouter (Бесплатный пул)", "prov_openrouter")
    ]

    buttons = []
    for label, callback_data in providers:
        prov_key = callback_data.replace("prov_", "")
        check = " ✅" if prov_key == current_provider else ""
        buttons.append([InlineKeyboardButton(text=f"{label}{check}", callback_data=callback_data)])

    # Интегрированные каталоги моделей
    buttons.append([
        InlineKeyboardButton(text="🎯 Модели Gemini", callback_data="viewmods_gemini"),
        InlineKeyboardButton(text="🎯 Модели Groq", callback_data="viewmods_groq")
    ])
    buttons.append([
        InlineKeyboardButton(text="🎯 Бесплатные OpenRouter", callback_data="viewmods_openrouter")
    ])
    buttons.append([InlineKeyboardButton(text="🔙 Главный терминал", callback_data="btn_menu")])
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
        ("📦 OpenSource & Софт", "soft"),
        ("⚔️ СВО & Геополитика", "svo"),
        ("🛡 Кибербезопасность", "sec"),
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
