from contextlib import ExitStack
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cohost import deploy
from cohost.render import load_xray_state, render, render_service_unit
from cohost.tests.test_render import SETTINGS, XRAY_TEXT


class ServiceDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.unit = self.root / "hy2-mihomo.service"
        self.pending = self.root / "pending.json"
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.sub = self.root / "subscriptions"
        self.sub.mkdir()
        self.caddy = self.root / "caddy.caddy"
        self.hy = self.root / "hysteria.yaml"
        xray = self.root / "deploy.env"
        xray.write_text(XRAY_TEXT, encoding="utf-8")
        self.xray = load_xray_state(xray)
        self.data = render(self.xray, SETTINGS, legacy=False)
        self.caddy.write_text(self.data["caddy.caddy"], encoding="utf-8")
        self.hy.write_text(self.data["hysteria.yaml"], encoding="utf-8")
        (self.sub / SETTINGS["sub_filename"]).write_text(self.data["mihomo.yaml"], encoding="utf-8")
        self.old = render_service_unit(coupled=True)
        self.unit.write_text(self.old, encoding="utf-8")
        self.manager = {"unit": self.old, "hy_pid": 42, "caddy_pid": 43, "reloads": 0}
        self.fail_check = False
        self.fail_reload = False
        self.strong_dropin = False

        def run(*args, **kwargs):
            output = ""
            if args[:2] == ("systemctl", "daemon-reload"):
                if self.fail_reload:
                    raise RuntimeError("manager reload unavailable")
                self.manager["unit"] = self.unit.read_text(encoding="utf-8")
                self.manager["reloads"] += 1
            elif args[0] == "systemctl" and args[1] in {"restart", "stop"}:
                self.manager["hy_pid"] += 1
                self.manager["caddy_pid"] += 1
            elif args[:2] == ("systemctl", "show"):
                output = "caddy.service\n" if self.strong_dropin or "Requires=" in self.manager["unit"] else "sysinit.target\n"
            return subprocess.CompletedProcess(args, 0, stdout=output)

        def check(*, allow_pending=False, allow_coupled_unit=False):
            if self.pending.exists() and not allow_pending:
                raise RuntimeError("pending change")
            deploy.check_rendered_files(self.data, SETTINGS["sub_filename"],
                                        allow_coupled_unit=allow_coupled_unit)
            if not allow_coupled_unit:
                deploy.check_service_dependencies()
            if self.fail_check and allow_pending:
                raise RuntimeError("post-migration check failed")

        def write(path, data, mode, uid=0, gid=0):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.multiple(deploy, UNIT=self.unit, PENDING=self.pending,
                                               BACKUP_ROOT=self.backups, CADDY_SNIPPET=self.caddy,
                                               HY_CONFIG=self.hy, SUB_DIR=self.sub))
        self.stack.enter_context(patch.object(deploy, "run", side_effect=run))
        self.stack.enter_context(patch.object(deploy, "check", side_effect=check))
        self.stack.enter_context(patch.object(deploy, "atomic_write", side_effect=write))
        self.stack.enter_context(patch.object(deploy, "read_state", return_value={
            "phase": "final", "settings": dict(SETTINGS)}))

    def test_migration_changes_only_unit_and_keeps_processes_running(self):
        before = {p: p.read_bytes() for p in (self.caddy, self.hy, self.sub / SETTINGS["sub_filename"])}
        deploy.decouple_services()
        self.assertEqual(self.unit.read_text(encoding="utf-8"), render_service_unit())
        self.assertEqual(self.manager["unit"], render_service_unit())
        self.assertEqual((self.manager["hy_pid"], self.manager["caddy_pid"]), (42, 43))
        self.assertEqual(self.manager["reloads"], 1)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertFalse(self.pending.exists())
        self.assertEqual(len(list(self.backups.glob("*.json"))), 1)

    def test_migration_is_idempotent(self):
        deploy.decouple_services()
        backups = sorted(self.backups.iterdir())
        deploy.decouple_services()
        self.assertEqual(sorted(self.backups.iterdir()), backups)
        self.assertEqual(self.manager["reloads"], 1)
        self.assertEqual(self.manager["hy_pid"], 42)

    def test_staged_deployment_can_migrate_without_reinstall_or_route_change(self):
        self.data = render(self.xray, SETTINGS, legacy=True)
        self.caddy.write_text(self.data["caddy.caddy"], encoding="utf-8")
        staged = {"phase": "staged", "settings": dict(SETTINGS)}
        with patch.object(deploy, "read_state", return_value=staged):
            deploy.decouple_services()
        self.assertEqual(staged["phase"], "staged")
        self.assertEqual(self.caddy.read_text(encoding="utf-8"), self.data["caddy.caddy"])
        self.assertEqual(self.manager["unit"], render_service_unit())
        self.assertEqual(self.manager["hy_pid"], 42)

    def test_unknown_unit_is_rejected_before_any_write(self):
        unknown = self.old + "\n# unmanaged modification\n"
        self.unit.write_text(unknown, encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "deployed file differs"):
            deploy.decouple_services()
        self.assertEqual(self.unit.read_text(encoding="utf-8"), unknown)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_other_config_drift_is_rejected_before_unit_migration(self):
        self.hy.write_text("unmanaged config", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "deployed file differs"):
            deploy.decouple_services()
        self.assertEqual(self.unit.read_text(encoding="utf-8"), self.old)
        self.assertFalse(self.pending.exists())

    def test_post_migration_failure_restores_old_unit_without_restart(self):
        self.fail_check = True
        with self.assertRaisesRegex(RuntimeError, "post-migration"):
            deploy.decouple_services()
        self.assertEqual(self.unit.read_text(encoding="utf-8"), self.old)
        self.assertEqual(self.manager["unit"], self.old)
        self.assertEqual(self.manager["hy_pid"], 42)
        self.assertFalse(self.pending.exists())

    def test_interrupted_migration_recovers_without_restarting_services(self):
        deploy.begin_change("decouple-services", [self.unit], SETTINGS)
        self.unit.write_text(render_service_unit(), encoding="utf-8")
        self.manager["unit"] = render_service_unit()
        deploy.restore_pending()
        self.assertEqual(self.unit.read_text(encoding="utf-8"), self.old)
        self.assertEqual(self.manager["unit"], self.old)
        self.assertEqual((self.manager["hy_pid"], self.manager["caddy_pid"]), (42, 43))
        self.assertFalse(self.pending.exists())

    def test_recovery_retains_pending_if_daemon_reload_fails(self):
        deploy.begin_change("decouple-services", [self.unit], SETTINGS)
        self.unit.write_text(render_service_unit(), encoding="utf-8")
        self.fail_reload = True
        with self.assertRaisesRegex(RuntimeError, "reload unavailable"):
            deploy.restore_pending()
        self.assertTrue(self.pending.exists())
        self.assertEqual(self.manager["hy_pid"], 42)

    def test_normal_checks_reject_old_strong_dependency(self):
        with self.assertRaisesRegex(RuntimeError, "deployed file differs"):
            deploy.check_rendered_files(self.data, SETTINGS["sub_filename"])
        deploy.check_rendered_files(self.data, SETTINGS["sub_filename"], allow_coupled_unit=True)

    def test_strong_systemd_dropin_is_detected_and_migration_rolled_back(self):
        self.strong_dropin = True
        with self.assertRaisesRegex(RuntimeError, "strong Caddy dependency"):
            deploy.decouple_services()
        self.assertEqual(self.unit.read_text(encoding="utf-8"), self.old)
        self.assertEqual(self.manager["hy_pid"], 42)
        self.assertFalse(self.pending.exists())


if __name__ == "__main__":
    unittest.main()
