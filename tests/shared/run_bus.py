"""Run-bus: tail the unified events.jsonl and broadcast over WebSocket.

Started by ``benchmark_harness.main()`` before the per-model loop and torn
down after the run completes. Browser clients (the viewer's LiveGantt
and AuditViewer) subscribe to ``ws://127.0.0.1:<port>/stream`` to receive
every event the harness/exgraph/langgraph/dagster writer emits, in real
time. New connections receive the file's existing contents first
(replay), then the live tail.

Discovery: the harness writes ``<run_dir>/.bus-port`` so the SPA can
read the port without a hardcoded constant.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RunBus:
    """Background thread running uvicorn with a tail-and-broadcast app."""

    def __init__(self, events_path: Path, port: int | None = None) -> None:
        self.events_path = events_path
        self.port = port or _free_port()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Any = None
        self._stopping = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="run-bus", daemon=True)
        self._thread.start()
        # Best-effort discovery file for the SPA. We write the port even
        # before the server is fully bound — uvicorn binds within a few
        # ms and clients retry.
        port_file = self.events_path.parent / ".bus-port"
        port_file.write_text(str(self.port))

    def stop(self) -> None:
        self._stopping.set()
        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.shutdown)
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            import uvicorn
            from fastapi import FastAPI, WebSocket, WebSocketDisconnect
            from fastapi.middleware.cors import CORSMiddleware
        except ImportError:
            logger.warning(
                "run_bus: fastapi/uvicorn not installed; live event stream disabled. "
                "Run `uv pip install fastapi uvicorn` to enable."
            )
            return

        app = FastAPI()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        clients: set[WebSocket] = set()
        clients_lock = asyncio.Lock()

        async def _broadcast(line: str) -> None:
            async with clients_lock:
                dead: list[WebSocket] = []
                for ws in clients:
                    try:
                        await ws.send_text(line)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    clients.discard(ws)

        async def _tail_loop() -> None:
            """Poll the JSONL for new lines and broadcast each one."""
            position = 0
            while not self._stopping.is_set():
                if self.events_path.exists():
                    size = self.events_path.stat().st_size
                    if size > position:
                        with self.events_path.open("rb") as f:
                            f.seek(position)
                            chunk = f.read(size - position)
                            position = size
                        for line in chunk.decode("utf-8", errors="replace").splitlines():
                            line = line.strip()
                            if line:
                                await _broadcast(line)
                    elif size < position:
                        # File rotated/truncated; restart from beginning.
                        position = 0
                await asyncio.sleep(0.25)

        # Routes are mounted under /viewer/bus/ so the Vite proxy can
        # forward without path rewriting (Vite doesn't apply rewrite on
        # WebSocket upgrades). Direct callers (curl) can also use the
        # un-prefixed routes for convenience.
        async def _health_impl() -> dict[str, Any]:
            return {
                "ok": True,
                "events_path": str(self.events_path),
                "exists": self.events_path.exists(),
            }

        async def _events_impl() -> list[dict[str, Any]]:
            if not self.events_path.exists():
                return []
            out = []
            with self.events_path.open() as f:
                for line in f:
                    line = line.strip()
                    if line:
                        with contextlib.suppress(json.JSONDecodeError):
                            out.append(json.loads(line))
            return out

        app.add_api_route("/health", _health_impl, methods=["GET"])
        app.add_api_route("/viewer/bus/health", _health_impl, methods=["GET"])
        app.add_api_route("/events", _events_impl, methods=["GET"])
        app.add_api_route("/viewer/bus/events", _events_impl, methods=["GET"])

        async def _stream_impl(ws: WebSocket) -> None:
            await ws.accept()
            # Replay history first so a late client still sees the full
            # timeline. After this we just join the broadcast set.
            if self.events_path.exists():
                with self.events_path.open() as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            await ws.send_text(line)
            async with clients_lock:
                clients.add(ws)
            try:
                while True:
                    # Keep-alive: clients don't send anything, just wait.
                    await ws.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                async with clients_lock:
                    clients.discard(ws)

        app.add_api_websocket_route("/stream", _stream_impl)
        app.add_api_websocket_route("/viewer/bus/stream", _stream_impl)

        @app.on_event("startup")
        async def _on_startup() -> None:
            asyncio.create_task(_tail_loop())

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._server = uvicorn.Server(config)
        try:
            self._loop.run_until_complete(self._server.serve())
        except Exception:
            logger.exception("run_bus: server crashed")
        finally:
            self._loop.close()
