import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.ai_intent_router import AIIntentRouter

async def test_intent_router():
    router = AIIntentRouter()

    print("=== TEST 1: Heuristic Routing for Post Finding ===")
    q1 = 'в каком-то посте в Военной сводке или в Осведомителе вроде был пост про то, что русские подменяют картинку на украинских устройствах обнаружения дронов "Чуйка" на лягушку. Найди мне этот пост'
    r1 = await router.classify(q1)
    assert r1["action"] == "find_post", f"Expected find_post, got {r1['action']}"
    assert "voennaja_svodka" in r1["target_channels"], "Should extract voennaja_svodka"
    assert "infomil_live" in r1["target_channels"], "Should extract infomil_live"
    print(f"✓ Post finding correctly routed: action={r1['action']}, targets={r1['target_channels']}")

    print("\n=== TEST 2: Factual/News Query -> Deep Search ===")
    q2 = "иск против цукерберга и Meta 40 штатов"
    r2 = await router.classify(q2)
    assert r2["action"] == "deep_search", f"Expected deep_search, got {r2['action']}"
    print(f"✓ Factual lawsuit inquiry routed to deep_search: action={r2['action']}, query='{r2['clean_query']}'")

    print("\n=== TEST 3: Network Search Query -> Deep Search ===")
    q3 = "найди в интернете информацию о новых модификациях дрона Герань-3"
    r3 = await router.classify(q3)
    assert r3["action"] == "deep_search", f"Expected deep_search, got {r3['action']}"
    print(f"✓ Network search correctly routed: action={r3['action']}, query='{r3['clean_query']}'")

    print("\n=== TEST 4: Status and Diagnostics Query ===")
    q4 = "как там сервер, в строю ли бот и видит ли LM Studio видеокарту?"
    r4 = await router.classify(q4)
    assert r4["action"] == "status", f"Expected status, got {r4['action']}"
    print(f"✓ Status inquiry correctly routed: action={r4['action']}")

    print("\n=== TEST 5: Digest / Summary Request ===")
    q5 = "подготовь мне утреннюю сводку за сегодня по фронту и дронам"
    r5 = await router.classify(q5)
    assert r5["action"] == "digest", f"Expected digest, got {r5['action']}"
    print(f"✓ Digest inquiry correctly routed: action={r5['action']}, focus='{r5['clean_query']}'")

    print("\n=== TEST 6: Casual Chat / Programming Assistance ===")
    q6 = "привет, напиши функцию на Python для парсинга HTML"
    r6 = await router.classify(q6)
    assert r6["action"] == "chat", f"Expected chat, got {r6['action']}"
    print(f"✓ Conversational request correctly routed to chat: action={r6['action']}")

    print("\n=== TEST 7: Frontline Map / Military Summary ===")
    q7 = "покажи карту сво military summary"
    r7 = await router.classify(q7)
    assert r7["action"] == "frontline_map", f"Expected frontline_map, got {r7['action']}"
    print(f"✓ Frontline map inquiry correctly routed: action={r7['action']}")

    print("\n=== ALL INTENT ROUTER TESTS PASSED ===")

if __name__ == "__main__":
    asyncio.run(test_intent_router())
