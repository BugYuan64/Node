import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
import json
import subprocess

from cohost import deploy
from cohost.deploy import prepare_settings


BASE = {
    "hy_domain": "hy.example.com",
    "hy_port": 8443,
    "hy_password": "",
    "sub_token": "",
    "sub_filename": "proxy-001.yaml",
    "hy_node": "proxy-HY",
    "probe_url": "https://www.gstatic.com/generate_204",
    "probe_interval": 300,
    "probe_tolerance": 50,
}


class SettingsTests(unittest.TestCase):
    def test_first_install_rollback_stops_and_disables_before_removing_unit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unit, pending, backup = root / "hy.service", root / "pending.json", root / "backup.json"
            unit.write_text("new unit")
            backup.write_text(json.dumps({str(unit): None}))
            pending.write_text(json.dumps({"action": "install", "backup": str(backup)}))
            service = {"running": True, "enabled": True}

            def systemctl(*command, **kwargs):
                if "stop" in command:
                    service["running"] = False
                if "disable" in command:
                    if not unit.exists():
                        return subprocess.CompletedProcess(command, 1)
                    service["enabled"] = False
                code = 0 if service["running"] else 3
                return subprocess.CompletedProcess(command, code if "is-active" in command else 0)

            def unchecked(command, **kwargs):
                return systemctl(*command, **kwargs)

            with patch.multiple(deploy, UNIT=unit, PENDING=pending, BACKUP_ROOT=root), \
                 patch.object(deploy, "run", side_effect=systemctl), \
                 patch.object(deploy.subprocess, "run", side_effect=unchecked):
                deploy.restore_pending()
            self.assertFalse(service["running"])
            self.assertFalse(service["enabled"])
            self.assertFalse(unit.exists())
            self.assertFalse(pending.exists())

    def test_network_tuning_preserves_larger_existing_buffers(self):
        from cohost.deploy import network_settings
        result = network_settings({"net.core.rmem_max": "33554432", "net.core.wmem_max": "4194304",
                                   "net.ipv4.tcp_congestion_control": "cubic"})
        self.assertEqual(result["net.core.rmem_max"], "33554432")
        self.assertEqual(result["net.core.wmem_max"], "7340032")
        self.assertEqual(result["net.ipv4.tcp_congestion_control"], "bbr")

    def test_first_install_generates_independent_credentials(self):
        result = prepare_settings(BASE, None)
        self.assertEqual(len(result["hy_password"]), 64)
        self.assertEqual(len(result["sub_token"]), 64)
        self.assertNotEqual(result["hy_password"], result["sub_token"])

    def test_reinstall_reuses_saved_credentials(self):
        saved = prepare_settings(BASE, None)
        self.assertEqual(prepare_settings(BASE, saved), saved)

    def test_reinstall_rejects_config_drift(self):
        saved = prepare_settings(BASE, None)
        with self.assertRaises(ValueError):
            prepare_settings(dict(BASE, hy_domain="different.example.com"), saved)


if __name__ == "__main__":
    unittest.main()
