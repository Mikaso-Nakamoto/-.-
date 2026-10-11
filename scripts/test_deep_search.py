import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.storage import Storage
from src.pipeline.deep_search import DeepSearchEngine
from src.llm.router import LLMRouter
from src.config import load_app_config

async def test_deep_search_flow():
    print("=== TEST 1: Storage Darknet Setting Toggle ===")
    storage = Storage(":memory:")
    assert not storage.is_darknet_search_enabled(), "Default darknet search should be False"
    storage.set_darknet_search(True)
    assert storage.is_darknet_search_enabled(), "Darknet search should be True after toggle"
    storage.set_darknet_search(False)
    assert not storage.is_darknet_search_enabled(), "Darknet search should be False after toggle"
    print("✓ Storage Darknet toggle works properly.")

    print("\n=== TEST 2: DeepSearchEngine Parsing of Ahmia Onion results ===")
    engine = DeepSearchEngine()
    dummy_ahmia_html = """
    <ol class="searchResults">
        <li class="result">
            <h4><a href="/search/redirect?redirect_url=http://breach4xfkdlsms.onion/threads/129">BreachForums Database Leaks</a></h4>
            <p>Database leak discussion and sample credentials dump.</p>
            <cite>http://breach4xfkdlsms.onion</cite>
        </li>
        <li class="result">
            <h4><a href="http://darkforum789xyz.onion/post/55">Exploit Zero-Day Archive</a></h4>
            <p>Analysis of Linux SSH vulnerabilities and mitigation scripts.</p>
            <cite>http://darkforum789xyz.onion</cite>
        </li>
    </ol>
    """
    parsed = engine._parse_ahmia_results(dummy_ahmia_html, limit=2)
    assert len(parsed) == 2, f"Expected 2 results, got {len(parsed)}"
    assert "breach4xfkdlsms.onion" in parsed[0]["url"]
    assert "BreachForums" in parsed[0]["title"]
    assert "darkforum789xyz.onion" in parsed[1]["url"]
    print("✓ Ahmia onion result parsing works properly.")

    print("\n=== TEST 3: DeepSearchEngine User Sources Priority ===")
    # Insert test items in storage
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

    print("\n=== TEST 4: Fallback Summary Generation (Without live LLM) ===")
    summary = engine._build_fallback_summary(
        query="ядерное сдерживание",
        user_sources=user_sources,
        web_sources=[{"title": "Reuters: Security Analysis", "url": "https://reuters.com/news/1", "snippet": "Global discussion on deterrence."}],
        darknet_sources=[{"title": "Hidden Forum Thread", "url": "http://hiddenxyz.onion", "snippet": "Geopolitics thread"}],
        include_darknet=True
    )
    assert "По данным ваших каналов и мониторинга" in summary
    assert "Внешние авторитетные источники" in summary
    assert "Теневой сегмент (.onion / Darknet)" in summary
    assert "https://t.me/medvedev_telegram/501" in summary
    print("✓ Fallback multi-contour summary generated cleanly with correct sections.")

    print("\n=== TEST 5: Conversational Query Cleaning and Channel Extraction ===")
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
