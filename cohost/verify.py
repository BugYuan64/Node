"""Real local TCP/UDP smoke tests through a temporary mihomo process."""

import http.client
import json
import os
import secrets
import socket
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    data = b""
    while len(data) < length:
        part = sock.recv(length - len(data))
        if not part:
            raise RuntimeError("SOCKS5 connection closed")
        data += part
    return data


def _assert_listener_owned(process: subprocess.Popen, port: int) -> None:
    """A stale process on the chosen port must never satisfy this smoke test."""
    fds = Path(f"/proc/{process.pid}/fd")
    owned = set()
    for fd in fds.iterdir():
        try:
            owned.add(os.readlink(fd))
        except OSError:
            continue
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        for row in table.read_text().splitlines()[1:]:
            fields = row.split()
            if fields[3] == "0A" and int(fields[1].split(":")[1], 16) == port:
                if f"socket:[{fields[9]}]" in owned:
                    return
    raise RuntimeError("temporary mihomo does not own its listening port")


def smoke(config: dict[str, str], mihomo: Path, *, bad_password: bool = False) -> None:
    """Force all test traffic through HY, including private loopback destinations."""
    marker = secrets.token_hex(16).encode("ascii")
    tcp_listener = socket.socket()
    udp_listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tcp_listener.bind(("127.0.0.1", 0))
    tcp_listener.listen(1)
    udp_listener.bind(("127.0.0.1", 0))
    tcp_listener.settimeout(12)
    udp_listener.settimeout(12)
    tcp_port = tcp_listener.getsockname()[1]
    udp_port = udp_listener.getsockname()[1]
    proxy_port = _free_port()
    done = threading.Event()

    def tcp_echo():
        try:
            with tcp_listener.accept()[0] as client:
                client.settimeout(5)
                client.recv(4096)
                client.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 32\r\nConnection: close\r\n\r\n" + marker)
        except OSError:
            pass

    def udp_echo():
        try:
            data, address = udp_listener.recvfrom(4096)
            if data == marker:
                udp_listener.sendto(marker, address)
        except OSError:
            pass
        finally:
            done.set()

    threading.Thread(target=tcp_echo, daemon=True).start()
    threading.Thread(target=udp_echo, daemon=True).start()
    password = secrets.token_hex(32) if bad_password else config["HY_PASSWORD"]
    temporary = f'''mixed-port: {proxy_port}
allow-lan: false
mode: rule
log-level: warning
proxies:
  - name: HY_TEST
    type: hysteria2
    server: 127.0.0.1
    port: {int(config["HY_PORT"])}
    password: {json.dumps(password)}
    sni: {config["PROXY_DOMAIN"]}
    skip-cert-verify: false
rules:
  - MATCH,HY_TEST
'''
    try:
        with tempfile.TemporaryDirectory(prefix="hy2-check-") as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(temporary, encoding="utf-8")
            subprocess.run([str(mihomo), "-t", "-d", directory, "-f", str(path)],
                           check=True, stdout=subprocess.DEVNULL, timeout=20)
            with open(Path(directory) / "mihomo.log", "wb") as log:
                process = subprocess.Popen([str(mihomo), "-d", directory, "-f", str(path)],
                                           stdout=log, stderr=subprocess.STDOUT)
                try:
                    for _ in range(60):
                        if process.poll() is not None:
                            raise RuntimeError("temporary mihomo exited before listening")
                        try:
                            with socket.create_connection(("127.0.0.1", proxy_port), timeout=0.2):
                                break
                        except OSError:
                            time.sleep(0.1)
                    else:
                        raise RuntimeError("temporary mihomo did not listen")
                    _assert_listener_owned(process, proxy_port)
                    connection = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=10)
                    try:
                        connection.request("GET", f"http://127.0.0.1:{tcp_port}/")
                        response = connection.getresponse()
                        body = response.read()
                        if bad_password:
                            if response.status == 200 and body == marker:
                                raise RuntimeError("incorrect password unexpectedly worked")
                            return
                        if response.status != 200 or body != marker:
                            raise RuntimeError("TCP proxy returned wrong response")
                    except (OSError, http.client.HTTPException):
                        if bad_password:
                            return
                        raise
                    finally:
                        connection.close()

                    with socket.create_connection(("127.0.0.1", proxy_port), timeout=5) as control:
                        control.settimeout(8)
                        control.sendall(b"\x05\x01\x00")
                        if _recv_exact(control, 2) != b"\x05\x00":
                            raise RuntimeError("SOCKS5 greeting failed")
                        control.sendall(b"\x05\x03\x00\x01" + socket.inet_aton("127.0.0.1") + b"\x00\x00")
                        reply = _recv_exact(control, 4)
                        if reply[:2] != b"\x05\x00" or reply[3] != 1:
                            raise RuntimeError("SOCKS5 UDP associate failed")
                        relay_ip = socket.inet_ntoa(_recv_exact(control, 4))
                        relay_port = struct.unpack("!H", _recv_exact(control, 2))[0]
                        if relay_ip == "0.0.0.0":
                            relay_ip = "127.0.0.1"
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                            udp.settimeout(8)
                            packet = b"\x00\x00\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", udp_port) + marker
                            udp.sendto(packet, (relay_ip, relay_port))
                            data, _ = udp.recvfrom(4096)
                            if not data.endswith(marker) or not done.wait(2):
                                raise RuntimeError("UDP proxy returned wrong response")
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
    finally:
        tcp_listener.close()
        udp_listener.close()
