import asyncio
import logging
import sys
import uvicorn
from aiogram import Bot, Dispatcher
from src.config import load_app_config
from src.pipeline.storage import Storage
from src.llm.router import LLMRouter
from src.pipeline.digest_builder import DigestBuilder
from src.scheduler.cron_scheduler import DigestScheduler
from src.bot.handlers import setup_router
from src.api.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("main")

async def main():
    cfg = load_app_config()

    if not cfg.bot.token or cfg.bot.token == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.error("КРИТИЧЕСКАЯ ОШИБКА: TELEGRAM_BOT_TOKEN не указан в config.yaml или .env!")
        logger.info("Укажите токен бота перед запуском.")
        return

    # Инициализация хранилища (SQLite WAL)
    storage = Storage(cfg.storage.db_path)

    # Инициализация роутера LLM
    saved_provider = storage.get_setting("active_provider")
    if saved_provider:
        cfg.llm.default_provider = saved_provider

    llm_router = LLMRouter(cfg.llm)

    # Восстановление сохраненных моделей для каждого провайдера
    for prov in ["openrouter", "groq", "gemini", "local"]:
        saved_m = storage.get_setting(f"model_{prov}")
        if saved_m:
            llm_router.set_model(prov, saved_m)

    # Инициализация сборщика дайджеста
    digest_builder = DigestBuilder(storage, llm_router)

    # Инициализация Telegram бота
    bot = Bot(token=cfg.bot.token)
    dp = Dispatcher()

    # Инициализация планировщика (08:50)
    scheduler = DigestScheduler(cfg.scheduler, digest_builder, bot, cfg.bot.admin_id)
    scheduler.start()

    router = setup_router(digest_builder, llm_router, storage, cfg.bot.admin_id, scheduler)
    dp.include_router(router)

    # Инициализация Web/PWA сервера на FastAPI
    api_app = create_app(storage, llm_router, digest_builder)
    uv_cfg = uvicorn.Config(app=api_app, host="0.0.0.0", port=8000, log_level="warning")
    web_server = uvicorn.Server(uv_cfg)

    logger.info("🚀 Автономный ИИ-хаб и Web/PWA интерфейс запущены (порт 8000)!")
    try:
        await asyncio.gather(
            dp.start_polling(bot),
            web_server.serve()
        )
    finally:
        scheduler.stop()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен пользователем.")
