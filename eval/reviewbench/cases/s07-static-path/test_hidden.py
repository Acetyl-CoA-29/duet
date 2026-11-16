import pytest

from module import Forbidden, locate, resolve


def test_happy_inside_and_plain_escape():
    assert resolve("/srv/public", "/css/site.css?v=3") == "/srv/public/css/site.css"
    assert locate("/srv/public", "/")[0] == "/srv/public/index.html"
    assert locate("/srv/public", "/logo.PNG")[1] == "image/png"
    with pytest.raises(Forbidden):
        resolve("/srv/public", "/../../etc/passwd")


def test_defect_sibling_folder_with_the_same_prefix():
    with pytest.raises(Forbidden):
        resolve("/srv/public", "/../public-backup/dump.sql")
    with pytest.raises(Forbidden):
        resolve("/srv/public", "/%2e%2e/public_old/index.html")
