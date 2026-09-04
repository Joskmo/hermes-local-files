import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from hermes_local_files.admin_cli import Check, main


class AdminCliTests(unittest.TestCase):
    def run_cli(self, argv, checks):
        output = io.StringIO()
        with patch(
            "hermes_local_files.admin_cli.collect_checks",
            return_value=checks,
        ), redirect_stdout(output):
            code = main(argv)
        return code, output.getvalue()

    def test_doctor_json_has_stable_machine_readable_shape(self):
        checks = [
            Check("service", True, "active", ""),
            Check("sync", False, "offline", "Restart the tunnel"),
        ]

        code, output = self.run_cli(
            ["doctor", "--scope", "macos", "--json"],
            checks,
        )

        self.assertEqual(code, 1)
        self.assertEqual(
            json.loads(output),
            {
                "schema_version": 1,
                "ok": False,
                "scope": "macos",
                "checks": [
                    {"id": "service", "ok": True, "detail": "active", "remedy": ""},
                    {
                        "id": "sync",
                        "ok": False,
                        "detail": "offline",
                        "remedy": "Restart the tunnel",
                    },
                ],
            },
        )

    def test_status_human_output_is_short_and_actionable(self):
        checks = [Check("companion", False, "not running", "Log in again")]

        code, output = self.run_cli(["status", "--scope", "macos"], checks)

        self.assertEqual(code, 1)
        self.assertIn("[FAIL] companion: not running", output)
        self.assertIn("fix: Log in again", output)

    def test_success_exits_zero(self):
        code, output = self.run_cli(
            ["doctor", "--scope", "server", "--json"],
            [Check("service", True, "active", "")],
        )

        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["ok"])

    def test_install_subcommand_forwards_arguments_without_shell(self):
        with patch("hermes_local_files.admin_cli.subprocess.run") as run:
            run.return_value.returncode = 0

            code = main(["install-server", "--data-root", "/srv/test"])

        self.assertEqual(code, 0)
        argv = run.call_args.args[0]
        self.assertIsInstance(argv, list)
        self.assertTrue(argv[1].endswith("scripts/install_server.py"))
        self.assertEqual(argv[-2:], ["--data-root", "/srv/test"])
        self.assertNotIn("shell", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
