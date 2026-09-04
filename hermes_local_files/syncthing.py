"""Минимальный клиент стабильного Syncthing REST API."""

import json
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class SyncthingError(RuntimeError):
    """Ошибка обращения к локальному Syncthing."""


class SyncthingClient:
    """Аутентифицированный клиент loopback REST API Syncthing."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        base_url = base_url.rstrip("/")
        if not base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("Syncthing API must be loopback-only")
        if not api_key:
            raise ValueError("Syncthing API key is required")
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    @staticmethod
    def _id(value: str) -> str:
        if not value or not _ID_RE.fullmatch(value):
            raise ValueError("Unsafe Syncthing identifier")
        return value

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        data = None
        headers = {"Accept": "application/json", "X-API-Key": self.api_key}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except Exception as exc:
            raise SyncthingError(f"Syncthing request failed: {method} {path}: {exc}") from exc
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise SyncthingError(f"Syncthing returned invalid JSON for {path}") from exc
        return parsed if isinstance(parsed, dict) else {"value": parsed}

    def device_id(self) -> str:
        """Вернуть device ID локального экземпляра."""

        value = self._request("GET", "/rest/system/status").get("myID")
        if not isinstance(value, str) or not value:
            raise SyncthingError("Syncthing status has no device id")
        return value

    def configure_private_transport(self, listen_address: str) -> None:
        """Отключить discovery/NAT/relay и оставить loopback listener."""

        if not listen_address.startswith("tcp://127.0.0.1:"):
            raise ValueError("Listen address must use loopback")
        self._request(
            "PATCH",
            "/rest/config/options",
            {
                "listenAddresses": [listen_address],
                "globalAnnounceEnabled": False,
                "localAnnounceEnabled": False,
                "relaysEnabled": False,
                "natEnabled": False,
                "urAccepted": -1,
            },
        )

    def put_device(self, *, device_id: str, name: str, addresses: Iterable[str]) -> None:
        """Идемпотентно добавить или обновить удалённое устройство."""

        device_id = self._id(device_id)
        clean_addresses = list(addresses)
        if not clean_addresses:
            raise ValueError("At least one device address is required")
        config = self._request("GET", "/rest/config/defaults/device")
        config.update(
            {
                "deviceID": device_id,
                "name": name,
                "addresses": clean_addresses,
                "compression": "metadata",
                "introducer": False,
                "skipIntroductionRemovals": False,
                "paused": False,
                "autoAcceptFolders": False,
                "untrusted": False,
            }
        )
        self._request("PUT", f"/rest/config/devices/{device_id}", config)

    def put_folder(self, config: Dict[str, Any]) -> None:
        """Идемпотентно добавить или обновить папку."""

        folder_id = self._id(str(config.get("id") or ""))
        merged = self._request("GET", "/rest/config/defaults/folder")
        merged.update(config)
        self._request("PUT", f"/rest/config/folders/{folder_id}", merged)

    def set_ignores(self, folder_id: str, patterns: List[str]) -> None:
        """Задать ignore patterns общей папки."""

        folder_id = self._id(folder_id)
        query = urlencode({"folder": folder_id})
        self._request("POST", f"/rest/db/ignores?{query}", {"ignore": patterns})

    def rescan(self, folder_id: str) -> None:
        """Запросить немедленное сканирование общей папки."""

        folder_id = self._id(folder_id)
        query = urlencode({"folder": folder_id})
        self._request("POST", f"/rest/db/scan?{query}", {})

    def folder_status(self, folder_id: str, remote_device_id: str) -> Dict[str, Any]:
        """Получить минимальный набор показателей для UI."""

        folder_id = self._id(folder_id)
        remote_device_id = self._id(remote_device_id)
        folder_query = urlencode({"folder": folder_id})
        remote_query = urlencode({"device": remote_device_id, "folder": folder_id})
        status = self._request("GET", f"/rest/db/status?{folder_query}")
        local_completion = self._request("GET", f"/rest/db/completion?{folder_query}")
        remote_completion = self._request("GET", f"/rest/db/completion?{remote_query}")
        folder_errors = self._request("GET", f"/rest/folder/errors?{folder_query}")
        connections = self._request("GET", "/rest/system/connections")
        remote = (connections.get("connections") or {}).get(remote_device_id) or {}
        errors = folder_errors.get("errors")
        return {
            "state": str(status.get("state") or "unknown"),
            "pull_errors": int(status.get("pullErrors") or 0),
            "folder_errors": len(errors) if isinstance(errors, list) else 0,
            "local_completion": float(local_completion.get("completion") or 0.0),
            "remote_completion": float(remote_completion.get("completion") or 0.0),
            "remote_state": str(remote_completion.get("remoteState") or "unknown"),
            "connected": bool(remote.get("connected")),
        }
