import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cohost.egress_audit import (
    EXIT_EXTERNAL,
    EXIT_OK,
    EXIT_PROJECT_GUARDRAIL,
    audit_profile,
    classify_finding,
    classify_report,
    main,
    parse_profile,
)
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

REPORT = {
    "schema": "egress-report/v3",
    "score": 61,
    "grade": {"letter": "C"},
    "categories": [
        {
            "id": "live",
            "label": "连接观测",
            "items": [
                {"id": "dns", "label": "DNS 归属示例", "pts": 1.25},
                {"id": "tz", "label": "时区示例", "pts": 2.5},
                {"id": "lang", "label": "语言示例", "pts": 0.75},
                {"id": "webrtc-local", "label": "本机候选示例", "pts": 1.5},
                {"id": "turnstile", "label": "交互验证示例", "pts": 4},
            ],
        },
        {
            "id": "network",
            "label": "网络画像",
            "items": [
                {"id": "hosting", "label": "托管类型示例", "pts": 5.5},
            ],
        },
        {
            "id": "neighbours",
            "label": "邻居观测",
            "items": [
                {"id": "asn-bots", "label": "邻居流量示例", "pts": 6.25},
            ],
        },
        {
            "id": "abuse",
            "label": "滥用观测",
            "items": [
                {"id": "risk", "label": "风险记录示例", "pts": 2.75},
            ],
        },
        {
            "id": "geo",
            "label": "地理观测",
            "items": [
                {"id": "broadcast", "label": "注册定位示例", "pts": 1.25},
            ],
        },
    ],
}


class EgressAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        state = self.root / "deploy.env"
        state.write_text(XRAY_TEXT, encoding="utf-8")
        self.profile_text = render(
            load_xray_state(state), validate_settings(SETTINGS), legacy=False
        )["mihomo.yaml"]
        self.profile_path = self.root / "mihomo.yaml"
        self.profile_path.write_text(self.profile_text, encoding="utf-8")

    def audit_text(self, text):
        return audit_profile(parse_profile(text))

    def test_report_findings_and_points_are_attributed(self):
        result = classify_report(REPORT)
        by_source = {finding["source"]: finding for finding in result["findings"]}

        self.assertEqual(by_source["categories[0].items[0]"]["category"], "project")
        self.assertEqual(by_source["categories[0].items[1]"]["category"], "client")
        self.assertEqual(by_source["categories[0].items[3]"]["category"], "client")
        self.assertEqual(by_source["categories[0].items[4]"]["category"], "client")
        self.assertEqual(by_source["categories[1].items[0]"]["category"], "egress")
        self.assertEqual(by_source["categories[2].items[0]"]["category"], "egress")
        self.assertEqual(by_source["categories[3].items[0]"]["category"], "egress")
        self.assertEqual(by_source["categories[4].items[0]"]["category"], "egress")

        self.assertEqual(result["point_totals"], {
            "project": 1.25,
            "client": 8.75,
            "egress": 15.75,
        })
        self.assertEqual(result["counts"], {
            "project": 1,
            "client": 4,
            "egress": 4,
        })
        self.assertTrue(result["external_findings_present"])
        self.assertEqual(
            classify_finding("dns", "DNS 归属示例", "live"), "project"
        )
        self.assertEqual(
            classify_finding("webrtc-local", "本机候选示例", "live"), "client"
        )
        self.assertEqual(
            classify_finding("asn-bots", "邻居流量示例", "neighbours"), "egress"
        )

    def test_missing_report_sections_are_reported_as_warnings(self):
        result = classify_report({"schema": "egress-report/v3"})
        self.assertTrue(result["provided"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["point_totals"], {
            "project": 0,
            "client": 0,
            "egress": 0,
        })
        self.assertTrue(result["warnings"])

    def test_rendered_profile_passes_all_guardrails(self):
        result = self.audit_text(self.profile_text)
        self.assertTrue(result["passed"])
        self.assertEqual(result["failed_ids"], [])
        self.assertEqual(len(result["checks"]), 10)
        self.assertTrue(all(check["status"] == "pass" for check in result["checks"]))

    def test_google_public_dns_in_foreign_nameserver_is_flagged(self):
        for resolver in (
            "https://8.8.8.8/dns-query#PROXY",
            "https://8.8.4.4/dns-query#PROXY",
            "https://dns.google/dns-query#PROXY",
        ):
            with self.subTest(resolver=resolver):
                text = self.profile_text.replace(
                    "https://9.9.9.9/dns-query#PROXY", resolver, 1
                )
                result = self.audit_text(text)
                self.assertFalse(result["passed"])
                self.assertIn("dns.nameserver.region_safe", result["failed_ids"])

    def test_report_cross_region_dns_resolvers_and_details_are_surfaced(self):
        enriched = dict(REPORT)
        enriched["summary"] = {"country": "JP"}
        enriched["categories"] = [
            {
                "id": "live",
                "label": "连接实测",
                "items": [
                    {
                        "id": "dns",
                        "label": "DNS 跨区解析",
                        "pts": 3,
                        "detail": "公共 DNS 在 JP/US 解析，出口在 JP",
                    }
                ],
            }
        ]
        enriched["probes"] = {
            "dnsClassified": {
                "resolvers": [
                    {"org": "CloudFlare Inc", "country": "JP", "kind": "same"},
                    {"org": "Google LLC", "country": "US", "kind": "public"},
                    {"org": "Google LLC", "country": "US", "kind": "public"},
                    {"org": "Amazon.com Inc.", "country": "JP", "kind": "own"},
                ]
            }
        }
        result = classify_report(enriched)
        self.assertEqual(
            result["findings"][0]["detail"], "公共 DNS 在 JP/US 解析，出口在 JP"
        )
        self.assertEqual(
            result["dns_cross_region"],
            [{"org": "Google LLC", "country": "US", "kind": "public", "count": 2}],
        )

    def test_enabled_ipv6_is_rejected_at_root_and_dns(self):
        variants = {
            "root.ipv6": self.profile_text.replace(
                "\nipv6: false\n", "\nipv6: true\n", 1
            ),
            "dns.ipv6": self.profile_text.replace(
                "\n  ipv6: false\n", "\n  ipv6: true\n", 1
            ),
        }
        for expected, text in variants.items():
            with self.subTest(check=expected):
                result = self.audit_text(text)
                self.assertFalse(result["passed"])
                self.assertIn(expected, result["failed_ids"])

    def test_foreign_nameserver_requires_exact_proxy_route(self):
        lines = self.profile_text.splitlines()
        for index, line in enumerate(lines):
            if line.strip().startswith("- https://") and line.rstrip().endswith("#PROXY"):
                lines[index] = "    - https://dns.example.net/dns-query"
                break
        else:
            self.fail("rendered profile has no proxy-routed foreign nameserver")
        result = self.audit_text("\n".join(lines))
        self.assertFalse(result["passed"])
        self.assertIn("dns.nameserver", result["failed_ids"])

    def test_match_direct_is_rejected(self):
        text = self.profile_text.replace("  - MATCH,PROXY", "  - MATCH,DIRECT")
        result = self.audit_text(text)
        self.assertFalse(result["passed"])
        self.assertIn("rules.final", result["failed_ids"])

    def test_direct_member_in_proxy_group_is_rejected(self):
        heading = "  - name: PROXY\n    type: select\n    proxies:\n"
        self.assertIn(heading, self.profile_text)
        text = self.profile_text.replace(heading, heading + "      - DIRECT\n", 1)
        result = self.audit_text(text)
        self.assertFalse(result["passed"])
        self.assertIn("groups.proxy.direct", result["failed_ids"])

    def test_cli_exit_codes_and_json_output(self):
        report_path = self.root / "report.json"
        report_path.write_text(json.dumps(REPORT), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([
                "--profile", str(self.profile_path),
                "--report", str(report_path),
                "--json",
            ])
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["profile_audit"]["passed"])
        self.assertEqual(payload["report"]["point_totals"]["egress"], 15.75)

        broken = self.root / "broken.yaml"
        broken.write_text(
            self.profile_text.replace("  - MATCH,PROXY", "  - MATCH,DIRECT"),
            encoding="utf-8",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--profile", str(broken), "--json"])
        self.assertEqual(code, EXIT_PROJECT_GUARDRAIL)
        self.assertFalse(json.loads(output.getvalue())["profile_audit"]["passed"])

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([
                "--profile", str(self.profile_path),
                "--report", str(report_path),
                "--fail-on-external",
                "--json",
            ])
        self.assertEqual(code, EXIT_EXTERNAL)
        self.assertTrue(json.loads(output.getvalue())["summary"]
                        ["external_findings_present"])


if __name__ == "__main__":
    unittest.main()
