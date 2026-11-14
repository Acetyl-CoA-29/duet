from servicecfg.launch import plan_launches
from servicecfg.resolver import resolve_service


def deployment():
    return {"profiles": {"web": {"workers": 6, "labels": {"team": "frontend"}}},
            "services": {"api": {"extends": ["web"], "timeout": 10}}}


def test_service_can_use_a_profile():
    settings = resolve_service(deployment(), "api")
    assert settings["workers"] == 6
    assert settings["timeout"] == 10
    assert settings["labels"] == {"team": "frontend"}


def test_launch_uses_profile_settings():
    row = plan_launches(deployment())[0]
    assert row["workers"] == 6
    assert row["labels"] == {"team": "frontend"}
