import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.storage import Storage
from src.pipeline.query_decomposer import QueryDecomposer
from src.pipeline.google_grounding import GoogleSearchGroundingEngine
from src.pipeline.deep_search import DeepSearchEngine

async def test_all():
    print("=== TEST 1: Storage Google AI Search Setting ===")
    storage = Storage(":memory:")
    assert storage.is_google_ai_search_enabled() is True, "Default google AI search should be enabled (True)"
    storage.set_google_ai_search(False)
    assert storage.is_google_ai_search_enabled() is False, "Should be False after disabling"
    storage.set_google_ai_search(True)
    assert storage.is_google_ai_search_enabled() is True, "Should be True after re-enabling"
    print("✓ Storage Google AI toggle works properly.")

    print("\n=== TEST 2: QueryDecomposer Heuristic Decomposition ===")
    decomposer = QueryDecomposer()

    # Query with specific Russian drone/weapon model
    res1 = await decomposer.decompose("расскажи про характеристики и применение нового дрона Герань-5")
    assert "Герань-5" in res1["specific_entities"] or any("герань" in e.lower() for e in res1["specific_entities"])
    assert len(res1["search_queries"]) >= 1
    print(f"✓ Decomposed 'Герань-5': entities={res1['specific_entities']}, queries={res1['search_queries']}")

    # Query with multiple models
    res2 = await decomposer.decompose("сравни характеристики дронов Герань-2, Ланцет-3 и Герань-5")
    entities_lower = [e.lower() for e in res2["specific_entities"]]
    assert any("герань-5" in e for e in entities_lower) or any("герань" in e for e in entities_lower)
    assert any("ланцет" in e for e in entities_lower)
    print(f"✓ Decomposed multi-entity query: entities={res2['specific_entities']}")

    # English technical query
    res3 = await decomposer.decompose("benchmarks and specs for RTX 5090 vs RX 8900 XTX")
    assert any("5090" in e for e in res3["specific_entities"])
    print(f"✓ Decomposed GPU query: entities={res3['specific_entities']}")

    print("\n=== TEST 3: QueryDecomposer LLM Output Parsing ===")
    mock_llm_json = json.dumps({
        "specific_entities": ["БПЛА Герань-5", "Турбореактивный двигатель", "КБ Радуга"],
        "main_intent": "Тактико-технические характеристики реактивной модификации Герань-5",
        "search_queries": [
            "БПЛА Герань-5 ТТХ реактивный двигатель",
            "новая модификация Герань 5 скорость дальность",
            "дрон камикадзе Герань-5 боевая часть"
        ],
        "domain": "svo"
    })
    parsed = decomposer._parse_json_result(f"```json\n{mock_llm_json}\n```", "test query")
    assert "БПЛА Герань-5" in parsed["specific_entities"]
    assert len(parsed["search_queries"]) == 3
    print("✓ Markdown JSON fenced output successfully parsed.")

    print("\n=== TEST 4: GoogleSearchGroundingEngine Parsing ===")
    grounding_engine = GoogleSearchGroundingEngine(api_key="mock_key")
    mock_gemini_resp = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                "Российские войска применили новую модификацию БПЛА «Герань-5». "
                                "В отличие от «Герани-2», новый дрон оснащен малогабаритным турбореактивным "
                                "двигателем и развивает скорость до 600 км/ч."
                            )
                        }
                    ]
                },
                "groundingMetadata": {
                    "groundingChunks": [
                        {
                            "web": {
                                "uri": "https://lostarmour.info/news/geran-5-specs",
                                "title": "LostArmour: Новые средства поражения"
                            }
                        },
                        {
                            "web": {
                                "uri": "https://tass.ru/armiya-i-opk/12345678",
                                "title": "ТАСС: Особенности новой техники"
                            }
                        }
                    ]
                }
            }
        ]
    }
    parsed_results = grounding_engine._parse_gemini_response(mock_gemini_resp, "Герань-5")
    assert len(parsed_results) == 2, f"Expected 2 sources, got {len(parsed_results)}"
    assert parsed_results[0]["title"] == "LostArmour: Новые средства поражения"
    assert "lostarmour.info" in parsed_results[0]["url"]
    assert "Объективный контроль" in parsed_results[0]["trust_badge"]
    assert parsed_results[1]["title"] == "ТАСС: Особенности новой техники"
    print("✓ Grounding metadata with trust verification parsed successfully:")
    for r in parsed_results:
        print(f"  - {r['trust_badge']} {r['title']} ({r['url']})")

    print("\n=== TEST 5: DeepSearch Integration with Specific Entities ===")
    deep_engine = DeepSearchEngine()
    storage.save_news_items([
        {
            "channel": "infomil_live",
            "title": "Сводка: применение дронов на фронте",
            "content": "В ночных налетах задействованы стандартные барражирующие боеприпасы и ложные цели.",
            "url": "https://t.me/infomil_live/901",
            "published_at": "2026-10-11 10:00:00"
        },
        {
            "channel": "voennaja_svodka",
            "title": "Первое подтверждение применения Герань-5",
            "content": "Опубликованы обломки реактивной модификации Герань-5 с новой навигационной системой «Комета».",
            "url": "https://t.me/voennaja_svodka/902",
            "published_at": "2026-10-11 11:00:00"
        }
    ])

    results = await deep_engine.search_user_sources(
        query="Герань",
        storage=storage,
        limit=5,
        specific_entities=["Герань-5"]
    )
    assert len(results) > 0
    # The post with "Герань-5" should be prioritized at the top due to entity boost
    assert "Герань-5" in results[0]["title"] or "Герань-5" in results[0]["content"]
    assert results[0]["channel"] == "voennaja_svodka"
    print(f"✓ User source matching specific entity 'Герань-5' prioritized: [{results[0]['channel']}] {results[0]['title']}")

    print("\n=== TEST 6: Broad query 'новое оружие России' does NOT lock into ross_name channel ===")
    from src.pipeline.semantic_search import SemanticSearchEngine
    sem_engine = SemanticSearchEngine()
    cq, tg_channels = sem_engine.extract_search_intent("новое оружие России")
    assert tg_channels == [], f"Should NOT lock to any channel, got {tg_channels}"
    print(f"✓ Channel locking avoided: clean_q='{cq}', channels={tg_channels}")

    decomp = deep_engine.decomposer.fallback_decompose("новое оружие России")
    broad_results = await deep_engine.search_user_sources(
        query="новое оружие России",
        storage=storage,
        limit=5,
        specific_entities=decomp.get("specific_entities")
    )
    assert len(broad_results) > 0, "Should find posts matching decomposed military entities"
    assert broad_results[0]["channel"] == "voennaja_svodka"
    print(f"✓ Broad query successfully retrieved: [{broad_results[0]['channel']}] {broad_results[0]['title']}")

    print("\n=== ALL DECOMPOSER & GROUNDING TESTS PASSED ===")

if __name__ == "__main__":
    asyncio.run(test_all())
