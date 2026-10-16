#!/usr/bin/env python3
"""Verify the downloaded subscription with a real local mihomo process."""

import argparse
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def validate_graph(config: dict) -> None:
    nodes = {node["name"] for node in config["proxies"]}
    groups = {group["name"]: group["proxies"] for group in config["proxy-groups"]}
    if len(nodes) != len(config["proxies"]) or len(groups) != len(config["proxy-groups"]) or nodes & groups.keys():
        raise RuntimeError("duplicate proxy or group names")

    def visit(name: str, ancestors: set[str]) -> None:
        if name in ancestors:
            raise RuntimeError(f"proxy group cycle at {name}")
        for member in groups[name]:
            if member in groups:
                visit(member, ancestors | {name})
            elif member not in nodes | {"DIRECT", "REJECT"}:
                raise RuntimeError(f"undefined proxy target: {member}")

    for name in groups:
        visit(name, set())
    if groups.get("自动选择") != ["proxy-HY", "proxy-Xray"]:
        raise RuntimeError("unexpected automatic group members")
    if groups.get("PROXY") != ["自动选择", "proxy-HY", "proxy-Xray"]:
        raise RuntimeError("unexpected PROXY group members")
    if config["mode"] != "rule" or config["rules"][-1] != "MATCH,PROXY":
        raise RuntimeError("foreign fallback must use PROXY in rule mode")


def verify(config_path: Path, mihomo: Path, data_dir: Path, expected_ip: str,
           unavailable_node: str | None = None) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    validate_graph(config)
    if unavailable_node:
        node = next(node for node in config["proxies"] if node["name"] == unavailable_node)
        node.update({"server": "127.0.0.1", "port": free_port()})
    config.setdefault("hosts", {})["cohost-cn-route-check-20261016.net"] = "223.5.5.5"
    proxy_port, api_port = free_port(), free_port()
    while api_port == proxy_port:
        api_port = free_port()
    secret = secrets.token_hex(32)
    config.update({"mixed-port": proxy_port, "allow-lan": False, "bind-address": "127.0.0.1",
                   "external-controller": f"127.0.0.1:{api_port}", "secret": secret,
                   "tun": {"enable": False}, "profile": {"store-selected": False}})
    if config.get("dns"):
        config["dns"]["listen"] = f"127.0.0.1:{free_port()}"
    data_dir.mkdir(parents=True, exist_ok=True)
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    proxy_url = f"http://127.0.0.1:{proxy_port}"
    via = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))

    def api(path: str, value: dict | None = None):
        request = urllib.request.Request(f"http://127.0.0.1:{api_port}{path}",
                                         data=json.dumps(value).encode() if value is not None else None,
                                         method="PUT" if value is not None else "GET",
                                         headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"})
        with direct.open(request, timeout=12) as response:
            body = response.read()
            return json.loads(body) if body else None

    def counts():
        return {(rule["type"], rule["payload"].lower()): (rule["proxy"], rule["extra"]["hitCount"])
                for rule in api("/rules")["rules"]}

    with tempfile.TemporaryDirectory(prefix="proxy-client-check-") as temporary:
        path = Path(temporary) / "config.yaml"
        path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        log_path = Path(temporary) / "mihomo.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen([str(mihomo), "-d", str(data_dir), "-f", str(path)],
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            try:
                deadline = time.monotonic() + 45
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("mihomo exited before startup: " + log_path.read_text(errors="replace")[-1200:])
                    try:
                        api("/rules")
                        break
                    except (OSError, urllib.error.URLError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError("mihomo controller startup timeout")
                        time.sleep(0.2)

                if unavailable_node:
                    api("/proxies/PROXY", {"name": "自动选择"})
                    group = urllib.parse.quote("自动选择", safe="")
                    api(f"/group/{group}/delay?url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204&timeout=4000")
                    survivor = "proxy-Xray" if unavailable_node == "proxy-HY" else "proxy-HY"
                    if api(f"/proxies/{group}")["now"] != survivor:
                        raise RuntimeError("automatic group did not select the healthy node")
                    with via.open("https://api.ipify.org?format=json", timeout=20) as response:
                        if json.load(response)["ip"] != expected_ip:
                            raise RuntimeError("failover did not preserve proxy exit")
                    print(f"Isolated failure {unavailable_node}: automatic -> {survivor}, exit={expected_ip}")
                    return

                for domain in ("www.baidu.com", "www.google.com"):
                    answer = api("/dns/query?name=" + domain + "&type=A")
                    if answer.get("Status") != 0 or not answer.get("Answer"):
                        raise RuntimeError(f"DNS resolution failed: {domain}")
                    print(f"DNS query: {domain} OK")

                sub_domain = next(rule.split(",")[1] for rule in config["rules"]
                                  if rule.startswith("DOMAIN,sub.") and rule.endswith(",DIRECT"))
                probes = [
                    ("CN domain", "http://www.baidu.com/", ("DomainSuffix", "baidu.com"), True),
                    ("subscription domain", f"https://{sub_domain}/", ("Domain", sub_domain.lower()), True),
                    ("server IP", f"http://{expected_ip}/", ("IPCIDR", expected_ip + "/32"), False),
                    ("CN IP", "http://223.5.5.5/", ("GeoIP", "cn"), False),
                    ("unknown CN domain", "http://cohost-cn-route-check-20261016.net/", ("GeoIP", "cn"), False),
                ]
                for label, url, key, require_response in probes:
                    before = counts()
                    try:
                        with via.open(url, timeout=10 if require_response else 3) as response:
                            response.read(64)
                    except urllib.error.HTTPError:
                        pass  # A real HTTP error also proves that the destination was reached.
                    except (OSError, urllib.error.URLError):
                        if require_response:
                            raise
                    route, hits = counts()[key]
                    if route != "DIRECT" or hits <= before[key][1]:
                        raise RuntimeError(f"{label} did not hit DIRECT")
                    print(f"{label}: DIRECT")

                for node in ("proxy-Xray", "proxy-HY"):
                    api("/proxies/PROXY", {"name": node})
                    if api("/proxies/PROXY")["now"] != node:
                        raise RuntimeError(f"node selection failed: {node}")
                    before = counts()
                    with via.open("https://api.ipify.org?format=json", timeout=20) as response:
                        address = json.load(response)["ip"]
                    after = counts()
                    matched = any(policy == "PROXY" and count > before[key][1]
                                  for key, (policy, count) in after.items())
                    if not matched or address != expected_ip:
                        raise RuntimeError(f"unexpected proxy exit for {node}: {address}")
                    print(f"{node}: PROXY, exit={address}")
                print("Proxy group graph: no cycles")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--mihomo", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("build/mihomo"))
    parser.add_argument("--expected-ip", required=True)
    parser.add_argument("--unavailable-node", choices=("proxy-Xray", "proxy-HY"),
                        help="simulate one failed endpoint in the isolated test process only")
    args = parser.parse_args()
    verify(args.config.resolve(), args.mihomo.resolve(), args.data_dir.resolve(), args.expected_ip,
           args.unavailable_node)
