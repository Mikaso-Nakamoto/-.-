#!/usr/bin/env python3
"""
Скрипт полной очистки базы данных и архивов дайджестов:
- Очищает таблицы seen_items, news_feed, full_digests, digest_history, chat_history в SQLite.
- Сохраняет настройки пользователя (admin_id, выбранные модели, провайдеры).
- Удаляет все временные и тестовые файлы дайджестов в data/digests/ (.md, .html).
- Выполняет SQLite VACUUM для сжатия базы до минимального размера.
"""

import os
import sys
import shutil
from pathlib import Path

# Обеспечиваем корректный импорт модулей проекта
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.config import load_app_config
from src.pipeline.storage import Storage

def clean_database_and_digests():
    cfg = load_app_config()
    db_path = cfg.storage.db_path

    print(f"🧹 Инициализация очистки базы данных: {db_path}...")
    storage = Storage(db_path)
    storage.purge_all_news_and_digests()
    print("✅ Таблицы SQLite (seen_items, news_feed, full_digests, digest_history, chat_history) очищены!")

    # Очистка локальных файлов отчетов
    digests_dir = Path("data/digests")
    if digests_dir.exists():
        count = 0
        for f in digests_dir.glob("*.*"):
            try:
                f.unlink()
                count += 1
            except Exception as e:
                print(f"⚠️ Не удалось удалить {f}: {e}")
        print(f"✅ Удалено архивных файлов дайджестов: {count}")
    else:
        print("ℹ️ Директория data/digests пуста.")

    print("\n🎉 Все тестовые новости и архивы успешно зачищены!")
    print("Следующий запуск начнется с чистого листа по актуальному списку источников.")

if __name__ == "__main__":
    clean_database_and_digests()
