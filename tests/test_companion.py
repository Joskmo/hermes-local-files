import json
import tempfile
import unittest
from pathlib import Path

from hermes_local_files.companion import (
    LocalProvisioner,
    MappingStore,
    StatusStore,
    TunnelCommand,
)


class FakeSyncthing:
    def __init__(self):
        self.devices = []
        self.folders = []
        self.ignores = []
        self.scans = []

    def device_id(self):
        return "MAC-ID"

    def put_device(self, **payload):
        self.devices.append(payload)

    def put_folder(self, payload):
        self.folders.append(payload)

    def set_ignores(self, folder_id, patterns):
        self.ignores.append((folder_id, patterns))

    def rescan(self, folder_id):
        self.scans.append(folder_id)


class MappingStoreTests(unittest.TestCase):
    def test_store_is_atomic_and_roundtrips_mapping(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "mappings.json"
            store = MappingStore(path)
            payload = {
                "mapping_id": "map-1",
                "name": "Family",
                "local_path": "/Users/example/Family",
                "folder_id": "hermes-abc",
                "server_path": "/srv/projects/family",
                "server_device_id": "SERVER-ID",
            }

            store.put(payload)

            self.assertEqual(store.list(), [payload])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            json.loads(path.read_text())

    def test_same_mapping_updates_but_duplicate_local_folder_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            store = MappingStore(Path(raw) / "mappings.json")
            first = {
                "mapping_id": "map-1",
                "local_path": "/Users/example/Family",
                "name": "One",
            }
            store.put(first)
            store.put({**first, "name": "Renamed"})

            self.assertEqual(store.list()[0]["name"], "Renamed")
            with self.assertRaises(ValueError):
                store.put({
                    "mapping_id": "map-2",
                    "local_path": "/Users/example/Family",
                    "name": "Duplicate",
                })


class TunnelCommandTests(unittest.TestCase):
    def test_command_has_no_shell_and_forwards_only_loopback(self):
        known_hosts = Path(__file__)
        command = TunnelCommand(
            ssh_target="home-hermes",
            ssh_port=6000,
            local_port=32200,
            remote_port=22000,
            identity_file=Path(__file__),
            known_hosts_file=known_hosts,
        ).argv()

        self.assertEqual(command[0], "/usr/bin/ssh")
        self.assertIn("127.0.0.1:32200:127.0.0.1:22000", command)
        self.assertEqual(command[command.index("-p") + 1], "6000")
        self.assertEqual(
            command[command.index("-i") + 1],
            str(Path(__file__).resolve()),
        )
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("IdentitiesOnly=yes", command)
        self.assertIn(f"UserKnownHostsFile={known_hosts.resolve()}", command)
        self.assertIn("GlobalKnownHostsFile=/dev/null", command)
        self.assertEqual(command[-1], "home-hermes")
        self.assertNotIn("sh", command)

    def test_unsafe_target_or_port_is_rejected(self):
        for target in ("", "host; rm -rf /", "$(bad)", "host name", "host:6000"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                TunnelCommand(target, 32200, 22000).argv()
        with self.assertRaises(ValueError):
            TunnelCommand("home-hermes", 22, 22000).argv()
        with self.assertRaises(ValueError):
            TunnelCommand("home-hermes", 32200, 22000, ssh_port=70000).argv()


class StatusStoreTests(unittest.TestCase):
    def test_status_is_derived_from_syncthing_tunnel_and_conflicts(self):
        with tempfile.TemporaryDirectory() as raw:
            local = Path(raw) / "Family"
            local.mkdir()
            mappings = MappingStore(Path(raw) / "mappings.json")
            mappings.put({
                "mapping_id": "map-1",
                "name": "Family",
                "local_path": str(local),
                "folder_id": "hermes-abc",
                "server_device_id": "SERVER-ID",
            })

            class LiveSyncthing:
                def folder_status(self, folder_id, device_id):
                    self.seen = (folder_id, device_id)
                    return {
                        "state": "idle",
                        "pull_errors": 0,
                        "folder_errors": 0,
                        "local_completion": 100.0,
                        "remote_completion": 100.0,
                        "remote_state": "valid",
                        "connected": True,
                    }

            sync = LiveSyncthing()
            status = StatusStore(mappings, sync, tunnel_up=lambda: True)

            self.assertEqual(status.list()[0]["status"], "synced")
            self.assertEqual(sync.seen, ("hermes-abc", "SERVER-ID"))

            (local / "note.sync-conflict-20260904.txt").write_text("both")
            self.assertEqual(status.list()[0]["status"], "attention")

    def test_status_is_offline_when_probe_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            local = Path(raw) / "Family"
            local.mkdir()
            mappings = MappingStore(Path(raw) / "mappings.json")
            mappings.put({
                "mapping_id": "map-1",
                "name": "Family",
                "local_path": str(local),
                "folder_id": "hermes-abc",
                "server_device_id": "SERVER-ID",
            })

            class BrokenSyncthing:
                def folder_status(self, *_args):
                    raise RuntimeError("offline")

            status = StatusStore(mappings, BrokenSyncthing(), tunnel_up=lambda: False)

            self.assertEqual(status.list()[0]["status"], "offline")


class LocalProvisionerTests(unittest.TestCase):
    def test_configures_server_through_tunnel_and_persists_mapping(self):
        with tempfile.TemporaryDirectory() as raw:
            local = Path(raw) / "Family"
            local.mkdir()
            sync = FakeSyncthing()
            store = MappingStore(Path(raw) / "mappings.json")
            provisioner = LocalProvisioner(sync, store, tunnel_port=32200)
            server = {
                "mapping_id": "map-1",
                "name": "Family",
                "folder_id": "hermes-abc",
                "server_path": "/srv/projects/family",
                "server_device_id": "SERVER-ID",
            }

            result = provisioner.provision(str(local), server)

            self.assertEqual(result["local_device_id"], "MAC-ID")
            self.assertEqual(sync.devices[0]["addresses"], ["tcp://127.0.0.1:32200"])
            self.assertEqual(sync.folders[0]["path"], str(local.resolve()))
            self.assertEqual(sync.folders[0]["type"], "sendreceive")
            self.assertEqual(sync.folders[0]["versioning"]["type"], "")
            self.assertEqual(store.list()[0]["server_path"], "/srv/projects/family")

    def test_missing_local_folder_is_rejected_before_syncthing_changes(self):
        with tempfile.TemporaryDirectory() as raw:
            sync = FakeSyncthing()
            provisioner = LocalProvisioner(
                sync,
                MappingStore(Path(raw) / "mappings.json"),
                tunnel_port=32200,
            )

            with self.assertRaises(ValueError):
                provisioner.provision(
                    str(Path(raw) / "missing"),
                    {
                        "mapping_id": "map-1",
                        "name": "Missing",
                        "folder_id": "hermes-abc",
                        "server_path": "/srv/projects/missing",
                        "server_device_id": "SERVER-ID",
                    },
                )

            self.assertEqual(sync.devices, [])


if __name__ == "__main__":
    unittest.main()
