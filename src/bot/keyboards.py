from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_model_selection_keyboard(current_provider: str) -> InlineKeyboardMarkup:
    providers = [
        ("🔄 Auto (Local -> Cloud Fallback)", "prov_auto"),
        ("🖥 Локальный ПК (Qwen 2.5 7B)", "prov_local"),
        ("⚡️ GroqCloud (Llama 3.3 70B)", "prov_groq"),
        ("🌐 Google Gemini (Flash)", "prov_gemini"),
        ("🔀 OpenRouter (Free Pool)", "prov_openrouter")
    ]

    buttons = []
    for label, callback_data in providers:
        prov_key = callback_data.replace("prov_", "")
        check = " ✅" if prov_key == current_provider else ""
        buttons.append([InlineKeyboardButton(text=f"{label}{check}", callback_data=callback_data)])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📰 Запустить дайджест сейчас", callback_data="btn_run_digest"),
            InlineKeyboardButton(text="⚡️ Статус нод и GPU", callback_data="btn_status")
        ],
        [
            InlineKeyboardButton(text="🧠 Выбор нейросети", callback_data="btn_select_model"),
            InlineKeyboardButton(text="📡 Источники", callback_data="btn_sources")
        ]
    ])
