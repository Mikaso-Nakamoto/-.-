import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from src.config import load_app_config
from src.pipeline.storage import Storage
from src.llm.router import LLMRouter
from src.pipeline.digest_builder import DigestBuilder
from src.scheduler.cron_scheduler import DigestScheduler
from src.bot.handlers import setup_router

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

    # Инициализация сборщика дайджеста
    digest_builder = DigestBuilder(storage, llm_router)

    # Инициализация Telegram бота
    bot = Bot(token=cfg.bot.token)
    dp = Dispatcher()

    router = setup_router(digest_builder, llm_router, storage, cfg.bot.admin_id)
    dp.include_router(router)

    # Инициализация планировщика (08:50)
    scheduler = DigestScheduler(cfg.scheduler, digest_builder, bot, cfg.bot.admin_id)
    scheduler.start()

    logger.info("🚀 Автономный ИИ-хаб успешно запущен и готов к работе!")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.stop()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен пользователем.")
