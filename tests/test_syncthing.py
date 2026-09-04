import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hermes_local_files.syncthing import SyncthingClient


class RecordingHandler(BaseHTTPRequestHandler):
    calls = []

    def _record(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        self.__class__.calls.append(
            (self.command, self.path, self.headers.get("X-API-Key"), body)
        )

    def _reply(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._record()
        if self.path == "/rest/system/status":
            self._reply({"myID": "LOCAL-ID"})
        elif self.path == "/rest/config/defaults/device":
            self._reply({"deviceID": "", "name": "", "addresses": ["dynamic"], "untrusted": False})
        elif self.path == "/rest/config/defaults/folder":
            self._reply({"id": "", "path": "", "devices": [], "order": "random"})
        elif self.path.startswith("/rest/db/status"):
            self._reply({"state": "idle", "pullErrors": 2})
        elif self.path.startswith("/rest/db/completion"):
            if "device=" in self.path:
                self._reply({"completion": 99.0, "remoteState": "valid"})
            else:
                self._reply({"completion": 100.0})
        elif self.path.startswith("/rest/folder/errors"):
            self._reply({"errors": [{"path": "bad.txt", "error": "permission denied"}]})
        elif self.path == "/rest/system/connections":
            self._reply({"connections": {"REMOTE-ID": {"connected": True}}})
        else:
            self._reply({})

    def do_PUT(self):
        self._record()
        self._reply({})

    def do_PATCH(self):
        self._record()
        self._reply({})

    def do_POST(self):
        self._record()
        self._reply({})

    def log_message(self, *_args):
        return


class SyncthingClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.client = SyncthingClient(f"http://{host}:{port}", "test-api-key")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        RecordingHandler.calls = []

    def test_device_id_reads_authenticated_status(self):
        self.assertEqual(self.client.device_id(), "LOCAL-ID")
        self.assertEqual(
            RecordingHandler.calls,
            [("GET", "/rest/system/status", "test-api-key", None)],
        )

    def test_configure_private_transport_disables_discovery_and_relays(self):
        self.client.configure_private_transport("tcp://127.0.0.1:22001")

        self.assertEqual(RecordingHandler.calls[0][0:3], (
            "PATCH", "/rest/config/options", "test-api-key"
        ))
        body = RecordingHandler.calls[0][3]
        self.assertEqual(body["listenAddresses"], ["tcp://127.0.0.1:22001"])
        self.assertFalse(body["globalAnnounceEnabled"])
        self.assertFalse(body["localAnnounceEnabled"])
        self.assertFalse(body["relaysEnabled"])
        self.assertFalse(body["natEnabled"])

    def test_put_device_uses_explicit_tunnel_address(self):
        self.client.put_device(
            device_id="REMOTE-ID",
            name="Home server",
            addresses=["tcp://127.0.0.1:32200"],
        )

        self.assertEqual(
            [(call[0], call[1]) for call in RecordingHandler.calls],
            [
                ("GET", "/rest/config/defaults/device"),
                ("PUT", "/rest/config/devices/REMOTE-ID"),
            ],
        )
        method, path, token, body = RecordingHandler.calls[1]
        self.assertEqual((method, path, token), (
            "PUT", "/rest/config/devices/REMOTE-ID", "test-api-key"
        ))
        self.assertEqual(body["deviceID"], "REMOTE-ID")
        self.assertEqual(body["addresses"], ["tcp://127.0.0.1:32200"])
        self.assertFalse(body["autoAcceptFolders"])
        self.assertFalse(body["untrusted"])

    def test_put_folder_and_ignores_then_rescan(self):
        folder = {"id": "hermes-abc", "path": "/tmp/project", "devices": []}
        ignores = ["(?d).DS_Store", "(?d)node_modules"]

        self.client.put_folder(folder)
        self.client.set_ignores("hermes-abc", ignores)
        self.client.rescan("hermes-abc")

        self.assertEqual(
            [(call[0], call[1]) for call in RecordingHandler.calls],
            [
                ("GET", "/rest/config/defaults/folder"),
                ("PUT", "/rest/config/folders/hermes-abc"),
                ("POST", "/rest/db/ignores?folder=hermes-abc"),
                ("POST", "/rest/db/scan?folder=hermes-abc"),
            ],
        )
        self.assertEqual(RecordingHandler.calls[1][3]["order"], "random")
        self.assertEqual(RecordingHandler.calls[2][3], {"ignore": ignores})

    def test_folder_status_reduces_live_api_values(self):
        status = self.client.folder_status("hermes-abc", "REMOTE-ID")

        self.assertEqual(status, {
            "state": "idle",
            "pull_errors": 2,
            "folder_errors": 1,
            "local_completion": 100.0,
            "remote_completion": 99.0,
            "remote_state": "valid",
            "connected": True,
        })


if __name__ == "__main__":
    unittest.main()
