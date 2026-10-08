from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from src.llm.router import POPULAR_MODELS

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📰 Дайджест сейчас", callback_data="btn_run_digest"),
            InlineKeyboardButton(text="⚡️ Статус нод", callback_data="btn_status")
        ],
        [
            InlineKeyboardButton(text="🧠 Выбор нейросети", callback_data="btn_select_provider"),
            InlineKeyboardButton(text="🎯 Модели провайдеров", callback_data="btn_browse_models")
        ],
        [
            InlineKeyboardButton(text="📡 Источники", callback_data="btn_sources"),
            InlineKeyboardButton(text="💬 Режим чата", callback_data="btn_chat_info")
        ]
    ])

def get_provider_selection_keyboard(current_provider: str) -> InlineKeyboardMarkup:
    providers = [
        ("🔄 Auto (Local -> Cloud Fallback)", "prov_auto"),
        ("🖥 Локальный ПК (Qwen 2.5 в LM Studio)", "prov_local"),
        ("⚡️ GroqCloud (LPU Ultra-Fast)", "prov_groq"),
        ("🌐 Google Gemini (Flash / Pro)", "prov_gemini"),
        ("🔀 OpenRouter (Free Pool ~25 моделей)", "prov_openrouter")
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
        [InlineKeyboardButton(text="🔀 Модели OpenRouter (Free)", callback_data="viewmods_openrouter")],
        [InlineKeyboardButton(text="⚡️ Модели GroqCloud", callback_data="viewmods_groq")],
        [InlineKeyboardButton(text="🌐 Модели Google Gemini", callback_data="viewmods_gemini")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="btn_menu")]
    ])
