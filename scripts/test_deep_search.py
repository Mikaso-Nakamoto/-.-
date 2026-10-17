import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.storage import Storage
from src.pipeline.deep_search import DeepSearchEngine

async def test_deep_search_flow():
    print("=== TEST 1: DeepSearchEngine User Sources Priority ===")
    storage = Storage(":memory:")
    engine = DeepSearchEngine()

    storage.save_news_items([
        {
            "channel": "medvedev_telegram",
            "title": "Заявление о ядерном сдерживании",
            "content": "Враги России должны помнить о неотвратимости возмездия и последствиях ударов по нашей территории.",
            "url": "https://t.me/medvedev_telegram/501",
            "published_at": "2026-10-10 15:00:00"
        },
        {
            "channel": "infomil_live",
            "title": "Анализ применения БПЛА Герань-2",
            "content": "Модернизированные дроны-камикадзе с термобарической боевой частью поразили склады.",
            "url": "https://t.me/infomil_live/800",
            "published_at": "2026-10-10 16:00:00"
        }
    ])

    user_sources = await engine.search_user_sources("возмездие и ядерное сдерживание Медведев", storage, limit=3)
    assert len(user_sources) > 0, "Should find user sources"
    assert user_sources[0]["channel"] == "medvedev_telegram"
    print(f"✓ Found user source prioritized: [{user_sources[0]['channel']}] {user_sources[0]['title']}")

    print("\n=== TEST 2: Fallback Summary Generation (Without live LLM) ===")
    summary = engine._build_fallback_summary(
        query="ядерное сдерживание",
        user_sources=user_sources,
        web_sources=[{"title": "Reuters: Security Analysis", "url": "https://reuters.com/news/1", "snippet": "Global discussion on deterrence."}],
        web_query="ядерное сдерживание"
    )
    assert "По данным ваших каналов и мониторинга" in summary
    assert "Внешние авторитетные источники" in summary
    assert "https://t.me/medvedev_telegram/501" in summary
    print("✓ Fallback multi-contour summary generated cleanly with correct sections.")

    print("\n=== TEST 3: Fallback Summary with Wikipedia Encyclopedic Dossier ===")
    wiki_summary = engine._build_fallback_summary(
        query="Генерал Суровикин",
        user_sources=[],
        web_sources=[
            {
                "title": "Википедия: Суровикин, Сергей Владимирович",
                "url": "https://ru.wikipedia.org/wiki/Суровикин,_Сергей_Владимирович",
                "snippet": "Сергей Владимирович Суровикин — российский военачальник, генерал армии (2021), Герой РФ (2017).",
                "trust_badge": "🟢 [Траствик / Википедия]"
            },
            {
                "title": "TOPNews.RU: вернулся из Алжира в Москву",
                "url": "https://topnews.ru/news/123",
                "snippet": "Служебная командировка в Алжир завершена.",
                "trust_badge": "🌐 [TOPNews.RU]"
            }
        ]
    )
    assert "Фактологическая справка:" in wiki_summary
    assert "Суровикин, Сергей Владимирович" in wiki_summary
    assert "TOPNews.RU" in wiki_summary
    print("✓ Wikipedia encyclopedic dossier successfully generated in fallback mode.")

    print("\n=== TEST 4: Conversational Query Cleaning and Channel Extraction ===")
    from src.pipeline.semantic_search import SemanticSearchEngine
    sem_engine = SemanticSearchEngine()
    test_q = 'в каком-то посте в Военной сводке или в Осведомителе вроде был пост про то, что русские подменяют картинку на украинских устройствах обнаружения дронов "Чуйка" на лягушку вместо видео с камеры русского дрона. Найди мне этот пост в канале и дай его сюда или скинь на него ссылку'
    clean_q, targets = sem_engine.extract_search_intent(test_q)
    assert "voennaja_svodka" in targets, "Should detect voennaja_svodka"
    assert "infomil_live" in targets, "Should detect infomil_live (Осведомитель)"
    assert "подменяют картинку" in clean_q, "Should extract core topic"
    assert "в каком-то посте" not in clean_q, "Should strip conversational opener"
    assert "скинь на него ссылку" not in clean_q, "Should strip conversational tail"
    print(f"✓ Extracted targets: {targets}")
    print(f"✓ Cleaned core query: «{clean_q}»")

    print("\n=== ALL DEEP SEARCH TESTS PASSED ===")

if __name__ == "__main__":
    asyncio.run(test_deep_search_flow())
