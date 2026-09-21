import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import ValidationError

from minglecraft.models import Observation


class BridgeApplication:
    """One active match and one inference in flight; reject conflicting clients."""

    def __init__(self, loop_factory, map_name: str):
        self.loop_factory, self.map_name = loop_factory, map_name
        self.loop = None
        self.latest = None
        self.lock = threading.Lock()

    def handle(self, path: str, payload: dict) -> dict:
        obs = Observation.model_validate(payload)
        if obs.map_name != self.map_name:
            raise ValueError(f"Expected map {self.map_name}")
        if not self.lock.acquire(blocking=False):
            raise ValueError("A decision is already in flight")
        try:
            if path == "/step":
                if obs.ended:
                    raise ValueError("Ended observations must use /end")
                if self.loop is None or self.loop.finished:
                    self.loop = self.loop_factory()
                result = asyncio.run(self.loop.step(obs)).model_dump()
                self.latest = obs
                return result
            if path == "/end":
                if not obs.ended:
                    raise ValueError("Final observations must set ended=true")
                if self.loop is None:
                    raise ValueError("No active match")
                result = self.loop.finish(obs)
                self.latest = obs
                return result
            raise ValueError("Unknown endpoint")
        finally:
            self.lock.release()

    def close(self):
        with self.lock:
            if self.loop and not self.loop.finished and self.latest:
                self.loop.finish(self.latest, completed=False)


def make_server(application: BridgeApplication, port: int = 8765):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def reply(self, status: int, value: dict):
            body = json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(
                200 if self.path == "/health" else 404,
                {"protocol_version": 1, "map": application.map_name},
            )

        def do_POST(self):
            if self.path not in ("/step", "/end"):
                self.reply(404, {"error": "Unknown endpoint"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2_000_000:
                    self.reply(413, {"error": "Expected a JSON body of at most 2 MB"})
                    return
                if self.headers.get_content_type() != "application/json":
                    self.reply(415, {"error": "Content-Type must be application/json"})
                    return
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Truncated request")
                result = application.handle(self.path, json.loads(raw))
                self.reply(200, result)
            except (ValidationError, ValueError, TypeError):
                self.reply(400, {"error": "Invalid observation, match, or request ordering"})
            except FileExistsError:
                self.reply(409, {"error": "Match ID already exists; use a new match ID"})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            except Exception:
                self.reply(500, {"error": "Internal bridge error; no command issued"})

        def log_message(self, *_):
            pass

    # Deliberately loopback-only: run alongside StarCraft or use an SSH tunnel.
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
