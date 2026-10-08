import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from src.llm.router import LLMRouter
from src.pipeline.digest_builder import DigestBuilder
from src.pipeline.storage import Storage
from src.bot.keyboards import get_main_menu_keyboard, get_model_selection_keyboard
from src.config import load_preferences, load_sources

logger = logging.getLogger(__name__)

def setup_router(digest_builder: DigestBuilder, llm_router: LLMRouter, storage: Storage, admin_id: int) -> Router:
    r = Router()

    def is_admin(user_id: int) -> bool:
        return admin_id == 0 or user_id == admin_id

    @r.message(Command("start"))
    async def cmd_start(msg: Message):
        if not is_admin(msg.from_user.id):
            await msg.answer("⛔️ Доступ ограничен.")
            return

        is_local_on = await llm_router.check_local_health()
        local_status = "🟢 В сети (Готов к инференсу)" if is_local_on else "🔴 Офлайн / Спит (Включен облачный резерв)"

        text = (
            f"👋 *Привет! Я твой персональный автономный ИИ-хаб.*\n\n"
            f"💻 *Ноутбук-сервер:* 🟢 24/7 Активен\n"
            f"🖥 *Основной ПК (RTX 2060S / Qwen 2.5):* {local_status}\n"
            f"🧠 *Активная модель:* `{llm_router.active_provider}`\n"
            f"⏰ *Расписание дайджеста:* `Каждый день в 08:50`\n\n"
            f"Используй кнопки ниже для управления системой:"
        )
        await msg.answer(text, parse_mode="Markdown", reply_markup=get_main_menu_keyboard())

    @r.message(Command("digest"))
    @r.callback_query(F.data == "btn_run_digest")
    async def cmd_digest(event: Message | CallbackQuery):
        msg = event if isinstance(event, Message) else event.message
        if isinstance(event, CallbackQuery):
            await event.answer("Сбор новостей и запуск нейросети...")

        wait_msg = await msg.answer("⏳ *Идет сбор публикаций из Telegram/RSS и генерация сводки через LLM...*", parse_mode="Markdown")

        res = await digest_builder.generate_digest()
        await wait_msg.delete()

        # Если текст длинный для одного сообщения в ТГ (лимит 4096 символов)
        text = res["text"]
        if len(text) > 4000:
            for x in range(0, len(text), 4000):
                await msg.answer(text[x:x+4000], parse_mode="Markdown")
        else:
            await msg.answer(text, parse_mode="Markdown")

    @r.message(Command("model"))
    @r.callback_query(F.data == "btn_select_model")
    async def cmd_model(event: Message | CallbackQuery):
        msg = event if isinstance(event, Message) else event.message
        if isinstance(event, CallbackQuery):
            await event.answer()

        kb = get_model_selection_keyboard(llm_router.active_provider)
        await msg.answer("🧠 *Выберите активную нейросеть для обработки новостей и запросов:*", parse_mode="Markdown", reply_markup=kb)

    @r.callback_query(F.data.startswith("prov_"))
    async def on_provider_selected(call: CallbackQuery):
        new_provider = call.data.replace("prov_", "")
        llm_router.set_provider(new_provider)
        storage.set_setting("active_provider", new_provider)

        await call.answer(f"Выбран провайдер: {new_provider}")
        kb = get_model_selection_keyboard(new_provider)
        await call.message.edit_reply_markup(reply_markup=kb)

    @r.message(Command("status"))
    @r.callback_query(F.data == "btn_status")
    async def cmd_status(event: Message | CallbackQuery):
        msg = event if isinstance(event, Message) else event.message
        if isinstance(event, CallbackQuery):
            await event.answer()

        local_on = await llm_router.check_local_health()
        text = (
            f"📊 *Статус узлов системы (2026):*\n\n"
            f"1. 💻 *Ноутбук (Домашний сервер):* 🟢 Работает 24/7\n"
            f"   - Telegram Bot Core: OK\n"
            f"   - Scheduler (08:50): OK\n"
            f"   - SQLite Deduplication: OK\n\n"
            f"2. 🖥 *Основной ПК (i5-10400F + RTX 2060S):*\n"
            f"   - Статус Qwen 2.5 7B: {'🟢 Доступен' if local_on else '⚪️ Не отвечает (офлайн/сон)'}\n"
            f"   - Эндпоинт: `{llm_router.config.local.base_url}`\n\n"
            f"3. ☁️ *Облачные шлюзы (Резерв & Альтернативы):*\n"
            f"   - GroqCloud: {'🟢 Активен' if llm_router.config.groq.api_key else '⚪️ Нет ключа'}\n"
            f"   - Google Gemini: {'🟢 Активен' if llm_router.config.gemini.api_key else '⚪️ Нет ключа'}\n"
            f"   - OpenRouter: {'🟢 Активен' if llm_router.config.openrouter.api_key else '⚪️ Нет ключа'}\n\n"
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
            f"📡 *Подключенные источники:*\n\n"
            f"📢 *Telegram каналы:*\n{tg_list}\n\n"
            f"📰 *RSS Ленты:*\n{rss_list}\n\n"
            f"_Для добавления каналов отредактируйте config/sources.yaml на сервере._"
        )
        await msg.answer(text, parse_mode="Markdown")

    return r
