import asyncio

from telemetry_gateway.models import DeviceState
from telemetry_gateway.realtime import RealtimeHub


def make_state(**overrides) -> DeviceState:
    values = dict(
        device_id="device-01",
        boot_id="boot-a",
        generation=1,
        sequence=1,
        device_time="2026-08-12T09:00:00+00:00",
        received_at="2026-08-12T09:00:01+00:00",
        metric="temperature",
        value=21.4,
    )
    values.update(overrides)
    return DeviceState(**values)


class FakeWebSocket:
    def __init__(self, block: bool = False) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self._block = block
        self._release = asyncio.Event()

    async def accept(self) -> None:
        pass

    async def send_json(self, message: dict) -> None:
        if self._block:
            await self._release.wait()
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True

    def release(self) -> None:
        self._release.set()


async def _settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


def test_publish_does_not_block_on_a_slow_client() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        slow = FakeWebSocket(block=True)
        fast = FakeWebSocket()
        await hub.connect(slow)
        await hub.connect(fast)

        state = make_state()
        await asyncio.wait_for(hub.publish(state), timeout=1.0)
        await _settle()

        assert fast.sent == [{"type": "device.state.changed", "data": state.to_api()}]
        assert slow.sent == []

    asyncio.run(scenario())


def test_coalesces_multiple_updates_for_the_same_key_before_delivery() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        client = FakeWebSocket()
        await hub.connect(client)

        first = make_state(value=1.0)
        second = make_state(value=2.0)
        await hub.publish(first)
        await hub.publish(second)
        await _settle()

        assert client.sent == [{"type": "device.state.changed", "data": second.to_api()}]

    asyncio.run(scenario())


def test_overflow_disconnects_only_the_offending_client() -> None:
    async def scenario() -> None:
        hub = RealtimeHub(max_pending=1)
        victim = FakeWebSocket(block=True)
        healthy = FakeWebSocket()
        await hub.connect(victim)
        await hub.connect(healthy)

        await hub.publish(make_state(metric="temperature", value=1.0))
        await _settle()
        # Both clients drained "temperature". victim's writer is now stuck
        # inside send_json (block=True); healthy's writer is free again.

        await hub.publish(make_state(metric="humidity", value=2.0))
        await _settle()
        # healthy drains+sends "humidity"; victim's writer is still stuck on
        # the first send, so "humidity" sits unread in victim's buffer.

        await hub.publish(make_state(metric="pressure", value=3.0))
        # victim's buffer already holds one pending key (humidity) and this
        # is a second, distinct key -> exceeds max_pending=1 -> overflow.

        assert hub.size == 1
        assert victim.closed is True
        assert healthy.closed is False

    asyncio.run(scenario())


def test_writer_task_is_cleaned_up_on_disconnect() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        client = FakeWebSocket()
        await hub.connect(client)
        task = hub._connections[client].task

        hub.disconnect(client)
        await _settle()

        assert hub.size == 0
        assert task.done()

    asyncio.run(scenario())


def test_healthy_client_still_receives_state_changes() -> None:
    async def scenario() -> None:
        hub = RealtimeHub()
        client = FakeWebSocket()
        await hub.connect(client)

        state = make_state()
        await hub.publish(state)
        await _settle()

        assert client.sent == [{"type": "device.state.changed", "data": state.to_api()}]

    asyncio.run(scenario())
