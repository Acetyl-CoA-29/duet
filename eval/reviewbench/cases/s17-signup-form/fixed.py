"""Validate sign-up form fields before an account is created."""
import re
from typing import Dict, List, Mapping, Tuple

USERNAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,19}")
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
RESERVED_NAMES = frozenset({"admin", "root", "support", "system"})
MIN_PASSWORD_LENGTH = 10
MAX_EMAIL_LENGTH = 254


class FormError(ValueError):
    """One or more fields were rejected; `problems` pairs each field with a message."""

    def __init__(self, problems: List[Tuple[str, str]]) -> None:
        super().__init__("; ".join(f"{field}: {message}" for field, message in problems))
        self.problems = problems


def check_username(name: object) -> str:
    if not isinstance(name, str) or not USERNAME_PATTERN.fullmatch(name):
        raise ValueError("use 3 to 20 lower-case letters, digits or underscores, starting with a letter")
    if name in RESERVED_NAMES:
        raise ValueError("that name is reserved")
    return name


def check_email(email: object) -> str:
    if not isinstance(email, str):
        raise ValueError("enter an email address")
    address = email.strip()
    if len(address) > MAX_EMAIL_LENGTH or not EMAIL_PATTERN.fullmatch(address):
        raise ValueError("enter a valid email address")
    return address.lower()


def check_password(password: object, username: str) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"use at least {MIN_PASSWORD_LENGTH} characters")
    if username and username.lower() in password.lower():
        raise ValueError("the password must not contain the username")
    return password


def validate_signup(form: Mapping[str, object]) -> Dict[str, str]:
    """Return the cleaned fields, or raise FormError naming every field that was rejected."""
    cleaned: Dict[str, str] = {}
    problems: List[Tuple[str, str]] = []
    checks = (("username", check_username), ("email", check_email))
    for field, check in checks:
        try:
            cleaned[field] = check(form.get(field))
        except ValueError as exc:
            problems.append((field, str(exc)))
    try:
        cleaned["password"] = check_password(form.get("password"), cleaned.get("username", ""))
    except ValueError as exc:
        problems.append(("password", str(exc)))
    if problems:
        raise FormError(problems)
    return cleaned
