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

    if web_app_url:
        rows.append([InlineKeyboardButton(text="📱 Открыть окно внутри TG", web_app=WebAppInfo(url=web_app_url))])

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
    if web_app_url:
        action_row.append(InlineKeyboardButton(text="📱 Открыть окно в TG", web_app=WebAppInfo(url=web_app_url)))
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
