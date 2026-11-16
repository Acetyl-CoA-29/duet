from module import load_settings, save_settings


def test_happy_round_trip(tmp_path):
    target = tmp_path / "settings.json"
    assert save_settings(target, {"theme": "dark"}) is True
    assert load_settings(target)["theme"] == "dark"


def test_defect_failed_write_is_reported(tmp_path):
    assert save_settings(tmp_path / "no-such-folder" / "settings.json", {}) is False
