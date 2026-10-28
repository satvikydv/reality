import asyncio

from app.events import EventBus


def run(coro):
    return asyncio.run(coro)


def test_publish_stamps_and_delivers_to_all_subscribers():
    async def go():
        bus = EventBus()
        a, b = bus.subscribe("dashboard"), bus.subscribe("phone")
        e = bus.publish({"type": "x", "n": 1})
        assert e["seq"] == 1 and "ts" in e
        assert await a.get() == e and await b.get() == e
        assert bus.count("dashboard") == 1 and bus.count("phone") == 1
        a.close()
        assert bus.count("dashboard") == 0
        bus.publish({"type": "y"})
        assert (await b.get())["type"] == "y" and a.queue.empty()

    run(go())


def test_replay_gives_recent_history_first():
    async def go():
        bus = EventBus(history=3)
        for i in range(5):
            bus.publish({"type": "e", "i": i})
        assert [e["i"] for e in bus.history()] == [2, 3, 4]
        async with bus.subscribe(replay=2) as sub:
            assert [(await sub.get())["i"] for _ in range(2)] == [3, 4]
            bus.publish({"type": "e", "i": 5})
            assert (await sub.get())["i"] == 5
        assert bus.count("dashboard") == 0  # left the context: unsubscribed

    run(go())


def test_slow_subscriber_loses_oldest_events_without_blocking_publisher():
    async def go():
        bus = EventBus()
        sub = bus.subscribe(maxsize=3)
        for i in range(10):
            bus.publish({"type": "e", "i": i})
        assert sub.dropped == 7 and [(await sub.get())["i"] for _ in range(3)] == [7, 8, 9]

    run(go())
