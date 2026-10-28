import tempfile
import unittest
import os
import hashlib
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from cohost import deploy
from cohost.containers import manage


class ContainerSafetyTests(unittest.TestCase):
    def test_container_check_rejects_changed_xray_state_before_runtime_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'deploy.env'
            state.write_bytes(b'changed-state')
            saved = {'xray_sha256': hashlib.sha256(b'original-state').hexdigest(), 'settings': {}}
            with patch.object(manage.legacy, 'XRAY_STATE', state), \
                 patch.object(manage.legacy, 'PENDING', Path(directory) / 'absent'), \
                 patch.object(manage.legacy, 'read_state', return_value=saved), \
                 patch.object(manage.legacy, 'load_xray_state', return_value={}), \
                 patch.object(manage.legacy, 'render', side_effect=RuntimeError('runtime checks entered')):
                with self.assertRaisesRegex(RuntimeError, 'Xray state drifted'):
                    manage.check()

    def test_diagnostics_handle_timeout_without_losing_private_error(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            error = subprocess.TimeoutExpired('example', 1, output=b'private output', stderr=b'private error')
            with patch.object(manage.legacy, 'BACKUP_ROOT', Path(directory)), \
                 patch.object(manage.legacy, 'atomic_write', side_effect=lambda path, data, mode: path.write_bytes(data)):
                manage.save_diagnostic(error)
            self.assertIn('private error', (Path(directory) / 'docker-last-error.txt').read_text())

    def test_renewed_certificate_ownership_recovery_preserves_content_and_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert = root / "renewed.key"
            cert.write_bytes(b"fictional-renewed-certificate-key")
            cert.chmod(0o600)
            mode = cert.stat().st_mode
            original_stat = Path.stat

            def root_owned_certificate(path, *args, **kwargs):
                info = original_stat(path, *args, **kwargs)
                if path == cert:
                    fields = list(info)
                    fields[4] = 0
                    return os.stat_result(fields)
                return info

            account = SimpleNamespace(pw_uid=999, pw_gid=988)
            with patch.object(manage.legacy, "pwd", SimpleNamespace(getpwnam=lambda name: account)), \
                 patch.object(Path, "stat", root_owned_certificate), \
                 patch.object(manage.os, "chown", create=True) as chown:
                manage.restore_caddy_ownership(root)
            chown.assert_any_call(cert, 999, 988, follow_symlinks=False)
            self.assertEqual(cert.read_bytes(), b"fictional-renewed-certificate-key")
            self.assertEqual(cert.stat().st_mode, mode)

    def test_caddy_storage_symlink_is_rejected(self):
        with patch.object(manage.legacy, "pwd", SimpleNamespace(getpwnam=lambda name: None)), \
             patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                manage.restore_caddy_ownership(Path("example"))

    def test_ownership_failure_retains_marker_and_does_not_start_legacy_services(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "docker.json"
            marker.write_text('{"phase":"active"}')
            with patch.object(manage, "MARKER", marker), patch.object(manage, "compose"), \
                 patch.object(manage, "run") as run, \
                 patch.object(manage, "restore_caddy_ownership", side_effect=PermissionError("ownership")):
                with self.assertRaises(PermissionError):
                    manage.restore_services({"enabled": dict.fromkeys(manage.SERVICES, True)})
                run.assert_not_called()
            self.assertTrue(marker.exists())

    def test_legacy_maintenance_refuses_container_owned_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "docker.json"
            marker.write_text('{"phase":"active"}')
            with patch.object(deploy, "STATE_DIR", Path(directory)), \
                 patch("sys.argv", ["deploy.py", "update-rules"]), \
                 patch.object(deploy, "require_server", side_effect=RuntimeError("legacy maintenance entered")) as require_server:
                with self.assertRaisesRegex(RuntimeError, "containers/manage.py"):
                    deploy.main()
                require_server.assert_not_called()

    def test_failed_rollback_keeps_recovery_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "docker.json"
            marker.write_text('{"phase":"switching"}')
            with patch.object(manage, "MARKER", marker), \
                 patch.object(manage, "compose"), patch.object(manage, "run"), \
                 patch.object(manage, "restore_caddy_ownership", create=True), \
                 patch.object(manage.legacy, "check", side_effect=RuntimeError("unhealthy")):
                with self.assertRaisesRegex(RuntimeError, "unhealthy"):
                    manage.restore_services({"enabled": dict.fromkeys(manage.SERVICES, True)})
            self.assertTrue(marker.exists())

    def test_rollback_stops_containers_before_starting_legacy_services(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "docker.json"
            marker.write_text('{"phase":"active"}')
            calls = []
            with patch.object(manage, "MARKER", marker), \
                 patch.object(manage, "compose", side_effect=lambda *args: calls.append(("compose", *args))), \
                 patch.object(manage, "run", side_effect=lambda *args: calls.append(args)), \
                 patch.object(manage, "restore_caddy_ownership", create=True,
                              side_effect=lambda: calls.append(("ownership",))), \
                 patch.object(manage.legacy, "check", side_effect=lambda: calls.append(("verified",))):
                manage.restore_services({"enabled": dict.fromkeys(manage.SERVICES, True)})
            self.assertEqual(calls[0], ("compose", "down", "--timeout", "15"))
            self.assertLess(calls.index(("ownership",)), calls.index(("systemctl", "start", *manage.SERVICES)))
            self.assertIn(("systemctl", "start", *manage.SERVICES), calls)
            self.assertEqual(calls[-1], ("verified",))
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
