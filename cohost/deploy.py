#!/usr/bin/env python3
"""Stage, verify, and finalize Xray + Hysteria2 on the existing 001 VPS."""

from __future__ import annotations

import argparse
import base64
try:
    import fcntl
except ImportError:  # Settings/render tests also run on Windows.
    fcntl = None
import gzip
try:
    import grp
    import pwd
except ImportError:
    grp = None
    pwd = None
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

try:
    from .render import load_xray_state, render, subscription_url, validate_settings
    from .verify import smoke
except ImportError:  # Also support `python3 deploy.py` from the cohost directory.
    from render import load_xray_state, render, subscription_url, validate_settings
    from verify import smoke


ROOT = Path(__file__).resolve().parent
XRAY_STATE = Path("/etc/xray-mihomo-deployer/deploy.env")
XRAY_CONFIG = Path("/usr/local/etc/xray/config.json")
CADDY_MAIN = Path("/etc/caddy/Caddyfile")
CADDY_SNIPPET = Path("/etc/caddy/Caddyfile.d/xray-mihomo.caddy")
STATE_DIR = Path("/etc/cohost-proxy")
STATE = STATE_DIR / "state.json"
PENDING = STATE_DIR / "pending.json"
HY_CONFIG = Path("/etc/hy2-mihomo/hysteria.yaml")
UNIT = Path("/etc/systemd/system/hy2-mihomo.service")
ACME_DIR = Path("/var/lib/hy2-mihomo/acme")
BIN_DIR = Path("/usr/local/lib/hy2-mihomo")
HY_BIN = BIN_DIR / "hysteria"
MIHOMO_BIN = BIN_DIR / "mihomo"
SUB_DIR = Path("/srv/mihomo-sub")
BACKUP_ROOT = Path("/var/backups/cohost-proxy")
MIHOMO_DATA_DIR = Path("/var/cache/cohost-proxy/mihomo")
LOCK = Path("/run/lock/cohost-proxy.lock")
NETWORK_SYSCTL = Path("/etc/sysctl.d/80-cohost-proxy.conf")
NETWORK_MODULES = Path("/etc/modules-load.d/cohost-proxy.conf")
MARKER = "managed-by: cohost-proxy"
ASSETS = {
    "x86_64": (
        "hysteria-linux-amd64", "907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691",
        "mihomo-linux-amd64-v1-v1.19.32.gz", "306f81e723e60ce6b828899a6fe83e1d00e9ecefb2dc8d4d849312a5bc00efdc"),
    "aarch64": (
        "hysteria-linux-arm64", "a68a61a84452ca250ce0368202521965ca9cc9d801a404f1dc9008ac6cf677a7",
        "mihomo-linux-arm64-v1.19.32.gz", "9dd862e28b46ff7d775f169cceebc28deccaa0a9e804237d421cd2571e0caba0"),
}


def run(*args: str, timeout: int = 120, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, timeout=timeout, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def prepare_settings(template: dict, saved: dict | None) -> dict:
    candidate = dict(template)
    if saved is None:
        for key in ("hy_password", "sub_token"):
            if not candidate.get(key):
                candidate[key] = secrets.token_hex(32)
    else:
        for key in ("hy_password", "sub_token"):
            if not candidate.get(key):
                candidate[key] = saved[key]
        if candidate != saved:
            raise ValueError("existing cohost settings differ; credential or route rotation needs a separate migration")
    return validate_settings(candidate)


def require_server() -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("install/check/finalize/show must run as root on the Ubuntu VPS")
    if os.uname().machine not in ASSETS:
        raise RuntimeError("unsupported CPU architecture")
    for name in ("systemctl", "caddy", "xray", "curl", "useradd"):
        if not shutil.which(name):
            raise RuntimeError(f"missing required command: {name}")


def atomic_write(path: Path, data: bytes, mode: int, uid: int = 0, gid: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".cohost-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def snapshot(path: Path) -> dict | None:
    if not path.exists():
        return None
    stat = path.stat()
    return {
        "content": base64.b64encode(path.read_bytes()).decode("ascii"),
        "mode": stat.st_mode & 0o777,
        "uid": stat.st_uid,
        "gid": stat.st_gid,
    }


def restore(path: Path, value: dict | None) -> None:
    if value is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write(path, base64.b64decode(value["content"]), value["mode"], value["uid"], value["gid"])


def begin_change(action: str, paths: list[Path], settings: dict) -> None:
    if PENDING.exists():
        raise RuntimeError("pending deployment exists; run install or finalize to recover first")
    BACKUP_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(BACKUP_ROOT, 0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4)
    backup = BACKUP_ROOT / f"{action}-{stamp}.json"
    manifest = {str(path): snapshot(path) for path in paths}
    atomic_write(backup, json.dumps(manifest).encode(), 0o600)
    atomic_write(PENDING, json.dumps({"action": action, "backup": str(backup), "settings": settings}).encode(), 0o600)


def restore_pending() -> None:
    if not PENDING.exists():
        return
    pending = json.loads(PENDING.read_text())
    backup = Path(pending["backup"])
    if backup.parent != BACKUP_ROOT or not backup.is_file():
        raise RuntimeError("pending backup is missing or outside the managed backup directory")
    manifest = json.loads(backup.read_text())
    if pending["action"] == "install" and str(UNIT) in manifest and manifest[str(UNIT)] is None and UNIT.exists():
        # disable --now after deleting the unit can fail before stopping it.
        run("systemctl", "stop", "hy2-mihomo.service")
        run("systemctl", "disable", "hy2-mihomo.service")
    for name, value in manifest.items():
        restore(Path(name), value)
    if pending["action"] == "tune-network":
        for key, value in pending.get("network_values", {}).items():
            run("sysctl", "-w", f"{key}={value}", capture=True)
        run("systemctl", "restart", "caddy.service", "hy2-mihomo.service")
        PENDING.unlink()
        return
    if pending["action"] == "update-rules":
        PENDING.unlink()
        return
    run("systemctl", "daemon-reload")
    if UNIT.exists():
        run("systemctl", "restart", "hy2-mihomo.service")
    else:
        status = subprocess.run(["systemctl", "is-active", "--quiet", "hy2-mihomo.service"], check=False,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if status not in (3, 4):
            raise RuntimeError("HY2 rollback could not confirm the new service is stopped; pending retained")
    run("systemctl", "reload", "caddy")
    PENDING.unlink()


def download(url: str, expected_hash: str, target: Path, *, compressed: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, archive_name = tempfile.mkstemp(prefix=".cohost-download-", dir=target.parent)
    binary_name = archive_name
    try:
        digest = hashlib.sha256()
        request = urllib.request.Request(url, headers={"User-Agent": "cohost-proxy/1"})
        with os.fdopen(fd, "wb") as archive, urllib.request.urlopen(request, timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                archive.write(chunk)
            archive.flush()
            os.fsync(archive.fileno())
        if digest.hexdigest() != expected_hash:
            raise RuntimeError(f"official asset checksum mismatch: {url}")
        if compressed:
            binary_fd, binary_name = tempfile.mkstemp(prefix=".cohost-binary-", dir=target.parent)
            with gzip.open(archive_name, "rb") as source, os.fdopen(binary_fd, "wb") as binary:
                shutil.copyfileobj(source, binary, length=1024 * 1024)
                binary.flush()
                os.fsync(binary.fileno())
        os.chmod(binary_name, 0o755)
        os.replace(binary_name, target)
    finally:
        for name in (archive_name, binary_name):
            if os.path.exists(name):
                os.unlink(name)


def install_binaries() -> None:
    hy_name, hy_hash, mihomo_name, mihomo_hash = ASSETS[os.uname().machine]
    if not HY_BIN.exists() or hashlib.sha256(HY_BIN.read_bytes()).hexdigest() != hy_hash:
        download(f"https://github.com/HyNetworks/hysteria/releases/download/app%2Fv2.13.0/{hy_name}", hy_hash, HY_BIN)
    if not MIHOMO_BIN.exists() or "1.19.32" not in run(str(MIHOMO_BIN), "-v", capture=True).stdout:
        download(f"https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/{mihomo_name}",
                 mihomo_hash, MIHOMO_BIN, compressed=True)
    if "2.13.0" not in run(str(HY_BIN), "version", capture=True).stdout:
        raise RuntimeError("Hysteria binary version mismatch")


def prepare_system() -> tuple[int, int, int]:
    try:
        account = pwd.getpwnam("hy2proxy")
    except KeyError:
        run("useradd", "--system", "--user-group", "--no-create-home", "--home-dir",
            "/var/lib/hy2-mihomo", "--shell", "/usr/sbin/nologin", "hy2proxy")
        account = pwd.getpwnam("hy2proxy")
    caddy_gid = grp.getgrnam("caddy").gr_gid
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    HY_CONFIG.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chmod(HY_CONFIG.parent, 0o750)
    os.chown(HY_CONFIG.parent, 0, account.pw_gid)
    ACME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ACME_DIR, 0o700)
    os.chown(ACME_DIR.parent, account.pw_uid, account.pw_gid)
    os.chown(ACME_DIR, account.pw_uid, account.pw_gid)
    SUB_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chmod(SUB_DIR, 0o750)
    os.chown(SUB_DIR, 0, caddy_gid)
    return account.pw_uid, account.pw_gid, caddy_gid


def preflight(xray: dict[str, str], settings: dict) -> None:
    for service in ("xray", "caddy"):
        run("systemctl", "is-active", "--quiet", service)
    if "import /etc/caddy/Caddyfile.d/*" not in CADDY_MAIN.read_text():
        raise RuntimeError("Caddy main config does not import managed snippets")
    snippet = CADDY_SNIPPET.read_text()
    if MARKER not in snippet and (xray["PROXY_DOMAIN"] not in snippet or xray["WS_TOKEN"] not in snippet or xray["SUB_FILENAME"] not in snippet):
        raise RuntimeError("unexpected Xray Caddy snippet; refusing to overwrite")
    if UNIT.exists() and MARKER not in UNIT.read_text():
        raise RuntimeError("existing HY service is not managed by this deployer")
    if HY_CONFIG.exists() and not UNIT.exists():
        raise RuntimeError("unowned Hysteria config exists")
    if not XRAY_CONFIG.exists():
        raise RuntimeError("existing Xray config missing")
    xray_ip = socket.gethostbyname(xray["PROXY_DOMAIN"])
    if socket.gethostbyname(settings["hy_domain"]) != xray_ip:
        raise RuntimeError("HY domain does not resolve to the existing Xray VPS")


def validate_mihomo(text: str) -> None:
    MIHOMO_DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(MIHOMO_DATA_DIR, 0o700)
    with tempfile.TemporaryDirectory(prefix="cohost-mihomo-") as directory:
        path = Path(directory) / "config.yaml"
        path.write_text(text, encoding="utf-8")
        run(str(MIHOMO_BIN), "-t", "-d", str(MIHOMO_DATA_DIR), "-f", str(path), timeout=60, capture=True)


def hy_smoke(settings: dict, *, bad_password: bool = False) -> None:
    config = {"HY_PASSWORD": settings["hy_password"], "HY_PORT": str(settings["hy_port"]),
              "PROXY_DOMAIN": settings["hy_domain"]}
    smoke(config, MIHOMO_BIN, bad_password=bad_password)


def subscription_status(xray: dict[str, str], url: str, *, output: Path | None = None) -> str:
    command = ["curl", "--silent", "--show-error", "--noproxy", "*", "--max-time", "20",
               "--resolve", f'{xray["SUB_DOMAIN"]}:443:127.0.0.1',
               "--output", str(output or os.devnull), "--write-out", "%{http_code}", url]
    return run(*command, timeout=25, capture=True).stdout


def check_subscription(xray: dict[str, str], settings: dict, expected: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="cohost-sub-") as directory:
        output = Path(directory) / "subscription.yaml"
        url = subscription_url(xray, settings)
        last = ""
        for _ in range(12):
            try:
                last = subscription_status(xray, url, output=output)
                if last == "200" and output.read_bytes() == expected:
                    break
            except Exception as error:
                last = str(error)
            time.sleep(5)
        else:
            raise RuntimeError(f"new HTTPS subscription failed certificate, status or bytes check: {last}")
        invalid = "0" * 64 if settings["sub_token"] != "0" * 64 else "1" * 64
        wrong = f'https://{xray["SUB_DOMAIN"]}/s/{invalid}/{settings["sub_filename"]}'
        if subscription_status(xray, wrong) != "404":
            raise RuntimeError("wrong-token subscription path must return 404")
        validate_mihomo(output.read_text(encoding="utf-8"))


def read_state() -> dict:
    if not STATE.exists():
        raise RuntimeError("cohost deployment is not installed")
    data = json.loads(STATE.read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("phase") not in {"staged", "final"}:
        raise RuntimeError("unknown cohost state")
    data["settings"] = validate_settings(data["settings"])
    return data


def check(*, allow_pending: bool = False) -> None:
    if PENDING.exists() and not allow_pending:
        raise RuntimeError("deployment has a pending change; run install/finalize to recover")
    state = read_state()
    xray = load_xray_state(XRAY_STATE)
    if hashlib.sha256(XRAY_STATE.read_bytes()).hexdigest() != state["xray_sha256"]:
        raise RuntimeError("Xray state drifted since cohost installation")
    settings = state["settings"]
    data = render(xray, settings, legacy=state["phase"] == "staged")
    expected = {
        CADDY_SNIPPET: "caddy.caddy",
        HY_CONFIG: "hysteria.yaml",
        UNIT: "hy2-mihomo.service",
        SUB_DIR / settings["sub_filename"]: "mihomo.yaml",
    }
    for path, key in expected.items():
        if not path.exists() or path.read_text(encoding="utf-8") != data[key]:
            raise RuntimeError(f"deployed file differs from rendered source: {path}")
    stat = CADDY_SNIPPET.stat()
    if stat.st_mode & 0o777 != 0o640 or stat.st_uid != 0 or stat.st_gid != grp.getgrnam("caddy").gr_gid:
        raise RuntimeError("Caddy subscription token config must be root:caddy 0640; run update-rules")
    run("systemctl", "is-active", "--quiet", "xray")
    run("systemctl", "is-active", "--quiet", "caddy")
    run("systemctl", "is-active", "--quiet", "hy2-mihomo.service")
    run("xray", "run", "-test", "-config", str(XRAY_CONFIG), timeout=30, capture=True)
    run("caddy", "validate", "--config", str(CADDY_MAIN), "--adapter", "caddyfile", timeout=30, capture=True)
    validate_mihomo(data["mihomo.yaml"])
    hy_smoke(settings)
    hy_smoke(settings, bad_password=True)
    check_subscription(xray, settings, data["mihomo.yaml"].encode())
    if state["phase"] == "final":
        old = f'https://{xray["SUB_DOMAIN"]}/s/{xray["SUB_TOKEN"]}/{xray["SUB_FILENAME"]}'
        if subscription_status(xray, old) != "404":
            raise RuntimeError("old Xray-only subscription is still available")
    print("001 Xray + HY services, HY TCP/UDP/auth, combined YAML and HTTPS subscription verified")


def install(config_path: Path) -> None:
    interrupted_settings = None
    if PENDING.exists():
        interrupted_settings = json.loads(PENDING.read_text()).get("settings")
        restore_pending()
    xray = load_xray_state(XRAY_STATE)
    if STATE.exists():
        old_state = read_state()
        if old_state["phase"] == "final":
            raise RuntimeError("already finalized; use check/show")
        saved = old_state["settings"]
    else:
        saved = interrupted_settings
    template = json.loads(config_path.read_text(encoding="utf-8"))
    settings = prepare_settings(template, saved)
    preflight(xray, settings)
    uid, hy_gid, caddy_gid = prepare_system()
    install_binaries()
    data = render(xray, settings, legacy=True)
    validate_mihomo(data["mihomo.yaml"])
    run("xray", "run", "-test", "-config", str(XRAY_CONFIG), timeout=30, capture=True)
    sub_file = SUB_DIR / settings["sub_filename"]
    if sub_file.exists() and saved is None:
        raise RuntimeError("new subscription file already exists and is unowned")
    paths = [CADDY_SNIPPET, HY_CONFIG, UNIT, sub_file, STATE]
    begin_change("install", paths, settings)
    try:
        atomic_write(CADDY_SNIPPET, data["caddy.caddy"].encode(), 0o640, 0, caddy_gid)
        atomic_write(HY_CONFIG, data["hysteria.yaml"].encode(), 0o640, 0, hy_gid)
        atomic_write(UNIT, data["hy2-mihomo.service"].encode(), 0o644)
        run("caddy", "validate", "--config", str(CADDY_MAIN), "--adapter", "caddyfile", timeout=30, capture=True)
        run("systemctl", "daemon-reload")
        run("systemctl", "reload", "caddy")
        run("systemctl", "enable", "--now", "hy2-mihomo.service")
        run("systemctl", "restart", "hy2-mihomo.service")
        for attempt in range(8):
            try:
                hy_smoke(settings)
                hy_smoke(settings, bad_password=True)
                break
            except Exception:
                if attempt == 7:
                    raise
                time.sleep(5)
        atomic_write(sub_file, data["mihomo.yaml"].encode(), 0o640, 0, caddy_gid)
        check_subscription(xray, settings, data["mihomo.yaml"].encode())
        state = {"version": 1, "phase": "staged", "settings": settings,
                 "xray_sha256": hashlib.sha256(XRAY_STATE.read_bytes()).hexdigest()}
        atomic_write(STATE, json.dumps(state, ensure_ascii=False, indent=2).encode(), 0o600)
        PENDING.unlink()
    except Exception:
        restore_pending()
        raise
    print("Staged new URL (keep private): " + subscription_url(xray, settings))


def finalize() -> None:
    if PENDING.exists():
        restore_pending()
    check()
    state = read_state()
    if state["phase"] == "final":
        print("Already finalized")
        return
    xray = load_xray_state(XRAY_STATE)
    settings = state["settings"]
    data = render(xray, settings, legacy=False)
    old_sub = SUB_DIR / "config.yaml"
    begin_change("finalize", [CADDY_SNIPPET, old_sub, STATE], settings)
    try:
        atomic_write(CADDY_SNIPPET, data["caddy.caddy"].encode(), 0o640, 0, grp.getgrnam("caddy").gr_gid)
        run("caddy", "validate", "--config", str(CADDY_MAIN), "--adapter", "caddyfile", timeout=30, capture=True)
        run("systemctl", "reload", "caddy")
        old_url = f'https://{xray["SUB_DOMAIN"]}/s/{xray["SUB_TOKEN"]}/{xray["SUB_FILENAME"]}'
        if subscription_status(xray, old_url) != "404":
            raise RuntimeError("old Xray-only URL still responds after cutover")
        check_subscription(xray, settings, data["mihomo.yaml"].encode())
        old_sub.unlink(missing_ok=True)
        state["phase"] = "final"
        atomic_write(STATE, json.dumps(state, ensure_ascii=False, indent=2).encode(), 0o600)
        PENDING.unlink()
    except Exception:
        restore_pending()
        raise
    print("Finalized single subscription: " + subscription_url(xray, settings))


def update_rules() -> None:
    if PENDING.exists():
        restore_pending()
    state = read_state()
    if state["phase"] != "final":
        raise RuntimeError("finish the single-subscription cutover before updating rules")
    xray = load_xray_state(XRAY_STATE)
    if hashlib.sha256(XRAY_STATE.read_bytes()).hexdigest() != state["xray_sha256"]:
        raise RuntimeError("Xray state drifted since cohost installation")
    settings = state["settings"]
    data = render(xray, settings, legacy=False)
    for path, key in ((CADDY_SNIPPET, "caddy.caddy"), (HY_CONFIG, "hysteria.yaml"),
                      (UNIT, "hy2-mihomo.service")):
        if not path.exists() or path.read_text(encoding="utf-8") != data[key]:
            raise RuntimeError(f"non-subscription configuration differs: {path}")
    for service in ("xray", "caddy", "hy2-mihomo.service"):
        run("systemctl", "is-active", "--quiet", service)
    sub_file = SUB_DIR / settings["sub_filename"]
    if not sub_file.is_file():
        raise RuntimeError("current subscription file is missing")
    candidate = data["mihomo.yaml"].encode()
    validate_mihomo(data["mihomo.yaml"])
    caddy_gid = grp.getgrnam("caddy").gr_gid
    snippet_stat = CADDY_SNIPPET.stat()
    private_snippet = (snippet_stat.st_mode & 0o777 == 0o640 and snippet_stat.st_uid == 0
                       and snippet_stat.st_gid == caddy_gid)
    if sub_file.read_bytes() == candidate and private_snippet:
        check()
        print("Client rules already current")
        return
    begin_change("update-rules", [sub_file, CADDY_SNIPPET], settings)
    try:
        atomic_write(CADDY_SNIPPET, data["caddy.caddy"].encode(), 0o640, 0, caddy_gid)
        atomic_write(sub_file, candidate, 0o640, 0, caddy_gid)
        check(allow_pending=True)
        PENDING.unlink()
    except Exception:
        restore_pending()
        raise
    print("Updated client routing rules at the existing subscription URL")


def network_settings(current: dict[str, str]) -> dict[str, str]:
    # quic-go recommends at least 7 MiB; preserve any larger operator limits.
    return {"net.core.rmem_max": str(max(7340032, int(current["net.core.rmem_max"]))),
            "net.core.wmem_max": str(max(7340032, int(current["net.core.wmem_max"]))),
            "net.ipv4.tcp_congestion_control": "bbr"}


def tune_network() -> None:
    if PENDING.exists():
        restore_pending()
    check()
    keys = ("net.core.rmem_max", "net.core.wmem_max", "net.ipv4.tcp_congestion_control")
    current = {key: run("sysctl", "-n", key, capture=True).stdout.strip() for key in keys}
    target = network_settings(current)
    run("modprobe", "tcp_bbr", capture=True)
    available = run("sysctl", "-n", "net.ipv4.tcp_available_congestion_control", capture=True).stdout.split()
    if "bbr" not in available:
        raise RuntimeError("kernel BBR is unavailable; no network settings changed")
    content = "# managed-by: cohost-proxy\n" + "".join(f"{key} = {value}\n" for key, value in target.items())
    if (current == target and NETWORK_SYSCTL.exists() and NETWORK_SYSCTL.read_text() == content
            and NETWORK_MODULES.exists() and NETWORK_MODULES.read_text() == "tcp_bbr\n"):
        print("Network tuning already current")
        return
    for path in (NETWORK_SYSCTL, NETWORK_MODULES):
        if path.exists() and path == NETWORK_SYSCTL and MARKER not in path.read_text():
            raise RuntimeError(f"refusing to overwrite unmanaged network config: {path}")
        if path.exists() and path == NETWORK_MODULES and path.read_text() != "tcp_bbr\n":
            raise RuntimeError(f"refusing to overwrite unmanaged module config: {path}")
    begin_change("tune-network", [NETWORK_SYSCTL, NETWORK_MODULES], read_state()["settings"])
    try:
        pending = json.loads(PENDING.read_text())
        pending["network_values"] = current
        atomic_write(Path(pending["backup"]).with_suffix(".runtime.json"), json.dumps(current).encode(), 0o600)
        atomic_write(PENDING, json.dumps(pending).encode(), 0o600)
        atomic_write(NETWORK_SYSCTL, content.encode(), 0o644)
        atomic_write(NETWORK_MODULES, b"tcp_bbr\n", 0o644)
        run("sysctl", "-p", str(NETWORK_SYSCTL), capture=True)
        actual = {key: run("sysctl", "-n", key, capture=True).stdout.strip() for key in keys}
        if actual != target:
            raise RuntimeError("kernel network settings did not take effect")
        # Recreate the TLS listener as well as QUIC sockets with the new defaults.
        run("systemctl", "restart", "caddy.service", "hy2-mihomo.service")
        check(allow_pending=True)
        PENDING.unlink()
    except Exception:
        restore_pending()
        raise
    print("TCP BBR and UDP buffer limits applied and persisted; rollback snapshot saved")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "check", "show", "finalize", "update-rules", "tune-network"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.example.json")
    args = parser.parse_args()
    require_server()
    with LOCK.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == "install":
            install(args.config)
        elif args.command == "check":
            check()
        elif args.command == "finalize":
            finalize()
        elif args.command == "update-rules":
            update_rules()
        elif args.command == "tune-network":
            tune_network()
        else:
            if PENDING.exists():
                raise RuntimeError("deployment is pending")
            state = read_state()
            print("phase=" + state["phase"])
            print(subscription_url(load_xray_state(XRAY_STATE), state["settings"]))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
