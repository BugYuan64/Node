"""Render the 001 Xray + Hysteria2 subscription and owned service files."""

from __future__ import annotations

import json
import ipaddress
from pathlib import Path
import re
import shlex
from urllib.parse import urlsplit


DOMAIN = re.compile(r"^(?=.{4,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")
UUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
FILENAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}\.yaml$")
STATE_KEYS = ("PROXY_DOMAIN", "SUB_DOMAIN", "NODE_NAME", "SUB_FILENAME", "XRAY_PORT", "VLESS_UUID", "SUB_TOKEN", "WS_TOKEN")
SETTING_KEYS = ("hy_domain", "hy_port", "hy_password", "sub_token", "sub_filename", "hy_node", "probe_url", "probe_interval", "probe_tolerance")
# Keep Android Google service/CDN exceptions ahead of broad China routing and DNS.
GOOGLE_PROXY_SUFFIXES = (
    "googleapis.cn", "google.cn", "google.com", "googleapis.com", "gstatic.com",
    "gvt0.com", "gvt1.com", "gvt2.com", "gvt3.com", "googleusercontent.com",
    "xn--ngstr-lra8j.com",
)


def quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def load_xray_state(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Xray state line {number}: expected KEY=value")
        key, raw = line.split("=", 1)
        if key in values:
            raise ValueError(f"duplicate Xray state key {key}")
        if raw.startswith("$'"):
            raise ValueError("Bash ANSI quoting is not supported in Xray state")
        parts = shlex.split(raw)
        values[key] = parts[0] if len(parts) == 1 else "" if not parts else None
        if values[key] is None:
            raise ValueError(f"Xray state line {number}: ambiguous value")
    for key in STATE_KEYS:
        if key not in values:
            raise ValueError(f"missing Xray state key {key}")
    for key in ("PROXY_DOMAIN", "SUB_DOMAIN"):
        if not DOMAIN.fullmatch(values[key]):
            raise ValueError(f"invalid Xray {key}")
    if values["PROXY_DOMAIN"] == values["SUB_DOMAIN"]:
        raise ValueError("Xray proxy and subscription domains collide")
    if not UUID.fullmatch(values["VLESS_UUID"]):
        raise ValueError("invalid Xray UUID")
    if not HEX64.fullmatch(values["SUB_TOKEN"]) or not HEX32.fullmatch(values["WS_TOKEN"]):
        raise ValueError("invalid Xray route tokens")
    if not FILENAME.fullmatch(values["SUB_FILENAME"]):
        raise ValueError("invalid Xray subscription filename")
    if not values["XRAY_PORT"].isdigit() or not 1024 <= int(values["XRAY_PORT"]) <= 65535:
        raise ValueError("invalid Xray loopback port")
    if not values["NODE_NAME"] or any(ord(c) < 32 for c in values["NODE_NAME"]):
        raise ValueError("invalid Xray node name")
    return values


def validate_settings(settings: dict) -> dict:
    if set(settings) != set(SETTING_KEYS):
        raise ValueError("cohost settings keys do not match the schema")
    result = dict(settings)
    if not isinstance(result["hy_domain"], str) or not DOMAIN.fullmatch(result["hy_domain"]):
        raise ValueError("invalid HY domain")
    if type(result["hy_port"]) is not int or not 1024 <= result["hy_port"] <= 65535 or result["hy_port"] in (443, 18080):
        raise ValueError("invalid HY UDP port")
    for key in ("hy_password", "sub_token"):
        if not isinstance(result[key], str) or not HEX64.fullmatch(result[key]):
            raise ValueError(f"invalid {key}")
        result[key] = result[key].lower()
    if result["hy_password"] == result["sub_token"]:
        raise ValueError("HY password and subscription token must differ")
    if not isinstance(result["sub_filename"], str) or not FILENAME.fullmatch(result["sub_filename"]):
        raise ValueError("invalid subscription filename")
    node = result["hy_node"]
    if not isinstance(node, str) or not 1 <= len(node) <= 64 or any(ord(c) < 32 for c in node) or node in {"DIRECT", "REJECT", "PROXY", "自动选择"}:
        raise ValueError("invalid HY node name")
    url = result["probe_url"]
    if not isinstance(url, str):
        raise ValueError("invalid test URL")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("test URL must be a plain HTTPS URL")
    if type(result["probe_interval"]) is not int or not 30 <= result["probe_interval"] <= 3600:
        raise ValueError("invalid test interval")
    if type(result["probe_tolerance"]) is not int or not 0 <= result["probe_tolerance"] <= 1000:
        raise ValueError("invalid test tolerance")
    return result


def subscription_url(xray: dict[str, str], settings: dict) -> str:
    return f'https://{xray["SUB_DOMAIN"]}/s/{settings["sub_token"]}/{settings["sub_filename"]}'


def sslip_ipv4(domain: str) -> str | None:
    match = re.search(r"(?:^|\.)([0-9]{1,3}(?:-[0-9]{1,3}){3})\.sslip\.io$", domain, re.IGNORECASE)
    if not match:
        return None
    return str(ipaddress.IPv4Address(match.group(1).replace("-", ".")))


def render(xray: dict[str, str], settings: dict, *, legacy: bool) -> dict[str, str]:
    settings = validate_settings(settings)
    hy_domain = settings["hy_domain"]
    x_domain = xray["PROXY_DOMAIN"]
    sub_domain = xray["SUB_DOMAIN"]
    if len({hy_domain.lower(), x_domain.lower(), sub_domain.lower()}) != 3:
        raise ValueError("all three domains must be distinct")
    if settings["sub_token"] == xray["SUB_TOKEN"] or settings["sub_filename"] == xray["SUB_FILENAME"]:
        raise ValueError("new subscription route must differ from the old one")
    if settings["hy_node"] == xray["NODE_NAME"] or xray["NODE_NAME"] in {"DIRECT", "REJECT", "PROXY", "自动选择"}:
        raise ValueError("proxy and group names collide")
    x_name = quote(xray["NODE_NAME"])
    hy_name = quote(settings["hy_node"])
    password = quote(settings["hy_password"])
    server_ip = sslip_ipv4(x_domain)
    server_ip_rule = f"  - IP-CIDR,{server_ip}/32,DIRECT,no-resolve\n" if server_ip else ""
    google_dns = "".join(f"    {quote('+.' + suffix)}: *foreign-dns\n" for suffix in GOOGLE_PROXY_SUFFIXES)
    google_rules = "".join(f"  - DOMAIN-SUFFIX,{suffix},PROXY\n" for suffix in GOOGLE_PROXY_SUFFIXES)
    hy_config = f'''listen: :{settings["hy_port"]}
acme:
  domains:
    - {quote(hy_domain)}
  ca: letsencrypt
  listenHost: 127.0.0.1
  dir: /var/lib/hy2-mihomo/acme
  type: http
  http:
    altPort: 18080
auth:
  type: password
  password: {password}
'''
    # HY 端点显式走 DIRECT 拨号器，避免默认拨号路径超时；业务流量仍经 HY，不固定本机网卡。
    mihomo = f'''mixed-port: 7890
allow-lan: false
bind-address: 127.0.0.1
mode: rule
log-level: warning
ipv6: false
tcp-concurrent: true
find-process-mode: 'off'
profile:
  store-selected: true
  store-fake-ip: true
geo-auto-update: true
geo-update-interval: 24
dns:
  enable: true
  listen: 127.0.0.1:1053
  ipv6: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  fake-ip-filter:
    - '*.lan'
    - '*.local'
    - '*.arpa'
    - '+.msftconnecttest.com'
    - '+.msftncsi.com'
  default-nameserver:
    - 223.5.5.5
    - 119.29.29.29
  proxy-server-nameserver:
    - https://dns.alidns.com/dns-query
    - https://doh.pub/dns-query
  direct-nameserver: &domestic-dns
    - https://dns.alidns.com/dns-query
    - https://doh.pub/dns-query
  nameserver: &foreign-dns
    - https://1.1.1.1/dns-query#PROXY
    - https://8.8.8.8/dns-query#PROXY
  nameserver-policy:
{google_dns}    'geosite:google': *foreign-dns
    'geosite:cn,private': *domestic-dns
    '+.cn': *domestic-dns
    '{x_domain}': *domestic-dns
    '{hy_domain}': *domestic-dns
    '{sub_domain}': *domestic-dns
proxies:
  - name: {x_name}
    type: vless
    server: {server_ip or x_domain}
    port: 443
    uuid: {xray["VLESS_UUID"]}
    network: ws
    tls: true
    udp: true
    servername: {x_domain}
    skip-cert-verify: false
    ws-opts:
      path: /tunnel/{xray["WS_TOKEN"]}
      headers:
        Host: {x_domain}
  - name: {hy_name}
    type: hysteria2
    dialer-proxy: DIRECT
    server: {sslip_ipv4(hy_domain) or hy_domain}
    port: {settings["hy_port"]}
    password: {password}
    sni: {hy_domain}
    skip-cert-verify: false
proxy-groups:
  - name: "自动选择"
    type: url-test
    proxies:
      - {hy_name}
      - {x_name}
    url: {quote(settings["probe_url"])}
    interval: {min(settings["probe_interval"], 60)}
    tolerance: {settings["probe_tolerance"]}
    lazy: false
    timeout: 4000
    max-failed-times: 2
    expected-status: 204
  - name: PROXY
    type: select
    proxies:
      - "自动选择"
      - {hy_name}
      - {x_name}
rules:
  - DOMAIN,{x_domain},DIRECT
  - DOMAIN,{hy_domain},DIRECT
  - DOMAIN,{sub_domain},DIRECT
{server_ip_rule}  - DOMAIN-SUFFIX,lan,DIRECT
  - DOMAIN-SUFFIX,local,DIRECT
  - DOMAIN-SUFFIX,arpa,DIRECT
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,169.254.0.0/16,DIRECT,no-resolve
  - IP-CIDR,100.64.0.0/10,DIRECT,no-resolve
  - IP-CIDR,224.0.0.0/4,DIRECT,no-resolve
  - IP-CIDR,240.0.0.0/4,DIRECT,no-resolve
  - IP-CIDR6,::1/128,DIRECT,no-resolve
  - IP-CIDR6,fc00::/7,DIRECT,no-resolve
  - IP-CIDR6,fe80::/10,DIRECT,no-resolve
  - GEOSITE,private,DIRECT
{google_rules}  - GEOSITE,google,PROXY
  - DOMAIN-SUFFIX,cn,DIRECT
  - DOMAIN,cn.bing.com,DIRECT
  - DOMAIN-SUFFIX,126.com,DIRECT
  - DOMAIN-SUFFIX,163.com,DIRECT
  - DOMAIN-SUFFIX,alicdn.com,DIRECT
  - DOMAIN-SUFFIX,alipay.com,DIRECT
  - DOMAIN-SUFFIX,amap.com,DIRECT
  - DOMAIN-SUFFIX,baidu.com,DIRECT
  - DOMAIN-SUFFIX,bdimg.com,DIRECT
  - DOMAIN-SUFFIX,bdstatic.com,DIRECT
  - DOMAIN-SUFFIX,bilibili.com,DIRECT
  - DOMAIN-SUFFIX,bilivideo.com,DIRECT
  - DOMAIN-SUFFIX,csdn.net,DIRECT
  - DOMAIN-SUFFIX,douban.com,DIRECT
  - DOMAIN-SUFFIX,doubanio.com,DIRECT
  - DOMAIN-SUFFIX,gtimg.com,DIRECT
  - DOMAIN-SUFFIX,jd.com,DIRECT
  - DOMAIN-SUFFIX,meituan.com,DIRECT
  - DOMAIN-SUFFIX,mi.com,DIRECT
  - DOMAIN-SUFFIX,qcloud.com,DIRECT
  - DOMAIN-SUFFIX,qq.com,DIRECT
  - DOMAIN-SUFFIX,taobao.com,DIRECT
  - DOMAIN-SUFFIX,tencent.com,DIRECT
  - DOMAIN-SUFFIX,tmall.com,DIRECT
  - DOMAIN-SUFFIX,weibo.com,DIRECT
  - DOMAIN-SUFFIX,zhihu.com,DIRECT
  - DOMAIN-SUFFIX,zhimg.com,DIRECT
  - GEOSITE,CN,DIRECT
  - GEOSITE,geolocation-!cn,PROXY
  - GEOIP,CN,DIRECT
  - MATCH,PROXY
'''
    old_route = f'''    handle /s/{xray["SUB_TOKEN"]}/{xray["SUB_FILENAME"]} {{
        root * /srv/mihomo-sub
        rewrite * /config.yaml
        header Content-Type "text/yaml; charset=utf-8"
        file_server
    }}
''' if legacy else ""
    caddy = f'''# managed-by: cohost-proxy
{x_domain} {{
    tls {{
        issuer acme {{
            disable_tlsalpn_challenge
        }}
    }}
    @xray_ws {{
        path /tunnel/{xray["WS_TOKEN"]}
        header Connection *Upgrade*
        header Upgrade websocket
    }}
    handle @xray_ws {{
        reverse_proxy 127.0.0.1:{xray["XRAY_PORT"]}
    }}
    handle {{
        respond "Not Found" 404
    }}
}}

http://{hy_domain} {{
    handle /.well-known/acme-challenge/* {{
        reverse_proxy 127.0.0.1:18080
    }}
    handle {{
        respond "Not Found" 404
    }}
}}

{sub_domain} {{
    tls {{
        issuer acme {{
            disable_tlsalpn_challenge
        }}
    }}
{old_route}    handle /s/{settings["sub_token"]}/{settings["sub_filename"]} {{
        root * /srv/mihomo-sub
        rewrite * /{settings["sub_filename"]}
        header {{
            Content-Type "text/yaml; charset=utf-8"
            Cache-Control "no-store"
            X-Content-Type-Options "nosniff"
        }}
        file_server
    }}
    handle {{
        respond "Not Found" 404
    }}
}}
'''
    return {"hysteria.yaml": hy_config, "mihomo.yaml": mihomo, "caddy.caddy": caddy,
            "hy2-mihomo.service": render_service_unit()}


def render_service_unit(*, coupled: bool = False) -> str:
    """The coupled variant is only for recognizing an existing migration source."""
    dependencies = ("Wants=network-online.target\nRequires=caddy.service" if coupled
                    else "Wants=network-online.target caddy.service")
    return f'''# managed-by: cohost-proxy
[Unit]
Description=Hysteria2 proxy on 001
After=network-online.target caddy.service
{dependencies}

[Service]
Type=simple
User=hy2proxy
Group=hy2proxy
ExecStart=/usr/local/lib/hy2-mihomo/hysteria server -c /etc/hy2-mihomo/hysteria.yaml
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/hy2-mihomo
PrivateTmp=true

[Install]
WantedBy=multi-user.target
'''
