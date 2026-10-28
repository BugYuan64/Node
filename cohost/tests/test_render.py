import tempfile
import unittest
from pathlib import Path

import yaml

from cohost.render import load_xray_state, render, validate_settings


XRAY_TEXT = """PROXY_DOMAIN=proxy.example.com
SUB_DOMAIN=sub.example.com
NODE_NAME=proxy-Xray
SUB_FILENAME=proxy-Xray.yaml
XRAY_PORT=10000
SERVER_IP=192.0.2.10
VLESS_UUID=00000000-0000-4000-8000-000000000001
SUB_TOKEN=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
WS_TOKEN=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
XRAY_VERSION=''
CADDY_VERSION=''
"""

SETTINGS = {
    "hy_domain": "hy.example.com",
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
    def test_custom_domain_uses_explicit_endpoint_and_keeps_tls_identity(self):
        self.state.write_text(XRAY_TEXT, encoding='utf-8')
        xray = load_xray_state(self.state)
        xray.update(PROXY_DOMAIN='proxy.example.com', SUB_DOMAIN='sub.example.com')
        settings = dict(SETTINGS, hy_domain='hy.example.com')
        config = yaml.safe_load(render(xray, settings, legacy=False)['mihomo.yaml'])
        self.assertEqual([n['server'] for n in config['proxies']], ['192.0.2.10'] * 2)
        self.assertEqual(config['proxies'][0]['servername'], 'proxy.example.com')
        self.assertEqual(config['proxies'][1]['sni'], 'hy.example.com')
        self.assertIn('IP-CIDR,192.0.2.10/32,DIRECT,no-resolve', config['rules'])

    def test_optional_root_domain_gets_valid_https_without_exposing_subscription(self):
        self.state.write_text(XRAY_TEXT + 'ROOT_DOMAIN=example.com\n', encoding='utf-8')
        data = render(load_xray_state(self.state), SETTINGS, legacy=False)
        self.assertIn('\nexample.com {\n', data['caddy.caddy'])
        root = data['caddy.caddy'].split('\nexample.com {\n', 1)[1]
        self.assertNotIn('/s/', root)
        self.assertNotIn('file_server', root)
        self.assertIn('404', root)

    def test_endpoint_validation_rejects_fake_ip_and_non_ipv4_input(self):
        for invalid in ('198.18.1.2', 'example.com', '::1', '0.0.0.0', '127.0.0.1'):
            with self.subTest(invalid=invalid):
                self.state.write_text(XRAY_TEXT.replace('SERVER_IP=192.0.2.10', f'SERVER_IP={invalid}'), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_xray_state(self.state)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "deploy.env"
        self.state.write_text(XRAY_TEXT, encoding="utf-8")

    def test_hy_unit_weakly_starts_caddy_and_preserves_acme_route(self):
        data = render(load_xray_state(self.state), validate_settings(SETTINGS), legacy=False)
        unit = data["hy2-mihomo.service"]
        self.assertIn("After=network-online.target caddy.service\n", unit)
        self.assertIn("Wants=network-online.target caddy.service\n", unit)
        self.assertNotIn("Requires=", unit)
        self.assertNotIn("BindsTo=", unit)
        self.assertNotIn("PartOf=", unit)
        self.assertIn("reverse_proxy 127.0.0.1:18080", data["caddy.caddy"])
        acme = yaml.safe_load(data["hysteria.yaml"])["acme"]
        self.assertEqual(acme["listenHost"], "127.0.0.1")
        self.assertEqual(acme["http"]["altPort"], 18080)
        self.assertEqual(acme["dir"], "/var/lib/hy2-mihomo/acme")

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

    def test_hy_transport_uses_direct_dialer_without_pinning_a_network_interface(self):
        xray = load_xray_state(self.state)
        for legacy in (False, True):
            for name in ("proxy-HY", "hy-custom"):
                with self.subTest(legacy=legacy, node=name):
                    settings = dict(SETTINGS, hy_node=name)
                    config = yaml.safe_load(render(xray, settings, legacy=legacy)["mihomo.yaml"])
                    nodes = {node["name"]: node for node in config["proxies"]}
                    hy = nodes[name]
                    self.assertEqual(hy.get("dialer-proxy"), "DIRECT")
                    self.assertNotIn("dialer-proxy", nodes[xray["NODE_NAME"]])
                    self.assertNotIn("interface-name", hy)
                    self.assertNotIn("interface-name", config)

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
            "DOMAIN,proxy.example.com,DIRECT",
            "DOMAIN,hy.example.com,DIRECT",
            "DOMAIN,sub.example.com,DIRECT",
            "IP-CIDR,192.0.2.10/32,DIRECT,no-resolve",
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
        self.assertEqual(xray["server"], "192.0.2.10")
        self.assertEqual(hy["server"], "192.0.2.10")
        self.assertEqual(xray["servername"], "proxy.example.com")
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

    def test_google_play_routes_before_china_and_ip_fallbacks(self):
        config = yaml.safe_load(render(load_xray_state(self.state), SETTINGS, legacy=False)["mihomo.yaml"])
        rules = config["rules"]
        # Android's China service endpoint must not hit the broad .cn DIRECT rule.
        for suffix in ("googleapis.cn", "google.cn", "google.com", "googleapis.com",
                       "gstatic.com", "gvt0.com", "gvt1.com", "gvt2.com", "gvt3.com",
                       "googleusercontent.com", "xn--ngstr-lra8j.com"):
            rule = f"DOMAIN-SUFFIX,{suffix},PROXY"
            self.assertIn(rule, rules)
            self.assertLess(rules.index(rule), rules.index("DOMAIN-SUFFIX,cn,DIRECT"))
        self.assertLess(rules.index("GEOSITE,google,PROXY"), rules.index("GEOSITE,CN,DIRECT"))

    def test_google_dns_exceptions_precede_domestic_policy(self):
        config = yaml.safe_load(render(load_xray_state(self.state), SETTINGS, legacy=False)["mihomo.yaml"])
        dns = config["dns"]
        policies = dns["nameserver-policy"]
        keys = list(policies)
        for suffix in ("googleapis.cn", "google.cn", "google.com", "googleapis.com",
                       "gstatic.com", "gvt0.com", "gvt1.com", "gvt2.com", "gvt3.com",
                       "googleusercontent.com", "xn--ngstr-lra8j.com"):
            key = f"+.{suffix}"
            self.assertIn(key, policies)
            self.assertEqual(policies[key], dns["nameserver"])
            self.assertLess(keys.index(key), keys.index("geosite:cn,private"))
        self.assertEqual(policies["geosite:google"], dns["nameserver"])
        self.assertLess(keys.index("geosite:google"), keys.index("geosite:cn,private"))

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
