"""Server-side login sessions with an idle timeout."""
import secrets
import time
from typing import Callable, Dict, NamedTuple, Optional

DEFAULT_TTL_MINUTES = 30


class Session(NamedTuple):
    token: str
    user_id: str
    expires_at: float


class SessionStore:
    def __init__(self, ttl_minutes: float = DEFAULT_TTL_MINUTES, clock: Callable[[], float] = time.time) -> None:
        if ttl_minutes <= 0:
            raise ValueError("the timeout must be positive")
        self._ttl_minutes = ttl_minutes
        self._clock = clock
        self._sessions: Dict[str, Session] = {}

    def create(self, user_id: str) -> Session:
        if not user_id:
            raise ValueError("a session needs a user")
        token = secrets.token_urlsafe(32)
        session = Session(token, user_id, self._clock() + self._ttl_minutes * 60)
        self._sessions[token] = session
        return session

    def get(self, token: str) -> Optional[Session]:
        """The live session for `token`, or None when there is none or it has expired."""
        session = self._sessions.get(token)
        if session is None:
            return None
        if session.expires_at <= self._clock():
            del self._sessions[token]
            return None
        return session

    def touch(self, token: str) -> Optional[Session]:
        """Record activity: push the session's expiry back by the full timeout."""
        session = self.get(token)
        if session is None:
            return None
        renewed = session._replace(expires_at=self._clock() + self._ttl_minutes * 60)
        self._sessions[token] = renewed
        return renewed

    def revoke(self, token: str) -> bool:
        return self._sessions.pop(token, None) is not None

    def purge_expired(self) -> int:
        """Drop every expired session and return how many were dropped."""
        now = self._clock()
        expired = [token for token, session in self._sessions.items() if session.expires_at <= now]
        for token in expired:
            del self._sessions[token]
        return len(expired)
