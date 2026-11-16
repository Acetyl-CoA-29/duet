from module import SessionStore


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def test_happy_fresh_session_and_revoke():
    clock = Clock()
    store = SessionStore(ttl_minutes=30, clock=clock)
    session = store.create("ann")
    clock.now += 5
    assert store.get(session.token).user_id == "ann"
    assert store.revoke(session.token) and store.get(session.token) is None
    other = store.create("ben")
    clock.now += 31 * 60
    assert store.get(other.token) is None and store.purge_expired() == 0


def test_defect_new_session_lasts_the_full_timeout():
    clock = Clock()
    store = SessionStore(ttl_minutes=30, clock=clock)
    session = store.create("ann")
    clock.now += 10 * 60
    assert store.get(session.token) is not None
