import pytest

from module import EDIT, MANAGE, VIEW, Project, User, is_allowed, require, visible_projects

PROJECT = Project("p1", {"ed": "editor", "vi": "viewer"}, public=True)


def test_happy_roles_and_public_access():
    assert is_allowed(User("ed"), EDIT, PROJECT) and not is_allowed(User("ed"), MANAGE, PROJECT)
    assert is_allowed(User("stranger"), VIEW, PROJECT) and not is_allowed(User("stranger"), EDIT, PROJECT)
    assert is_allowed(User("root", admin=True), MANAGE, PROJECT)
    assert not is_allowed(User("ed", active=False), VIEW, PROJECT)
    assert visible_projects(User("vi"), [PROJECT, Project("p2", {})]) == [PROJECT]


def test_defect_deactivated_admin_is_refused():
    former_admin = User("root", active=False, admin=True)
    assert not is_allowed(former_admin, VIEW, PROJECT)
    with pytest.raises(PermissionError):
        require(former_admin, MANAGE, PROJECT)
