#!/usr/bin/env python3
"""
Скрипт глубокой архивной выгрузки постов из любого публичного Telegram-канала.
Использует Telethon (Telegram Client API / MTProto) для скачивания сотен/тысяч постов
с сохранением в локальную базу данных SQLite для последующего семантического поиска.

Использование:
    python3 scripts/telegram_channel_archive.py --channel milinfolive --limit 300
    python3 scripts/telegram_channel_archive.py --channel Voennaja_svodka --limit 500
"""

import os
import sys
import argparse
import asyncio
from pathlib import Path

# Обеспечиваем корректный импорт модулей проекта
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.config import load_app_config
from src.pipeline.storage import Storage

async def main():
    parser = argparse.ArgumentParser(description="Выгрузка архива постов из Telegram-канала")
    parser.add_argument("--channel", required=True, help="Юзернейм канала без @ (например, milinfolive, Voennaja_svodka)")
    parser.add_argument("--limit", type=int, default=200, help="Количество постов для выгрузки (по умолчанию: 200)")
    args = parser.parse_args()

    channel_name = args.channel.lstrip("@").strip()
    limit = args.limit

    cfg = load_app_config()
    storage = Storage(cfg.storage.db_path)

    print(f"📡 Подключение к каналу @{channel_name} для выгрузки {limit} постов...")

    try:
        from telethon import TelegramClient
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")

        if not api_id or not api_hash:
            print("⚠️ TELEGRAM_API_ID и TELEGRAM_API_HASH не найдены в .env.")
            print("Получить их можно бесплатно за 1 минуту на https://my.telegram.org в разделе 'API development tools'.")
            print("Выполняю выгрузку через публичный Web-сборщик с пагинацией...")
            from src.collectors.telegram_collector import TelegramWebCollector
            import httpx

            collector = TelegramWebCollector([{"username": channel_name, "category": "Архив"}])
            async with httpx.AsyncClient(timeout=15.0) as client:
                items = await collector.fetch_channel(client, {"username": channel_name, "category": "Архив"}, limit=min(limit, 60))
                if items:
                    storage.save_news_items(items)
                    print(f"✅ Успешно выгружено и сохранено в базу: {len(items)} постов из @{channel_name}!")
                else:
                    print(f"⚠️ Не удалось получить посты из канала @{channel_name}.")
            return

        client = TelegramClient("data/userbot_session", int(api_id), api_hash)
        await client.start()
        print("✅ Успешная авторизация в Telegram MTProto!")

        entity = await client.get_entity(channel_name)
        saved_items = []

        async for msg in client.iter_messages(entity, limit=limit):
            text = msg.text or msg.message or ""
            if not text and not msg.media:
                continue

            post_url = f"https://t.me/{channel_name}/{msg.id}"
            published = msg.date.isoformat() if msg.date else ""

            saved_items.append({
                "source_type": "telegram",
                "channel": f"@{channel_name}",
                "category": "Архив & OSINT",
                "title": text[:80] + ("..." if len(text) > 80 else ""),
                "content": text,
                "url": post_url,
                "image_url": "",
                "media_urls": [],
                "published_at": published
            })

        if saved_items:
            storage.save_news_items(saved_items)
            print(f"🎉 Выгружено и добавлено в базу {len(saved_items)} архивных постов из @{channel_name}!")
            print("Теперь по ним доступен мгновенный поиск в боте через команду /find.")

        await client.disconnect()

    except ImportError:
        print("ℹ️ Telethon не установлен. Выполняю выгрузку через встроенный веб-сборщик...")
        from src.collectors.telegram_collector import TelegramWebCollector
        import httpx

        collector = TelegramWebCollector([{"username": channel_name, "category": "Архив"}])
        async with httpx.AsyncClient(timeout=15.0) as client:
            items = await collector.fetch_channel(client, {"username": channel_name, "category": "Архив"}, limit=min(limit, 60))
            if items:
                storage.save_news_items(items)
                print(f"✅ Успешно сохранено {len(items)} постов в базу данных!")
            else:
                print(f"⚠️ Постов не найдено.")

if __name__ == "__main__":
    asyncio.run(main())
