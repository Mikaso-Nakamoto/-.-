import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.osint_toolkit import OSINTToolkit
from src.pipeline.query_decomposer import QueryDecomposer
from src.pipeline.ai_intent_router import AIIntentRouter

async def run_osint_tests():
    print("=== TEST 1: Query Decomposer Cloud-style Acronym Expansion ===")
    dec = QueryDecomposer()
    res = dec.fallback_decompose("ССО РФ")
    assert "Силы специальных операций РФ" in res["specific_entities"]
    assert any("задачи структура" in q for q in res["search_queries"])
    print(f"✓ Decomposed 'ССО РФ' -> entities: {res['specific_entities']}")
    print(f"✓ Formulated cloud-style queries: {res['search_queries']}")

    vks_res = dec.fallback_decompose("новости ВКС")
    assert "Воздушно-космические силы РФ" in vks_res["specific_entities"]
    print("✓ Decomposed 'ВКС' acronym.")

    reb_res = dec.fallback_decompose("комплексы РЭБ")
    assert "Радиоэлектронная борьба" in reb_res["specific_entities"]
    print("✓ Decomposed 'РЭБ' acronym.")

    print("\n=== TEST 2: OSINT Toolkit Entity Type Detection ===")
    toolkit = OSINTToolkit()
    assert toolkit.detect_entity_type("192.168.1.1") == "ip"
    assert toolkit.detect_entity_type("8.8.8.8") == "ip"
    assert toolkit.detect_entity_type("CVE-2024-3094") == "cve"
    assert toolkit.detect_entity_type("0x71C6634C0131365857102a8397570494aF453848") == "crypto"
    assert toolkit.detect_entity_type("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") == "crypto"
    assert toolkit.detect_entity_type("github.com") == "domain"
    assert toolkit.detect_entity_type("tass.ru") == "domain"
    assert toolkit.detect_entity_type("durov") == "username"
    print("✓ All entity types successfully recognized.")

    print("\n=== TEST 3: OSINT Framework Tools Knowledge Directory ===")
    all_tools = toolkit.get_framework_tools()
    assert len(all_tools) >= 5
    geoint = toolkit.get_framework_tools("geoint")
    assert len(geoint) > 0
    assert any("Sentinel" in t["name"] for t in geoint[0]["tools"])
    print(f"✓ OSINT Framework directory verified: {len(all_tools)} categories loaded.")

    print("\n=== TEST 4: Google Dorks Generation ===")
    dorks = toolkit.generate_dorks("target-corp.com")
    assert len(dorks) >= 4
    assert any("ext:env" in d["dork"] for d in dorks)
    assert any("inurl:admin" in d["dork"] for d in dorks)
    print("✓ Automated Google/Yandex dorks generated successfully.")

    print("\n=== TEST 5: Fallback OSINT Reports Generation ===")
    # IP report
    ip_rep = toolkit._build_fallback_report("1.1.1.1", {
        "type": "ip",
        "hostname": "one.one.one.one",
        "geoip": {"country": "Australia", "city": "Sydney", "isp": "Cloudflare", "asn": "AS13335"},
        "open_ports": [53, 80, 443],
        "reputation_links": {"AbuseIPDB": "https://abuseipdb.com/check/1.1.1.1"}
    })
    assert "one.one.one.one" in ip_rep
    assert "Australia" in ip_rep
    assert "443" in ip_rep

    # Domain report
    domain_rep = toolkit._build_fallback_report("example.com", {
        "type": "domain",
        "ips": ["93.184.216.34"],
        "rdap": {"registrar": "ICANN", "created": "1995-08-14"},
        "subdomains": ["api.example.com", "mail.example.com"],
        "dorks": dorks
    })
    assert "93.184.216.34" in domain_rep
    assert "api.example.com" in domain_rep

    # Email & Phone report
    email_rep = toolkit._build_fallback_report("victim@gmail.com", {
        "type": "email",
        "registered_services": ["Google", "Twitter", "Spotify"],
        "mx_records": ["gmail-smtp-in.l.google.com"],
        "breach_search_links": ["https://haveibeenpwned.com/account/victim@gmail.com"]
    })
    assert "Spotify" in email_rep
    assert "gmail-smtp-in.l.google.com" in email_rep

    phone_rep = toolkit._build_fallback_report("+79991234567", {
        "type": "phone",
        "country": "Россия",
        "carrier": "МегаФон",
        "search_links": ["https://t.me/+79991234567"]
    })
    assert "МегаФон" in phone_rep
    assert "Россия" in phone_rep

    print("✓ Technical OSINT fallback dossiers generated cleanly.")

    print("\n=== TEST 6: Intent Router OSINT Integration ===")
    router = AIIntentRouter()
    r1 = router.fast_heuristic_classify("/osint example.com")
    assert r1["action"] == "osint"
    assert r1["clean_query"] == "example.com"

    r2 = router.fast_heuristic_classify("пробей ip 8.8.8.8")
    assert r2["action"] == "osint"
    assert r2["clean_query"] == "8.8.8.8"

    r3 = router.fast_heuristic_classify("CVE-2024-3094")
    assert r3["action"] == "osint"
    assert r3["clean_query"] == "CVE-2024-3094"

    print("✓ OSINT intents successfully classified by heuristic router.")
    print("\n=== ALL OSINT TOOLKIT TESTS PASSED ===")

if __name__ == "__main__":
    asyncio.run(run_osint_tests())
