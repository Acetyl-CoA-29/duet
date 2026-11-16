"""Decide whether a user may perform an action on a project."""
from typing import FrozenSet, List, Mapping, NamedTuple, Optional, Sequence

VIEW, EDIT, MANAGE = "view", "edit", "manage"
ACTIONS = (VIEW, EDIT, MANAGE)
ROLE_ALLOWS: Mapping[str, FrozenSet[str]] = {
    "viewer": frozenset({VIEW}),
    "editor": frozenset({VIEW, EDIT}),
    "owner": frozenset({VIEW, EDIT, MANAGE}),
}


class User(NamedTuple):
    id: str
    active: bool = True
    admin: bool = False


class Project(NamedTuple):
    id: str
    members: Mapping[str, str]
    public: bool = False
    archived: bool = False


def role_of(user: User, project: Project) -> Optional[str]:
    """The user's role in the project, or None for someone who is not a member."""
    return project.members.get(user.id)


def is_allowed(user: User, action: str, project: Project) -> bool:
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action!r}")
    if user.admin:
        return True
    if not user.active:
        return False
    if project.archived and action != VIEW:
        return False
    role = role_of(user, project)
    if role is None:
        return project.public and action == VIEW
    return action in ROLE_ALLOWS.get(role, frozenset())


def require(user: User, action: str, project: Project) -> None:
    """Raise PermissionError unless the user may perform the action."""
    if not is_allowed(user, action, project):
        raise PermissionError(f"{user.id} may not {action} project {project.id}")


def visible_projects(user: User, projects: Sequence[Project]) -> List[Project]:
    return [project for project in projects if is_allowed(user, VIEW, project)]
