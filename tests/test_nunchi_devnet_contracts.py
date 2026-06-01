import json
from pathlib import Path


MANIFEST = Path(__file__).resolve().parents[1] / "configs" / "nunchi_devnet_contracts.json"


def test_nunchi_devnet_manifest_has_required_contracts():
    manifest = json.loads(MANIFEST.read_text())
    addresses = manifest["addresses"]

    required = [
        "IdentityRegistry",
        "ReputationRegistry",
        "ValidationRegistry",
        "WorkerRegistry",
        "BountyMarket",
        "JobTypeRegistry",
        "ex.USDC",
        "ex.MockPythOracle",
        "ex.PythFeeds",
        "ex.OracleManager",
        "AccountNFT",
        "PerpOrderbook",
    ]

    assert manifest["chainId"] == 1337
    assert manifest["rpcUrl"] == "https://nunchi-devnet.nunchi.trade"
    for name in required:
        assert addresses[name].startswith("0x")
        assert len(addresses[name]) == 42


def test_nunchi_devnet_manifest_documents_deferred_cannon_contracts():
    manifest = json.loads(MANIFEST.read_text())
    deferred = manifest["deferred"]

    assert "ClearingHouse" in deferred["phase_B_cannon"]
    assert any("MarketRegistry" in item for item in deferred["phase_B_cannon"])
    assert "PerpOrderbook" in manifest["addresses"]
    assert manifest["placeholders"]["MIRAGE_CH_used_in_PerpOrderbook_ctor"].startswith("0x")
