import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from src.llm.router import LLMRouter
from src.pipeline.digest_builder import DigestBuilder
from src.pipeline.storage import Storage
from src.bot.keyboards import (
    get_main_menu_keyboard,
    get_provider_selection_keyboard,
    get_models_keyboard,
    get_provider_browser_keyboard
)
from src.config import load_preferences, load_sources

logger = logging.getLogger(__name__)

def setup_router(digest_builder: DigestBuilder, llm_router: LLMRouter, storage: Storage, admin_id: int) -> Router:
    r = Router()

    def is_admin(user_id: int) -> bool:
        return admin_id == 0 or user_id == admin_id

    # --------------------------------------------------------------------------
    # Главное меню и базовые команды
    # --------------------------------------------------------------------------
    @r.message(Command("start"))
    @r.callback_query(F.data == "btn_menu")
    async def cmd_start(event: Message | CallbackQuery):
        msg = event if isinstance(event, Message) else event.message
        if isinstance(event, CallbackQuery):
            await event.answer()

        if not is_admin(event.from_user.id):
            await msg.answer("⛔️ Доступ ограничен.")
            return

        is_local_on = await llm_router.check_local_health()
        local_status = "🟢 В сети (LM Studio)" if is_local_on else "🔴 Офлайн (Включен облачный резерв)"

        active_prov = llm_router.active_provider
        current_model = llm_router.get_current_model_for_provider(active_prov if active_prov != "auto" else "openrouter")

        text = (
            f"👋 *Привет! Я твой персональный автономный ИИ-хаб.*\n\n"
            f"💻 *Ноутбук-сервер:* 🟢 24/7 Активен\n"
            f"🖥 *Основной ПК (LM Studio):* {local_status}\n"
            f"🧠 *Активный провайдер:* `{active_prov.upper()}`\n"
            f"🎯 *Текущая модель:* `{current_model}`\n"
            f"⏰ *Расписание дайджеста:* `Каждый день в 08:50`\n\n"
            f"💡 *Интерактивный режим:* Вы можете просто написать мне любой вопрос или задачу в этот чат, и я отвечу через текущую нейросеть!"
        )
        await msg.answer(text, parse_mode="Markdown", reply_markup=get_main_menu_keyboard())

    @r.callback_query(F.data == "btn_chat_info")
    async def on_chat_info(call: CallbackQuery):
        await call.answer()
        active_prov = llm_router.active_provider
        active_model = llm_router.get_current_model_for_provider(active_prov if active_prov != "auto" else "openrouter")
        text = (
            f"💬 *Режим прямого диалога с нейросетью:*\n\n"
            f"Просто напишите сюда любой текст, вопрос, кусок кода или задачу.\n\n"
            f"Текущая активная нейросеть: *{active_prov.upper()}* (`{active_model}`).\n"
            f"Промпты автоматически подстраиваются под мощность выбранной модели из файла `config/prompts.yaml` на сервере!"
        )
        await call.message.answer(text, parse_mode="Markdown")

    # --------------------------------------------------------------------------
    # Ручной запуск дайджеста
    # --------------------------------------------------------------------------
    @r.message(Command("digest"))
    @r.callback_query(F.data == "btn_run_digest")
    async def cmd_digest(event: Message | CallbackQuery):
        msg = event if isinstance(event, Message) else event.message
        if isinstance(event, CallbackQuery):
            await event.answer("Сбор новостей и запуск нейросети...")

        wait_msg = await msg.answer("⏳ *Идет сбор свежих новостей и структурирование через ИИ...*", parse_mode="Markdown")

        res = await digest_builder.generate_digest()
        await wait_msg.delete()

        text = res["text"]
        if len(text) > 4000:
            for x in range(0, len(text), 4000):
                await msg.answer(text[x:x+4000], parse_mode="Markdown")
        else:
            await msg.answer(text, parse_mode="Markdown")

    # --------------------------------------------------------------------------
    # Выбор провайдеров и моделей
    # --------------------------------------------------------------------------
    @r.message(Command("model"))
    @r.callback_query(F.data == "btn_select_provider")
    async def cmd_model(event: Message | CallbackQuery):
        msg = event if isinstance(event, Message) else event.message
        if isinstance(event, CallbackQuery):
            await event.answer()

        kb = get_provider_selection_keyboard(llm_router.active_provider)
        await msg.answer("🧠 *Выберите активного ИИ-провайдера:*", parse_mode="Markdown", reply_markup=kb)

    @r.callback_query(F.data.startswith("prov_"))
    async def on_provider_selected(call: CallbackQuery):
        new_provider = call.data.replace("prov_", "")
        llm_router.set_provider(new_provider)
        storage.set_setting("active_provider", new_provider)

        await call.answer(f"Выбран: {new_provider.upper()}")
        kb = get_provider_selection_keyboard(new_provider)
        await call.message.edit_reply_markup(reply_markup=kb)

    @r.callback_query(F.data == "btn_browse_models")
    async def on_browse_models(call: CallbackQuery):
        await call.answer()
        kb = get_provider_browser_keyboard()
        await call.message.answer("🎯 *Выберите провайдера для смены модели (включая бесплатные OpenRouter):*", parse_mode="Markdown", reply_markup=kb)

    @r.callback_query(F.data.startswith("viewmods_"))
    async def on_view_models(call: CallbackQuery):
        prov = call.data.replace("viewmods_", "")
        await call.answer()
        curr_m = llm_router.get_current_model_for_provider(prov)
        kb = get_models_keyboard(prov, curr_m)
        await call.message.answer(f"📋 *Список моделей для {prov.upper()}:*", parse_mode="Markdown", reply_markup=kb)

    @r.callback_query(F.data.startswith("setm_"))
    async def on_set_model(call: CallbackQuery):
        # Format: setm_{provider}__{model_id_escaped}
        raw = call.data.replace("setm_", "")
        prov, escaped_model = raw.split("__", 1)
        model_id = escaped_model.replace("--", "/").replace("==", ":")

        llm_router.set_model(prov, model_id)
        storage.set_setting(f"model_{prov}", model_id)

        await call.answer(f"Установлена модель: {model_id}")
        kb = get_models_keyboard(prov, model_id)
        await call.message.edit_reply_markup(reply_markup=kb)

    # --------------------------------------------------------------------------
    # Статус системы и источники
    # --------------------------------------------------------------------------
    @r.message(Command("status"))
    @r.callback_query(F.data == "btn_status")
    async def cmd_status(event: Message | CallbackQuery):
        msg = event if isinstance(event, Message) else event.message
        if isinstance(event, CallbackQuery):
            await event.answer()

        local_on = await llm_router.check_local_health()
        text = (
            f"📊 *Статус узлов системы (2026):*\n\n"
            f"1. 💻 *Ноутбук (Сервер 24/7):* 🟢 В сети\n"
            f"   - Планировщик (08:50): OK\n"
            f"   - База дедупликации SQLite: OK\n\n"
            f"2. 🖥 *Основной ПК (LM Studio + Qwen 2.5):*\n"
            f"   - Статус сервера LM Studio: {'🟢 Доступен (порт 1234)' if local_on else '⚪️ Не отвечает (офлайн/сон)'}\n"
            f"   - Адрес: `{llm_router.config.local.base_url}`\n\n"
            f"3. ☁️ *Облачные шлюзы:*\n"
            f"   - GroqCloud: {'🟢 Подключен' if llm_router.config.groq.api_key else '⚪️ Нет ключа'}\n"
            f"   - Google Gemini: {'🟢 Подключен' if llm_router.config.gemini.api_key else '⚪️ Нет ключа'}\n"
            f"   - OpenRouter (~25 free моделей): {'🟢 Подключен' if llm_router.config.openrouter.api_key else '⚪️ Нет ключа'}\n\n"
            f"Текущий режим: *{llm_router.active_provider.upper()}*"
        )
        await msg.answer(text, parse_mode="Markdown")

    @r.message(Command("sources"))
    @r.callback_query(F.data == "btn_sources")
    async def cmd_sources(event: Message | CallbackQuery):
        msg = event if isinstance(event, Message) else event.message
        if isinstance(event, CallbackQuery):
            await event.answer()

        sources = load_sources().get("sources", {})
        tg = sources.get("telegram_channels", [])
        rss = sources.get("rss_feeds", [])

        tg_list = "\n".join([f"• @{c.get('username')} _({c.get('category')})_" for c in tg]) or "Нет"
        rss_list = "\n".join([f"• {r.get('name', r.get('url'))} _({r.get('category')})_" for r in rss]) or "Нет"

        text = (
            f"📡 *Подключенные источники для дайджеста:*\n\n"
            f"📢 *Telegram-каналы:*\n{tg_list}\n\n"
            f"📰 *RSS-ленты:*\n{rss_list}\n\n"
            f"_Чтобы добавить канал, просто впишите его в config/sources.yaml на ноутбуке._"
        )
        await msg.answer(text, parse_mode="Markdown")

    # --------------------------------------------------------------------------
    # ПРЯМОЙ ЧАТ С ИИ (Обработка любых пользовательских текстовых сообщений)
    # --------------------------------------------------------------------------
    @r.message(F.text & ~F.text.startswith("/"))
    async def on_user_chat_message(msg: Message):
        if not is_admin(msg.from_user.id):
            return

        # Показываем статус "печатает..." в Telegram
        await msg.bot.send_chat_action(msg.chat.id, "typing")

        user_query = msg.text
        logger.info(f"Получен прямой запрос от пользователя: {user_query[:60]}...")

        res = await llm_router.generate_response(task="chat", user_prompt=user_query)

        if not res.get("success"):
            await msg.answer(f"⚠️ {res.get('content')}")
            return

        answer_text = res["content"]
        footer = f"\n\n🤖 *{res.get('provider').upper()}* (`{res.get('model')}`) • {res.get('latency')}с"
        if res.get("fallback_occurred"):
            footer += " _(failover)_"

        full_text = answer_text + footer

        if len(full_text) > 4000:
            for x in range(0, len(full_text), 4000):
                await msg.answer(full_text[x:x+4000], parse_mode="Markdown")
        else:
            await msg.answer(full_text, parse_mode="Markdown")

    return r
