import threading

from module import Tally


def test_happy_single_thread():
    tally = Tally({"hits": 2})
    assert tally.add("hits") == 3 and tally.add("misses", 5) == 5
    assert tally.top(1) == [("misses", 5)] and tally.reset("hits") == 3 and tally.get("hits") == 0


def test_defect_interleaved_updates_are_not_lost():
    tally = Tally()
    first_inside, second_done = threading.Event(), threading.Event()

    def slow_increment(value):
        first_inside.set()
        second_done.wait(0.5)
        return value + 1

    def second():
        tally.add("hits")
        second_done.set()

    one = threading.Thread(target=lambda: tally.update("hits", slow_increment))
    one.start()
    assert first_inside.wait(5)
    two = threading.Thread(target=second)
    two.start()
    one.join(10)
    two.join(10)
    assert tally.get("hits") == 2
