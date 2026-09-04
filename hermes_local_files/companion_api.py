"""Защищённый loopback HTTP API macOS companion."""

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any, Callable, Dict
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


_MAX_BODY = 1024 * 1024


class CompanionApp:
    """Небольшой прикладной слой, независимый от HTTP-сервера."""

    def __init__(
        self,
        *,
        token: str,
        picker: Callable[[], str],
        device_id: Callable[[], str],
        provisioner: Any,
        store: Any,
    ):
        if not token:
            raise ValueError("Companion bearer token is required")
        self.token = token
        self.picker = picker
        self.device_id = device_id
        self.provisioner = provisioner
        self.store = store

    def authorized(self, header: str) -> bool:
        """Проверить local capability token с constant-time сравнением."""

        return hmac.compare_digest(header or "", self.token)

    def dispatch(self, method: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Обработать один уже аутентифицированный запрос."""

        target = urlsplit(path)
        if method == "GET" and target.path == "/v1/health":
            return {"ok": True}
        if method == "GET" and target.path == "/v1/projects":
            return {"projects": self.store.list()}
        if method == "GET" and target.path == "/v1/status":
            folder_id = (parse_qs(target.query).get("folder_id") or [""])[0]
            if not folder_id:
                raise ValueError("Folder id is required")
            return self.provisioner.status(folder_id)
        if method == "POST" and target.path == "/v1/pick-folder":
            local_path = self.picker()
            if not local_path:
                raise ValueError("Folder selection was cancelled")
            return {
                "mapping_id": uuid4().hex,
                "local_path": local_path,
                "suggested_name": Path(local_path).name,
                "local_device_id": self.device_id(),
            }
        if method == "POST" and target.path == "/v1/provision-local":
            local_path = str(payload.get("local_path") or "")
            server = payload.get("server")
            if not local_path or not isinstance(server, dict):
                raise ValueError("Local path and server contract are required")
            return self.provisioner.provision(local_path, server)
        raise KeyError("Unknown companion endpoint")


def _handler(app: CompanionApp):
    class Handler(BaseHTTPRequestHandler):
        def _headers(self, status: int, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(length))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Headers",
                "X-Hermes-Local-Files-Token, Content-Type",
            )
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _write(self, status: int, payload: Dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._headers(status, len(raw))
            self.wfile.write(raw)

        def _serve(self) -> None:
            if self.client_address[0] not in {"127.0.0.1", "::1"}:
                self._write(403, {"error": "Loopback clients only"})
                return
            if not app.authorized(self.headers.get("X-Hermes-Local-Files-Token", "")):
                self._write(401, {"error": "Unauthorized"})
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length < 0 or length > _MAX_BODY:
                self._write(413, {"error": "Request body is too large"})
                return
            try:
                raw = self.rfile.read(length) if length else b""
                payload = json.loads(raw) if raw else {}
                if not isinstance(payload, dict):
                    raise ValueError("JSON body must be an object")
                result = app.dispatch(self.command, self.path, payload)
            except KeyError:
                self._write(404, {"error": "Not found"})
            except ValueError as exc:
                self._write(400, {"error": str(exc)})
            except Exception:
                self._write(500, {"error": "Companion operation failed"})
            else:
                self._write(200, result)

        def do_GET(self) -> None:
            self._serve()

        def do_POST(self) -> None:
            self._serve()

        def do_OPTIONS(self) -> None:
            self._headers(204, 0)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def make_server(host: str, port: int, app: CompanionApp) -> ThreadingHTTPServer:
    """Создать companion server только на loopback интерфейсе."""

    if host not in {"127.0.0.1", "::1"}:
        raise ValueError("Companion server must bind to loopback")
    return ThreadingHTTPServer((host, port), _handler(app))
