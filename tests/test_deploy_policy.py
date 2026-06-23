from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_readme_routes_managed_launch_through_nunchi_auth():
    readme = (REPO_ROOT / "README.md").read_text()

    assert "https://auth.nunchi.trade" in readme
    assert "railway.com/new/template" not in readme
    assert "railway.com/button.svg" not in readme
    assert "Self-Hosted" not in readme
    assert "self-host" not in readme.lower()
    assert "BYO Railway" not in readme


def test_public_docker_and_railway_deploy_configs_are_not_published():
    forbidden_paths = [
        "Dockerfile",
        "railway.toml",
        "deploy/openclaw-railway/Dockerfile",
        "deploy/openclaw-railway/railway.toml",
        "deploy/hermes-railway/Dockerfile",
        "deploy/hermes-railway/railway.toml",
    ]

    for path in forbidden_paths:
        assert not (REPO_ROOT / path).exists(), f"{path} reintroduces public self-host deployment"


def test_docs_do_not_advertise_self_host_deployment():
    docs = "\n".join(
        path.read_text()
        for path in [
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "api-reference.md",
            REPO_ROOT / "docs" / "key_management.md",
            REPO_ROOT / "docs" / "RUNBOOK.md",
        ]
    )

    forbidden_phrases = [
        "Deploy on Railway",
        "BYO Railway",
        "Self-Hosted",
        "self-host",
        "deploy to Railway",
        "Railway dashboard",
        "railway up",
        "included `railway.toml`",
    ]
    for phrase in forbidden_phrases:
        haystack = docs.lower() if phrase == "self-host" else docs
        needle = phrase.lower() if phrase == "self-host" else phrase
        assert needle not in haystack
