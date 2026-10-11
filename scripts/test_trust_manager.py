import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.trust_manager import TrustManager

def test_trust_manager():
    tm = TrustManager()

    # 1. Test verified CVE / Exploit source
    res1 = tm.verify_url("https://www.exploit-db.com/exploits/52110")
    assert res1["verified"] is True
    assert "CVE & Sec" in res1["badge"]
    print("✓ Exploit-DB verified:", res1["badge"])

    # 2. Test verified OSINT / LostArmour source
    res2 = tm.verify_url("https://lostarmour.info/armour")
    assert res2["verified"] is True
    assert "Объективный контроль" in res2["badge"]
    print("✓ LostArmour verified:", res2["badge"])

    # 3. Test reputable news/it source
    res3 = tm.verify_url("https://xakep.ru/2026/10/10/linux-kernel-bug/")
    assert res3["verified"] is True
    assert "Профильный IT-источник" in res3["badge"]
    print("✓ Xakep.ru verified:", res3["badge"])

    # 4. Test normal web source
    res4 = tm.verify_url("https://example.com/some/article")
    assert res4["verified"] is False
    assert "Открытый Web" in res4["badge"]
    print("✓ Standard web source categorized:", res4["badge"])

    print("=== ALL TRUST MANAGER TESTS PASSED ===")

if __name__ == "__main__":
    test_trust_manager()
