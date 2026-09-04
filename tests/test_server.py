import tempfile
import unittest
from pathlib import Path

from hermes_local_files.server import DEFAULT_IGNORES, ServerProvisioner


class FakeSyncthing:
    def __init__(self):
        self.devices = []
        self.folders = []
        self.ignores = []
        self.scans = []

    def device_id(self):
        return "SERVER-ID"

    def put_device(self, **payload):
        self.devices.append(payload)

    def put_folder(self, payload):
        self.folders.append(payload)

    def set_ignores(self, folder_id, patterns):
        self.ignores.append((folder_id, patterns))

    def rescan(self, folder_id):
        self.scans.append(folder_id)


class ServerProvisionerTests(unittest.TestCase):
    def test_provision_creates_safe_versioned_folder(self):
        with tempfile.TemporaryDirectory() as raw:
            sync = FakeSyncthing()
            provisioner = ServerProvisioner(Path(raw), sync)

            result = provisioner.provision(
                mapping_id="map-123",
                name="Семейный бюджет",
                local_device_id="MAC-ID",
            )

            expected = Path(raw).resolve() / "semeinyi-biudzhet"
            self.assertTrue(expected.is_dir())
            self.assertEqual(result["server_path"], str(expected))
            self.assertEqual(result["server_device_id"], "SERVER-ID")
            self.assertEqual(sync.devices[0]["device_id"], "MAC-ID")
            self.assertEqual(sync.folders[0]["path"], str(expected))
            self.assertEqual(sync.folders[0]["versioning"]["type"], "staggered")
            self.assertEqual(sync.ignores[0][1], DEFAULT_IGNORES)
            self.assertEqual(sync.scans, [result["folder_id"]])

    def test_repeating_provision_is_idempotent(self):
        with tempfile.TemporaryDirectory() as raw:
            sync = FakeSyncthing()
            provisioner = ServerProvisioner(Path(raw), sync)

            first = provisioner.provision("same-map", "Project", "MAC-ID")
            second = provisioner.provision("same-map", "Project", "MAC-ID")

            self.assertEqual(first, second)
            self.assertEqual(len(list(Path(raw).iterdir())), 1)

    def test_mapping_cannot_be_rebound_to_another_name(self):
        with tempfile.TemporaryDirectory() as raw:
            provisioner = ServerProvisioner(Path(raw), FakeSyncthing())
            provisioner.provision("map-1", "First", "MAC-ID")

            with self.assertRaises(ValueError):
                provisioner.provision("map-1", "Second", "MAC-ID")

    def test_different_mapping_cannot_claim_existing_project_path(self):
        with tempfile.TemporaryDirectory() as raw:
            provisioner = ServerProvisioner(Path(raw), FakeSyncthing())
            provisioner.provision("first-mapping", "Family", "MAC-ID")

            with self.assertRaisesRegex(ValueError, "already used"):
                provisioner.provision("second-mapping", "Family", "MAC-ID")

    def test_private_files_and_internal_manifest_are_ignored(self):
        self.assertIn("(?d).env", DEFAULT_IGNORES)
        self.assertIn("(?d).env.*", DEFAULT_IGNORES)
        self.assertIn("(?d).hermes-local-files.json", DEFAULT_IGNORES)

    def test_invalid_device_id_does_not_create_folder(self):
        with tempfile.TemporaryDirectory() as raw:
            provisioner = ServerProvisioner(Path(raw), FakeSyncthing())

            with self.assertRaises(ValueError):
                provisioner.provision("map-1", "Project", "../bad")

            self.assertEqual(list(Path(raw).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
