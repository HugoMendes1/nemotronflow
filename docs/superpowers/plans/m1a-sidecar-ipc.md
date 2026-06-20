# M1a — Python sidecar: WebSocket server + JSON-RPC + READY line

**Goal:** The backend boots as a sidecar, binds a localhost WebSocket, prints the READY line, and answers JSON-RPC requests. No audio, no engine yet — just a talking skeleton that the Tauri shell (M2) can launch and talk to.

**Files to create:**
- `backend/nemotronflow/ipc/__init__.py`
- `backend/nemotronflow/ipc/protocol.py` — JSON-RPC 2.0 envelope, method catalogue, typed payloads.
- `backend/nemotronflow/ipc/transport.py` — WebSocket server, framing, reconnect.
- `backend/nemotronflow/__main__.py` — entrypoint: pick free port, start server, print READY line.
- `backend/nemotronflow/server.py` — wires transport + protocol + a request dispatcher.
- `frontend/src/rpc/types.ts` — TypeScript mirror of the method catalogue (for parity test).
- `tests/test_ipc_protocol.py`
- `tests/test_ipc_transport.py`
- `tests/test_server.py`
- `tests/test_protocol_parity.py`

**Dependencies:** M0 (logging, pyproject with websockets).

**Acceptance criteria:**
- `python -m nemotronflow` binds `127.0.0.1:<auto>`, prints `NEMOTRONFLOW_READY <port>` to stdout, then serves JSON-RPC over WebSocket.
- A test client can: connect, send `settings/get` (returns defaults), send an unknown method (returns a `-32601 method not found` error), and disconnect cleanly.
- The READY line is the *only* stdout output before the first request (so Tauri can parse it reliably).
- `tests/test_protocol_parity.py` passes: method names in `protocol.py` match those in `types.ts`.

**Test strategy:** Unit tests for the envelope/serialization (protocol.py), integration test for the WS server with a real `websockets` client in-process (`pytest-asyncio`), parity test for the TS mirror.

**Estimated complexity:** Medium. JSON-RPC framing is fiddly; the WebSocket server is straightforward.

**Commit boundaries:** one commit per task (`feat(ipc): …`, `feat(server): …`, `test(ipc): parity`), tag `v0.1.0-m1a`.

---

## Task M1a.1: JSON-RPC protocol module (TDD)

**Files:** `backend/nemotronflow/ipc/__init__.py` (empty), `backend/nemotronflow/ipc/protocol.py`, `tests/test_ipc_protocol.py`.

- [ ] **Step 1 (write failing test):** `tests/test_ipc_protocol.py`:

```python
from __future__ import annotations

import pytest

from nemotronflow.ipc.protocol import (
    JSONRPCError,
    Notification,
    Request,
    Response,
    make_error_response,
    make_response,
    parse_message,
)


def test_parse_request() -> None:
    raw = {"jsonrpc": "2.0", "id": 1, "method": "settings/get", "params": {}}
    msg = parse_message(raw)
    assert isinstance(msg, Request)
    assert msg.id == 1
    assert msg.method == "settings/get"
    assert msg.params == {}


def test_parse_notification_has_no_id() -> None:
    raw = {"jsonrpc": "2.0", "method": "state/changed", "params": {"state": "idle"}}
    msg = parse_message(raw)
    assert isinstance(msg, Notification)
    assert msg.method == "state/changed"


def test_request_requires_id_and_method() -> None:
    with pytest.raises(JSONRPCError) as exc:
        parse_message({"jsonrpc": "2.0", "method": "x"})
    assert exc.value.code == -32602  # invalid params


def test_make_response_carries_id_and_result() -> None:
    resp = make_response(request_id=7, result={"ok": True})
    assert resp == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}


def test_make_error_response_carries_code_and_message() -> None:
    resp = make_error_response(request_id=7, code=-32601, message="method not found")
    assert resp == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {"code": -32601, "message": "method not found"},
    }


def test_notification_serializes_without_id() -> None:
    n = Notification(method="state/changed", params={"state": "listening"})
    assert n.to_dict() == {
        "jsonrpc": "2.0",
        "method": "state/changed",
        "params": {"state": "listening"},
    }
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_ipc_protocol.py -v` → `ModuleNotFoundError`.

- [ ] **Step 3 (implement):** `backend/nemotronflow/ipc/protocol.py`:

```python
"""JSON-RPC 2.0 message envelope and the NemotronFlow method catalogue."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

JSONRPC_VERSION = "2.0"

# Canonical method names. The parity test asserts this set equals the set in
# frontend/src/rpc/types.ts. Add a method here AND there when extending.
REQUEST_METHODS: frozenset[str] = frozenset(
    {
        "settings/get",
        "settings/set",
        "mics/list",
        "mics/test",
        "history/list",
        "history/add",
        "history/delete",
        "history/edit",
        "history/favorite",
        "history/clear",
        "history/export",
        "engine/set",
        "engine/reload",
        "shutdown",
    }
)
NOTIFICATION_METHODS: frozenset[str] = frozenset(
    {
        "state/changed",
        "transcription/result",
        "transcription/partial",
        "vad/start",
        "vad/end",
        "model/status",
        "model/progress",
        "download/progress",
        "log",
        "error",
    }
)


class JSONRPCError(Exception):
    """Raised for protocol-level errors; carries the JSON-RPC error code."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass
class Request:
    id: int | str
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC_VERSION, "id": self.id, "method": self.method, "params": self.params}


@dataclass
class Notification:
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC_VERSION, "method": self.method, "params": self.params}


class RawMessage(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


def parse_message(raw: dict[str, Any]) -> Request | Notification:
    if raw.get("jsonrpc") != JSONRPC_VERSION:
        raise JSONRPCError(-32600, "invalid request: jsonrpc must be '2.0'")
    method = raw.get("method")
    if not isinstance(method, str):
        raise JSONRPCError(-32600, "invalid request: missing method")
    params = raw.get("params", {})
    if not isinstance(params, dict):
        raise JSONRPCError(-32602, "invalid params: must be an object")
    if "id" in raw:
        rid = raw["id"]
        if not isinstance(rid, (int, str)):
            raise JSONRPCError(-32602, "invalid params: id must be int or str")
        return Request(id=rid, method=method, params=params)
    return Notification(method=method, params=params)


def make_response(request_id: int | str, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def make_error_response(
    request_id: int | str | None, code: int, message: str, data: Any = None
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": err}
```

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_ipc_protocol.py -v` → PASS.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/ipc/__init__.py backend/nemotronflow/ipc/protocol.py tests/test_ipc_protocol.py
git commit -m "feat(ipc): add JSON-RPC 2.0 protocol envelope and method catalogue"
```

---

## Task M1a.2: Request dispatcher + server skeleton (TDD)

**Files:** `backend/nemotronflow/server.py`, `tests/test_server.py`.

The server has a dispatcher table mapping method names → async handler functions. M1a only implements `settings/get` (returns defaults from M4's settings; for now a stub dict) and `shutdown`. Real handlers fill in across M1b/M4/M5.

- [ ] **Step 1 (write failing test):** `tests/test_server.py`:

```python
from __future__ import annotations

import pytest

from nemotronflow.server import Dispatcher


@pytest.mark.asyncio
async def test_dispatcher_routes_known_method() -> None:
    d = Dispatcher()
    result = await d.handle_request("settings/get", {})
    assert isinstance(result, dict)
    assert "hotkey" in result  # default settings present


@pytest.mark.asyncio
async def test_dispatcher_unknown_method_raises() -> None:
    from nemotronflow.ipc.protocol import JSONRPCError

    d = Dispatcher()
    with pytest.raises(JSONRPCError) as exc:
        await d.handle_request("does/not/exist", {})
    assert exc.value.code == -32601
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_server.py -v` → `ModuleNotFoundError`.

- [ ] **Step 3 (implement):** `backend/nemotronflow/server.py` (M1a subset; `Settings` comes in M4 but the dispatcher references a defaults dict now):

```python
"""WebSocket JSON-RPC server: lifecycle, READY line, request dispatch."""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Awaitable, Callable

from nemotronflow.ipc.protocol import (
    JSONRPCError,
    Notification,
    Request,
    make_error_response,
    make_response,
    parse_message,
)
from nemotronflow.logging import get_logger

log = get_logger("server")

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


def _default_settings() -> dict[str, Any]:
    """Placeholder defaults; replaced by Settings in M4. Keys must match
    the Settings dataclass fields so the M4 migration is a drop-in."""
    return {
        "hotkey": "ctrl+space",
        "engine": "nemotron",
        "auto_paste_enabled": True,
        "clipboard_restore_mode": "delayed",
        "history_enabled": True,
        "theme": "dark",
    }


class Dispatcher:
    """Routes JSON-RPC method names to async handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._register_defaults()

    def register(self, method: str, handler: Handler) -> None:
        self._handlers[method] = handler

    async def handle_request(self, method: str, params: dict[str, Any]) -> Any:
        handler = self._handlers.get(method)
        if handler is None:
            raise JSONRPCError(-32601, f"method not found: {method}")
        return await handler(params)

    def _register_defaults(self) -> None:
        async def _settings_get(_params: dict[str, Any]) -> dict[str, Any]:
            return _default_settings()

        async def _shutdown(_params: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        self.register("settings/get", _settings_get)
        self.register("shutdown", _shutdown)


async def serve(port: int = 0, ready_event: asyncio.Event | None = None) -> None:
    """Bind the WebSocket server and run until cancelled. If port==0, the OS
    picks a free port and it is printed as 'NEMOTRONFLOW_READY <port>' to stdout."""
    import websockets
    from websockets.asyncio.server import serve as ws_serve

    dispatcher = Dispatcher()

    async def _connection(ws: Any) -> None:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send(json.dumps(make_error_response(None, -32700, "parse error")))
                continue
            try:
                msg = parse_message(data)
            except JSONRPCError as e:
                await ws.send(json.dumps(make_error_response(None, e.code, str(e))))
                continue
            if isinstance(msg, Notification):
                log.debug("notification_received", method=msg.method, params=msg.params)
                continue
            await _handle_request(ws, dispatcher, msg)

    async with ws_serve(_connection, "127.0.0.1", port) as srv:
        sockets = srv.sockets
        bound_port = sockets[0].getsockname()[1] if sockets else port
        sys.stdout.write(f"NEMOTRONFLOW_READY {bound_port}\n")
        sys.stdout.flush()
        log.info("server_ready", port=bound_port)
        if ready_event is not None:
            ready_event.set()
        await asyncio.Future()  # run forever


async def _handle_request(ws: Any, dispatcher: Dispatcher, req: Request) -> None:
    try:
        result = await dispatcher.handle_request(req.method, req.params)
        await ws.send(json.dumps(make_response(req.id, result)))
    except JSONRPCError as e:
        await ws.send(json.dumps(make_error_response(req.id, e.code, str(e), e.data)))
    except Exception as e:  # noqa: BLE001 — boundary; report to client
        log.exception("handler_error", method=req.method)
        await ws.send(json.dumps(make_error_response(req.id, -32603, f"internal error: {e}")))
```

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_server.py -v` → PASS.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/server.py tests/test_server.py
git commit -m "feat(server): add JSON-RPC dispatcher and WebSocket server skeleton"
```

---

## Task M1a.3: WebSocket transport integration test (TDD)

**Files:** `tests/test_ipc_transport.py`.

Spin up the real WS server in-process and drive it with a `websockets` client. Proves the READY-line handoff and a full request/response round-trip over a socket.

- [ ] **Step 1 (write failing test):** `tests/test_ipc_transport.py`:

```python
from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from nemotronflow.server import serve


async def _read_ready_line(stream: asyncio.StreamReader, proc: asyncio.subprocess.Process) -> int:
    """In-process we read the port from the ready_event; for the subprocess
    variant (M2), Tauri reads stdout. Here we just await the event."""
    raise NotImplementedError  # replaced below


@pytest.mark.asyncio
async def test_round_trip_over_websocket() -> None:
    ready = asyncio.Event()
    server_task = asyncio.create_task(serve(port=0, ready_event=ready))
    await asyncio.wait_for(ready.wait(), timeout=5.0)
    # Recover the bound port from the server task's logs by re-resolving:
    # simpler: have serve expose the port via the event. We set it on the event
    # via a wrapper. For this test we read it from a shared mutable.
    # (See implementation note: serve sets port on the event's attribute.)
    port = getattr(ready, "port")  # type: ignore[attr-defined]

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "settings/get"}))
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            resp = json.loads(raw)
            assert resp["id"] == 1
            assert "result" in resp
            assert resp["result"]["hotkey"] == "ctrl+space"

            # Unknown method returns -32601.
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "no/such"}))
            raw = await ws.recv()
            resp = json.loads(raw)
            assert resp["id"] == 2
            assert resp["error"]["code"] == -32601
    finally:
        server_task.cancel()
        with pytest.raises((asyncio.CancelledError, BaseException)):
            await server_task
```

- [ ] **Step 2 (run, confirm fail):** `pytest tests/test_ipc_transport.py -v` → fails (the test expects `ready.port` which `serve` doesn't set).

- [ ] **Step 3 (fix implementation):** Edit `serve()` in `server.py` to set the bound port on the ready event before `set()`:

```python
        if ready_event is not None:
            setattr(ready_event, "port", bound_port)
            ready_event.set()
```

(Place this right before the existing `if ready_event is not None: ready_event.set()` line; remove the old line to avoid duplication.)

- [ ] **Step 4 (run, confirm pass):** `pytest tests/test_ipc_transport.py -v` → PASS.

- [ ] **Step 5:** Commit:
```bash
git add backend/nemotronflow/server.py tests/test_ipc_transport.py
git commit -m "feat(ipc): wire ready-line port handoff and add WS round-trip test"
```

---

## Task M1a.4: `__main__.py` entrypoint

**Files:** `backend/nemotronflow/__main__.py`.

- [ ] **Step 1:** Write `backend/nemotronflow/__main__.py`:

```python
"""Entrypoint: `python -m nemotronflow` boots the sidecar."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from nemotronflow.logging import configure, get_logger
from nemotronflow.server import serve


def _data_dir() -> Path:
    env = os.environ.get("NEMOTRONFLOW_DATA_DIR")
    base = Path(env) if env else Path.home() / ".nemotronflow"
    base.mkdir(parents=True, exist_ok=True)
    return base


def main() -> None:
    configure(level=os.environ.get("NEMOTRONFLOW_LOG_LEVEL", "INFO"), log_dir=_data_dir() / "logs")
    log = get_logger("main")
    log.info("starting_sidecar")
    port = int(os.environ.get("NEMOTRONFLOW_PORT", "0"))
    try:
        asyncio.run(serve(port=port))
    except KeyboardInterrupt:
        log.info("shutdown_via_keyboard_interrupt")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2 (smoke test):** Run `python -m nemotronflow` in one terminal, confirm it prints `NEMOTRONFLOW_READY <port>` and stays running. Ctrl+C exits cleanly. (This is a manual smoke check; no automated assertion — the transport test already covers the server path.)

- [ ] **Step 3:** Commit:
```bash
git add backend/nemotronflow/__main__.py
git commit -m "feat(main): add python -m nemotronflow entrypoint"
```

---

## Task M1a.5: Frontend RPC types + protocol parity test (TDD)

**Files:** `frontend/src/rpc/types.ts`, `tests/test_protocol_parity.py`.

- [ ] **Step 1:** Write `frontend/src/rpc/types.ts`:

```ts
// Method catalogue mirrored from backend/nemotronflow/ipc/protocol.py.
// The parity test (tests/test_protocol_parity.py) asserts these two sets stay equal.

export const REQUEST_METHODS = [
  "settings/get",
  "settings/set",
  "mics/list",
  "mics/test",
  "history/list",
  "history/add",
  "history/delete",
  "history/edit",
  "history/favorite",
  "history/clear",
  "history/export",
  "engine/set",
  "engine/reload",
  "shutdown",
] as const;

export const NOTIFICATION_METHODS = [
  "state/changed",
  "transcription/result",
  "transcription/partial",
  "vad/start",
  "vad/end",
  "model/status",
  "model/progress",
  "download/progress",
  "log",
  "error",
] as const;

export type RequestMethod = (typeof REQUEST_METHODS)[number];
export type NotificationMethod = (typeof NOTIFICATION_METHODS)[number];

export type OverlayState = "idle" | "listening" | "processing" | "done" | "error";
export type ClipboardRestoreMode = "immediate" | "delayed" | "disabled";

export interface StateChangedPayload {
  state: OverlayState;
  started_at?: number;
  utterance_id?: string;
}

export interface TranscriptionResultPayload {
  utterance_id: string;
  text: string;
  engine: string;
  audio_ms: number;
  total_ms: number;
  auto_paste: boolean;
  timestamp: number;
}

export interface ModelStatusPayload {
  engine: string;
  phase: "loading" | "ready" | "error";
  detail?: string;
}
```

- [ ] **Step 2 (write failing test):** `tests/test_protocol_parity.py`:

```python
from __future__ import annotations

import re
from pathlib import Path

from nemotronflow.ipc.protocol import NOTIFICATION_METHODS, REQUEST_METHODS

FRONTEND_TYPES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "rpc" / "types.ts"


def _extract_methods(ts_source: str, const_name: str) -> set[str]:
    match = re.search(rf"export const {const_name} = \[(.*?)\] as const;", ts_source, re.DOTALL)
    assert match is not None, f"{const_name} not found in types.ts"
    return {m.strip().strip('"') for m in match.group(1).split(",") if m.strip()}


def test_request_methods_match_frontend() -> None:
    ts = FRONTEND_TYPES.read_text(encoding="utf-8")
    assert set(REQUEST_METHODS) == _extract_methods(ts, "REQUEST_METHODS")


def test_notification_methods_match_frontend() -> None:
    ts = FRONTEND_TYPES.read_text(encoding="utf-8")
    assert set(NOTIFICATION_METHODS) == _extract_methods(ts, "NOTIFICATION_METHODS")
```

- [ ] **Step 3 (run, confirm pass — types.ts already written to match):**
```bash
pytest tests/test_protocol_parity.py -v
```
Expected: PASS.

- [ ] **Step 4:** Commit + tag:
```bash
git add frontend/src/rpc/types.ts tests/test_protocol_parity.py
git commit -m "feat(rpc): add frontend method catalogue and protocol parity test"
git tag v0.1.0-m1a
```

**M1a commit boundary:** `v0.1.0-m1a` — `python -m nemotronflow` boots, prints READY, answers JSON-RPC over WebSocket. The frontend and backend agree on the method catalogue. Nothing else works yet (no audio, no engine), but the sidecar is real and talkable.
