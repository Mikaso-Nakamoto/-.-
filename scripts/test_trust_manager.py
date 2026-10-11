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

    # 3. Test verified Onion directory (OnionTree)
    res3 = tm.verify_url("http://onions53ehmf4q75.onion/services")
    assert res3["verified"] is True
    assert "Verified Onion" in res3["badge"]
    print("✓ OnionTree verified:", res3["badge"])

    # 4. Test unverified / suspicious Onion
    res4 = tm.verify_url("http://unknownscamrandom123456789.onion/login")
    assert res4["verified"] is False
    assert "Неверифицированный Onion" in res4["badge"]
    assert "Риск фишинга" in res4["badge"]
    print("✓ Unknown onion flagged as unverified:", res4["badge"])

    # 5. Test reputable news/it source
    res5 = tm.verify_url("https://xakep.ru/2026/10/10/linux-kernel-bug/")
    assert res5["verified"] is True
    assert "Профильный IT-источник" in res5["badge"]
    print("✓ Xakep.ru verified:", res5["badge"])

    print("=== ALL TRUST MANAGER TESTS PASSED ===")

if __name__ == "__main__":
    test_trust_manager()
