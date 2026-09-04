import json
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hermes_local_files.companion_api import CompanionApp, make_server


class FakeProvisioner:
    def __init__(self):
        self.calls = []

    def provision(self, local_path, server):
        self.calls.append((local_path, server))
        return {**server, "local_path": local_path, "local_device_id": "MAC-ID"}

    def status(self, folder_id):
        return {"folder_id": folder_id, "state": "synced", "conflicts": []}


class FakeStore:
    def list(self):
        return [{"mapping_id": "existing", "name": "Existing"}]


class CompanionApiTests(unittest.TestCase):
    def setUp(self):
        self.provisioner = FakeProvisioner()
        self.app = CompanionApp(
            token="test-token",
            picker=lambda: "/Users/example/Documents/Family",
            device_id=lambda: "MAC-ID",
            provisioner=self.provisioner,
            store=FakeStore(),
        )
        self.server = make_server("127.0.0.1", 0, self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, token="test-token"):
        data = None if body is None else json.dumps(body).encode()
        headers = {"X-Hermes-Local-Files-Token": token} if token else {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(self.base + path, data=data, headers=headers, method=method)
        with urlopen(req, timeout=2) as response:
            return response.status, json.loads(response.read())

    def test_unauthorized_requests_are_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("GET", "/v1/projects", token="")

        self.assertEqual(caught.exception.code, 401)

    def test_health_is_authenticated_and_minimal(self):
        status, body = self.request("GET", "/v1/health")

        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})

    def test_pick_folder_returns_opaque_mapping_and_device(self):
        status, body = self.request("POST", "/v1/pick-folder", {})

        self.assertEqual(status, 200)
        self.assertEqual(body["local_path"], "/Users/example/Documents/Family")
        self.assertEqual(body["suggested_name"], "Family")
        self.assertEqual(body["local_device_id"], "MAC-ID")
        self.assertRegex(body["mapping_id"], r"^[a-f0-9]{32}$")

    def test_provision_local_calls_only_with_server_contract(self):
        payload = {
            "local_path": "/Users/example/Documents/Family",
            "server": {
                "mapping_id": "map-1",
                "name": "Family",
                "folder_id": "hermes-abc",
                "server_path": "/srv/projects/family",
                "server_device_id": "SERVER-ID",
            },
        }

        status, body = self.request("POST", "/v1/provision-local", payload)

        self.assertEqual(status, 200)
        self.assertEqual(body["local_device_id"], "MAC-ID")
        self.assertEqual(self.provisioner.calls, [(payload["local_path"], payload["server"])])

    def test_projects_returns_non_secret_mapping_list(self):
        status, body = self.request("GET", "/v1/projects")

        self.assertEqual(status, 200)
        self.assertEqual(body, {"projects": [{"mapping_id": "existing", "name": "Existing"}]})

    def test_status_is_read_from_local_sync_engine(self):
        status, body = self.request("GET", "/v1/status?folder_id=hermes-folder")

        self.assertEqual(status, 200)
        self.assertEqual(
            body,
            {"folder_id": "hermes-folder", "state": "synced", "conflicts": []},
        )


if __name__ == "__main__":
    unittest.main()
