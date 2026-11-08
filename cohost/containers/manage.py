#!/usr/bin/env python3
"""Migrate this existing Ubuntu deployment to Compose without rotating credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deploy as legacy

ROOT = Path(__file__).resolve().parent
MARKER = legacy.STATE_DIR / "docker.json"
SERVICES = ("xray", "caddy", "hy2-mihomo")
BINARIES = {"xray": Path("/usr/local/bin/xray"), "caddy": Path("/usr/bin/caddy"),
            "hysteria": legacy.HY_BIN}


def run(*args, timeout=120):
    # Capture diagnostics: Caddy adapters and application logs may contain secrets.
    return subprocess.run([str(a) for a in args], check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout).stdout.strip()


def compose(*args, timeout=120):
    return run("docker", "compose", "--project-directory", ROOT, "--env-file", ROOT / ".env",
               "-f", ROOT / "compose.yaml", *args, timeout=timeout)


def save(data):
    legacy.atomic_write(MARKER, (json.dumps(data, indent=2) + "\n").encode(), 0o600)


def fingerprint(paths):
    result = {}
    for path in paths:
        if path.is_file():
            info = path.stat()
            result[str(path)] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "mode": info.st_mode & 0o777, "uid": info.st_uid, "gid": info.st_gid}
    return result


def protected_paths():
    return [legacy.STATE, legacy.XRAY_STATE, legacy.XRAY_CONFIG, legacy.HY_CONFIG,
            *Path("/etc/caddy").rglob("*"), *legacy.SUB_DIR.rglob("*"),
            *Path("/var/lib/caddy").rglob("*.crt"), *Path("/var/lib/caddy").rglob("*.key"),
            *legacy.ACME_DIR.rglob("*.crt"), *legacy.ACME_DIR.rglob("*.key")]


def prepare_images():
    import pwd
    pairs = (("XRAY", "xray", "nobody"), ("CADDY", "caddy", "caddy"),
             ("HY", "hysteria", "hy2proxy"))
    environment = []
    hashes = {}
    for prefix, service, account_name in pairs:
        binary = BINARIES[service]
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        hashes[service] = digest
        tag = f"local/cohost-{service}:sha256-{digest[:16]}"
        with tempfile.TemporaryDirectory(prefix="cohost-image-") as directory:
            context = Path(directory)
            shutil.copy2(binary, context / "service")
            shutil.copy2(ROOT / "Dockerfile", context / "Dockerfile")
            shutil.copy2("/etc/ssl/certs/ca-certificates.crt", context / "ca-certificates.crt")
            run("docker", "build", "--network=none", "--tag", tag, directory, timeout=180)
        # Pin the immutable local image ID, even if someone later retags an image.
        image_id = run("docker", "image", "inspect", tag, "--format", "{{.Id}}")
        account = pwd.getpwnam(account_name)
        environment += [f"{prefix}_IMAGE={image_id}"]
        if service != "caddy":
            environment += [f"{prefix}_USER={account.pw_uid}:{account.pw_gid}"]
    legacy.atomic_write(ROOT / ".env", ("\n".join(environment) + "\n").encode(), 0o600)
    compose("config", "--quiet")
    return hashes


def validate_containers():
    compose("run", "--rm", "--no-deps", "xray", "run", "-test", "-config", str(legacy.XRAY_CONFIG))
    compose("run", "--rm", "--no-deps", "caddy", "validate", "--config", str(legacy.CADDY_MAIN),
            "--adapter", "caddyfile")
    compose("run", "--rm", "--no-deps", "hysteria", "version")


def check():
    if legacy.PENDING.exists():
        raise RuntimeError("legacy transaction is pending")
    state = legacy.read_state()
    xray = legacy.load_xray_state(legacy.XRAY_STATE)
    if hashlib.sha256(legacy.XRAY_STATE.read_bytes()).hexdigest() != state["xray_sha256"]:
        raise RuntimeError("Xray state drifted since cohost installation")
    data = legacy.render(xray, state["settings"], legacy=False)
    legacy.check_rendered_files(data, state["settings"]["sub_filename"])
    info = legacy.CADDY_SNIPPET.stat()
    if (info.st_mode & 0o777 != 0o640 or info.st_uid != 0
            or info.st_gid != legacy.grp.getgrnam("caddy").gr_gid):
        raise RuntimeError("Caddy subscription token config must be root:caddy 0640")
    if state["phase"] != "final":
        raise RuntimeError("only finalized deployments are supported")
    for service in SERVICES:
        if subprocess.run(["systemctl", "is-active", "--quiet", service]).returncode == 0:
            raise RuntimeError(f"legacy service is still running: {service}")
    for service in ("xray", "caddy", "hysteria"):
        container = compose("ps", "--all", "--quiet", service)
        if not container:
            raise RuntimeError(f"missing container: {service}")
        info = json.loads(run("docker", "inspect", container))[0]
        if not info["State"]["Running"] or info["RestartCount"]:
            raise RuntimeError(f"container stopped or restarted: {service}")
        if info["HostConfig"]["RestartPolicy"]["Name"] != "unless-stopped":
            raise RuntimeError(f"restart policy drift: {service}")
        configured = compose("config", "--format", "json")
        expected_image = json.loads(configured)["services"][service]["image"]
        if info["Image"] != expected_image:
            raise RuntimeError(f"image drift: {service}")
    validate_containers()
    legacy.validate_mihomo(data["mihomo.yaml"])
    legacy.hy_smoke(state["settings"])
    legacy.hy_smoke(state["settings"], bad_password=True)
    legacy.check_subscription(xray, state["settings"], data["mihomo.yaml"].encode())
    old = f'https://{xray["SUB_DOMAIN"]}/s/{xray["SUB_TOKEN"]}/{xray["SUB_FILENAME"]}'
    if legacy.subscription_status(xray, old) != "404":
        raise RuntimeError("old subscription became accessible")
    print("Compose: 3 running containers, pinned images, no restarts; HY TCP/UDP/auth and HTTPS subscription verified")


def restore_caddy_ownership(root=Path("/var/lib/caddy")):
    """Root container renewals must remain readable by the original service user."""
    account = legacy.pwd.getpwnam("caddy")
    if root.is_symlink():
        raise RuntimeError("Caddy storage root must not be a symlink")
    for path in [root, *root.rglob("*")]:
        if not path.is_symlink() and path.stat().st_uid == 0:
            os.chown(path, account.pw_uid, account.pw_gid, follow_symlinks=False)


def restore_services(record):
    compose("down", "--timeout", "15")
    restore_caddy_ownership()
    for service in SERVICES:
        run("systemctl", "enable" if record["enabled"][service] else "disable", service)
    run("systemctl", "start", *SERVICES)
    legacy.check()
    MARKER.unlink()
    print("Restored and verified original systemd services")


def migrate():
    if MARKER.exists():
        if json.loads(MARKER.read_text())["phase"] == "active":
            check()
            return
        raise RuntimeError("interrupted migration; run rollback before retrying")
    legacy.check()
    hashes = prepare_images()
    validate_containers()
    before = fingerprint(protected_paths())
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = legacy.BACKUP_ROOT / f"docker-before-{stamp}.tar.gz"
    paths = [Path(p) for p in ("/etc/caddy", "/etc/cohost-proxy", "/etc/xray-mihomo-deployer",
             "/usr/local/etc/xray", "/etc/hy2-mihomo", "/var/lib/caddy", "/var/lib/hy2-mihomo",
             "/srv/mihomo-sub", "/etc/systemd/system/xray.service",
             "/etc/systemd/system/xray.service.d", "/etc/systemd/system/hy2-mihomo.service",
             "/usr/lib/systemd/system/caddy.service")]
    paths.append(ROOT.parent)
    with tarfile.open(backup, "w:gz") as archive:
        for path in paths:
            archive.add(path, arcname=str(path).lstrip("/"))
    os.chmod(backup, 0o600)
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    legacy.atomic_write(Path(str(backup) + ".sha256"), f"{digest}  {backup.name}\n".encode(), 0o600)
    # Verify every archived regular file against its live source before cutover.
    with tarfile.open(backup) as archive:
        for member in archive.getmembers():
            if member.isfile():
                archived = archive.extractfile(member).read()
                if archived != (Path("/") / member.name).read_bytes():
                    raise RuntimeError(f"backup verification failed: {member.name}")
    record = {"phase": "switching", "backup": str(backup), "binary_sha256": hashes,
              "enabled": {s: run("systemctl", "is-enabled", s) == "enabled" for s in SERVICES},
              "protected": before, "created_utc": stamp}
    save(record)
    try:
        run("systemctl", "stop", *SERVICES)
        compose("up", "--detach", "--no-build", "--pull", "never")
        time.sleep(4)
        check()
        if fingerprint(Path(p) for p in before) != before:
            raise RuntimeError("protected configuration/certificate changed during cutover")
        run("systemctl", "disable", *SERVICES)
        run("systemctl", "enable", "docker")
        record["phase"] = "active"
        save(record)
        print(f"Migration complete; private rollback archive: {backup}")
    except BaseException:
        try:
            logs = compose("logs", "--no-color", "--tail", "80")
            legacy.atomic_write(legacy.BACKUP_ROOT / "docker-last-container-logs.txt", logs.encode(), 0o600)
        except Exception:
            pass
        restore_services(record)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "check", "rollback"))
    args = parser.parse_args()
    legacy.require_server()
    os.umask(0o077)
    with legacy.LOCK.open("w") as lock:
        legacy.fcntl.flock(lock, legacy.fcntl.LOCK_EX | legacy.fcntl.LOCK_NB)
        if args.command == "migrate":
            migrate()
        elif args.command == "check":
            check()
        else:
            restore_services(json.loads(MARKER.read_text()))


def save_diagnostic(error):
    def as_text(value):
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
    diagnostic = legacy.BACKUP_ROOT / "docker-last-error.txt"
    detail = as_text(getattr(error, "stdout", None)) + "\n" + as_text(getattr(error, "stderr", None))
    legacy.atomic_write(diagnostic, detail.encode(), 0o600)
    return diagnostic


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        # Diagnostics remain private; do not echo Caddy config or subscription tokens.
        diagnostic = save_diagnostic(error)
        print(f"Command failed; private diagnostics: {diagnostic}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
