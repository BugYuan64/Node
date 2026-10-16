#!/usr/bin/env python3
"""Measure both subscription nodes with an isolated mihomo and anonymous curl."""

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

import yaml


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def measure(args):
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    proxy_port, api_port = free_port(), free_port()
    while proxy_port == api_port:
        api_port = free_port()
    secret = secrets.token_hex(32)
    config.update({"mixed-port": proxy_port, "external-controller": f"127.0.0.1:{api_port}",
                   "secret": secret, "tun": {"enable": False}, "allow-lan": False,
                   "bind-address": "127.0.0.1", "profile": {"store-selected": False},
                   "log-level": "warning"})
    if config.get("dns"):
        config["dns"]["listen"] = f"127.0.0.1:{free_port()}"
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def api(path, value=None):
        request = urllib.request.Request(f"http://127.0.0.1:{api_port}{path}",
                                         data=json.dumps(value).encode() if value else None,
                                         method="PUT" if value else "GET",
                                         headers={"Authorization": f"Bearer {secret}",
                                                  "Content-Type": "application/json"})
        with direct.open(request, timeout=3) as response:
            return json.loads(response.read() or b"null")

    targets = args.url or ["https://www.gstatic.com/generate_204",
                           "https://www.cloudflare.com/cdn-cgi/trace"]
    args.data_dir.mkdir(parents=True, exist_ok=True)
    result = {"targets": targets, "rounds": args.rounds, "nodes": {}}
    with tempfile.TemporaryDirectory(prefix="cohost-measure-") as temporary:
        path = Path(temporary) / "config.yaml"
        path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with (Path(temporary) / "core.log").open("wb") as log:
            process = subprocess.Popen([str(args.mihomo.resolve()), "-d", str(args.data_dir.resolve()),
                                        "-f", str(path)], stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            try:
                deadline = time.monotonic() + 45
                while True:
                    try:
                        api("/version")
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError("temporary mihomo failed to start")
                        time.sleep(0.2)
                for node in ("proxy-Xray", "proxy-HY"):
                    api("/proxies/PROXY", {"name": node})
                    samples = []
                    for iteration in range(args.rounds):
                        for target in targets:
                            response = subprocess.run(
                                ["curl.exe" if sys.platform == "win32" else "curl", "--silent", "--show-error",
                                 "--proxy", f"http://127.0.0.1:{proxy_port}", "--noproxy", "",
                                 "--connect-timeout", "6", "--max-time", "20", "-o", os.devnull,
                                 "-w", "%{http_code} %{time_starttransfer} %{time_total} %{size_download}", target],
                                capture_output=True, text=True, timeout=25)
                            values = response.stdout.split()
                            status = int(values[0]) if values else 0
                            sample = {"url": target, "round": iteration, "status": status,
                                      "ok": response.returncode == 0 and 200 <= status < 400,
                                      "ttfb": float(values[1]) if len(values) > 1 else None,
                                      "total": float(values[2]) if len(values) > 2 else None,
                                      "bytes": int(values[3]) if len(values) > 3 else 0,
                                      "error": response.stderr[-200:]}
                            samples.append(sample)
                    result["nodes"][node] = samples
                    for target in targets:
                        matched = [s for s in samples if s["url"] == target]
                        good = [s["total"] for s in matched if s["ok"]]
                        median = round(statistics.median(good) * 1000) if good else None
                        print(f"{node}: median={median}ms, successes={len(good)}/{len(matched)}, {target}", flush=True)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if any(not sample["ok"] for samples in result["nodes"].values() for sample in samples):
        raise RuntimeError("one or more requests failed; see measurement JSON")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--mihomo", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("build/measure-data"))
    parser.add_argument("--output", type=Path, default=Path("build/measurement.json"))
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--url", action="append", help="anonymous public test URL; repeat to test multiple targets")
    arguments = parser.parse_args()
    if not 1 <= arguments.rounds <= 100:
        parser.error("rounds must be between 1 and 100")
    measure(arguments)
