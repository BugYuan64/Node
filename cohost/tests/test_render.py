import tempfile
import unittest
from pathlib import Path

import yaml

from cohost.render import load_xray_state, render, validate_settings


XRAY_TEXT = """PROXY_DOMAIN=proxy.13-193-30-180.sslip.io
SUB_DOMAIN=sub.13-193-30-180.sslip.io
NODE_NAME=proxy-Xray
SUB_FILENAME=proxy-Xray.yaml
XRAY_PORT=10000
VLESS_UUID=00000000-0000-4000-8000-000000000001
SUB_TOKEN=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
WS_TOKEN=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
XRAY_VERSION=''
CADDY_VERSION=''
"""

SETTINGS = {
    "hy_domain": "hy.13-193-30-180.sslip.io",
    "hy_port": 8443,
    "hy_password": "c" * 64,
    "sub_token": "d" * 64,
    "sub_filename": "proxy-001.yaml",
    "hy_node": "proxy-HY",
    "probe_url": "https://www.gstatic.com/generate_204",
    "probe_interval": 300,
    "probe_tolerance": 50,
}


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "deploy.env"
        self.state.write_text(XRAY_TEXT, encoding="utf-8")

    def test_both_nodes_and_two_groups_in_one_profile(self):
        data = render(load_xray_state(self.state), validate_settings(SETTINGS), legacy=False)
        config = yaml.safe_load(data["mihomo.yaml"])
        self.assertEqual([node["name"] for node in config["proxies"]], ["proxy-Xray", "proxy-HY"])
        self.assertEqual([node["type"] for node in config["proxies"]], ["vless", "hysteria2"])
        self.assertEqual(config["proxy-groups"][0]["name"], "自动选择")
        self.assertEqual(config["proxy-groups"][0]["type"], "url-test")
        self.assertEqual(config["proxy-groups"][0]["proxies"], ["proxy-HY", "proxy-Xray"])
        self.assertEqual(config["proxy-groups"][1]["name"], "PROXY")
        self.assertEqual(config["proxy-groups"][1]["proxies"], ["自动选择", "proxy-HY", "proxy-Xray"])
        self.assertEqual(config["rules"][-1], "MATCH,PROXY")
        self.assertFalse(config["proxies"][0]["skip-cert-verify"])
        self.assertFalse(config["proxies"][1]["skip-cert-verify"])

    def test_caddy_staging_preserves_legacy_and_final_removes_it(self):
        xray = load_xray_state(self.state)
        settings = validate_settings(SETTINGS)
        staged = render(xray, settings, legacy=True)["caddy.caddy"]
        final = render(xray, settings, legacy=False)["caddy.caddy"]
        self.assertIn("/tunnel/" + "b" * 32, final)
        self.assertIn("/.well-known/acme-challenge/*", final)
        self.assertIn("reverse_proxy 127.0.0.1:18080", final)
        self.assertIn("proxy-Xray.yaml", staged)
        self.assertNotIn("proxy-Xray.yaml", final)
        self.assertIn("proxy-001.yaml", final)

    def test_china_routes_direct_foreign_falls_back_and_proxy_path_has_no_cycle(self):
        config = yaml.safe_load(render(load_xray_state(self.state), validate_settings(SETTINGS), legacy=False)["mihomo.yaml"])
        rules = config["rules"]
        self.assertEqual(rules[:4], [
            "DOMAIN,proxy.13-193-30-180.sslip.io,DIRECT",
            "DOMAIN,hy.13-193-30-180.sslip.io,DIRECT",
            "DOMAIN,sub.13-193-30-180.sslip.io,DIRECT",
            "IP-CIDR,13.193.30.180/32,DIRECT,no-resolve",
        ])
        self.assertLess(rules.index("DOMAIN-SUFFIX,cn,DIRECT"), rules.index("GEOSITE,CN,DIRECT"))
        self.assertLess(rules.index("GEOSITE,CN,DIRECT"), rules.index("GEOIP,CN,DIRECT"))
        self.assertEqual(rules[-1], "MATCH,PROXY")
        groups = {group["name"]: group["proxies"] for group in config["proxy-groups"]}
        seen = set()

        def visit(group):
            self.assertNotIn(group, seen, "proxy group cycle")
            seen.add(group)
            for member in groups[group]:
                if member in groups:
                    visit(member)
            seen.remove(group)

        visit("PROXY")

    def test_fixed_ip_does_not_disable_tls_identity(self):
        config = yaml.safe_load(render(load_xray_state(self.state), validate_settings(SETTINGS), legacy=False)["mihomo.yaml"])
        xray, hy = config["proxies"]
        self.assertEqual(xray["server"], "13.193.30.180")
        self.assertEqual(hy["server"], "13.193.30.180")
        self.assertEqual(xray["servername"], "proxy.13-193-30-180.sslip.io")
        self.assertEqual(hy["sni"], SETTINGS["hy_domain"])
        self.assertFalse(xray["skip-cert-verify"])
        self.assertFalse(hy["skip-cert-verify"])

    def test_dns_separates_domestic_and_foreign_without_bootstrap_cycle(self):
        config = yaml.safe_load(render(load_xray_state(self.state), validate_settings(SETTINGS), legacy=False)["mihomo.yaml"])
        dns = config["dns"]
        self.assertTrue(dns["enable"])
        self.assertTrue(dns["listen"].startswith("127.0.0.1:"))
        self.assertTrue(all("#PROXY" in url for url in dns["nameserver"]))
        self.assertTrue(all("#PROXY" not in url for url in dns["proxy-server-nameserver"]))
        self.assertEqual(dns["nameserver-policy"]["geosite:cn,private"], dns["direct-nameserver"])

    def test_unknown_china_ip_can_resolve_and_shared_cdns_are_not_forced_direct(self):
        config = yaml.safe_load(render(load_xray_state(self.state), validate_settings(SETTINGS), legacy=False)["mihomo.yaml"])
        rules = config["rules"]
        self.assertIn("GEOIP,CN,DIRECT", rules)
        self.assertNotIn("DOMAIN-KEYWORD,-cn,DIRECT", rules)
        for suffix in ("akamaiedge.net", "cloudfront.net", "amazonaws.com", "edgekey.net"):
            self.assertNotIn(f"DOMAIN-SUFFIX,{suffix},DIRECT", rules)
        auto = config["proxy-groups"][0]
        self.assertLessEqual(auto["interval"], 60)
        self.assertLessEqual(auto["timeout"], 4000)
        self.assertEqual(auto["expected-status"], 204)

    def test_invalid_password_or_duplicate_token_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_settings(dict(SETTINGS, hy_password="short"))
        with self.assertRaises(ValueError):
            validate_settings(dict(SETTINGS, hy_password=SETTINGS["sub_token"]))


if __name__ == "__main__":
    unittest.main()
