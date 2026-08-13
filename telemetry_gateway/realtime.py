from __future__ import annotations

import asyncio
from typing import Any, Protocol

from fastapi import WebSocket

from telemetry_gateway.models import DeviceState

DEFAULT_MAX_PENDING = 256


class StatePublisher(Protocol):
    async def publish(self, state: DeviceState) -> None: ...


class _ClientBuffer:
    """Bounded, coalescing outbound buffer for one WebSocket client.

    Holds at most one pending message per (deviceId, metric) key, so a
    slow client accumulates superseded state rather than every message.
    """

    def __init__(self, max_pending: int) -> None:
        self._max_pending = max_pending
        self._pending: dict[tuple[str, str], dict[str, Any]] = {}
        self._closed = False
        self.event = asyncio.Event()

    def offer(self, key: tuple[str, str], message: dict[str, Any]) -> bool:
        if self._closed:
            return True
        if key not in self._pending and len(self._pending) >= self._max_pending:
            return False
        self._pending[key] = message
        self.event.set()
        return True

    def drain(self) -> list[dict[str, Any]]:
        items = list(self._pending.values())
        self._pending.clear()
        return items

    def close(self) -> None:
        self._closed = True
        self.event.set()

    @property
    def closed(self) -> bool:
        return self._closed


class _Connection:
    def __init__(self, websocket: WebSocket, max_pending: int) -> None:
        self.websocket = websocket
        self.buffer = _ClientBuffer(max_pending)
        self.task: asyncio.Task[None] | None = None


class RealtimeHub:
    def __init__(self, max_pending: int = DEFAULT_MAX_PENDING) -> None:
        self._max_pending = max_pending
        self._connections: dict[WebSocket, _Connection] = {}

    async def connect(self, client: WebSocket) -> None:
        await client.accept()
        connection = _Connection(client, self._max_pending)
        connection.task = asyncio.create_task(self._run_writer(connection))
        self._connections[client] = connection

    def disconnect(self, client: WebSocket) -> None:
        connection = self._connections.get(client)
        if connection is not None:
            self._remove(connection)

    async def publish(self, state: DeviceState) -> None:
        message = {"type": "device.state.changed", "data": state.to_api()}
        key = (state.device_id, state.metric)
        overflowed = [
            connection
            for connection in tuple(self._connections.values())
            if not connection.buffer.offer(key, message)
        ]
        for connection in overflowed:
            await self._disconnect_and_close(connection)

    @property
    def size(self) -> int:
        return len(self._connections)

    async def _run_writer(self, connection: _Connection) -> None:
        try:
            while True:
                await connection.buffer.event.wait()
                connection.buffer.event.clear()
                pending = connection.buffer.drain()
                if not pending:
                    if connection.buffer.closed:
                        return
                    continue
                for message in pending:
                    await connection.websocket.send_json(message)
        except Exception:
            pass
        finally:
            self._remove(connection)

    def _remove(self, connection: _Connection) -> None:
        if self._connections.get(connection.websocket) is not connection:
            return
        del self._connections[connection.websocket]
        connection.buffer.close()
        if connection.task is not None and connection.task is not asyncio.current_task():
            connection.task.cancel()

    async def _disconnect_and_close(self, connection: _Connection) -> None:
        self._remove(connection)
        try:
            await connection.websocket.close()
        except Exception:
            pass
