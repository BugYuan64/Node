#!/usr/bin/env python3
"""Audit project-controllable egress guardrails in a generated mihomo profile.

The audit is intentionally offline.  It uses the PyYAML dependency already
required by the repository's local checks, then checks the DNS, IPv6,
final-rule, and proxy-group invariants that this repository controls.

A resolver is considered proxy-routed only when its final YAML fragment is the
exact group name ``#PROXY``.  Other ``#<group>`` aliases are deliberately not
accepted automatically: accepting an arbitrary group name could hide a
DIRECT-containing path.  The generated profile uses ``#PROXY`` directly.

An optional ``egress-report/v3`` JSON is used only to attribute reported issue
points to the project, the client environment, or the egress itself.  Missing
report sections are ignored.  The tool never performs network requests and
does not claim that configuration changes can alter IP type or reputation.

Exit codes:
    0  profile guardrails passed
    1  input or parse error
    2  at least one project-controllable profile guardrail failed
    3  external findings present with ``--fail-on-external``
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

import yaml


TOOL_VERSION = 1
EXPECTED_SCHEMA = "egress-report/v3"
EXPECTED_GROUPS = ("自动选择", "PROXY")
PROXY_ROUTE_SUFFIX = "#PROXY"

EXIT_OK = 0
EXIT_INPUT = 1
EXIT_PROJECT_GUARDRAIL = 2
EXIT_EXTERNAL = 3

PROJECT_IDS = {
    "dns", "dns-leak", "dns-routing", "dns-split", "dns-mismatch",
    "split-dns", "ecs", "ecs-leak", "ipv6", "ipv6-leak", "ipv6-mismatch",
    "routing", "split-routing",
}
CLIENT_IDS = {
    "browser", "captcha", "client-env", "lang", "language",
    "language-mismatch", "local-ip", "localip", "recaptcha", "timezone",
    "timezone-mismatch", "turnstile", "tz", "webrtc", "webrtc-local",
}
EGRESS_IDS = {
    "abuse", "asn", "asn-bots", "blacklist", "blacklists", "bot-share",
    "broadcast", "dnsbl", "hosting", "reputation", "risk",
}
PROJECT_TERMS = ("dns", "ipv6", "ecs", "解析", "分流", "路由")
CLIENT_TERMS = (
    "timezone", "language", "webrtc", "turnstile", "captcha", "browser",
    "时区", "语言", "内网地址", "本地地址", "验证码", "浏览器",
)
EGRESS_TERMS = (
    "hosting", "broadcast", "blacklist", "reputation", "dnsbl", "risk", "bot",
    "asn", "abuse", "机房", "广播", "黑名单", "信誉", "风险", "机器流量",
    "滥用",
)
CATEGORY_CLASS = {
    "abuse": "egress",
    "anon": "egress",
    "geo": "egress",
    "live": "client",
    "neighbours": "egress",
    "network": "egress",
}
CLASS_LABELS = {
    "project": "配置相关",
    "client": "客户端环境",
    "egress": "出口与信誉",
}

LIMITATIONS = (
    "配置审计只能减少本项目配置引起的 DNS、IPv6 与分流泄漏，不能把机房 IP 变成住宅 IP。",
    "不能改变 ASN 机器流量占比、风险/黑名单记录、IP 注册地、广播定位或整体评级。",
    "不能改变 Turnstile 交互、浏览器时区/语言，也不能隐藏 WebRTC 暴露的本机候选地址。",
    "报告归因只说明问题类型，不能证明由当前 profile 引起；是否改善需重新实测出口报告。",
    "Profile 通过仅表示当前生成配置满足本项目护栏，不代表报告分数会上升。",
)


class ProfileFormatError(ValueError):
    """Raised when the profile YAML cannot be parsed or has the wrong root."""


def parse_profile(text: str) -> dict[str, Any]:
    """Parse a mihomo YAML profile using the repository's PyYAML dependency."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}" if mark is not None else ""
        problem = getattr(exc, "problem", None) or str(exc)
        raise ProfileFormatError(f"invalid YAML{location}: {problem}") from exc
    if not isinstance(document, dict):
        raise ProfileFormatError("profile root must be a mapping")
    return document


def load_profile(path: Path) -> dict[str, Any]:
    return parse_profile(path.read_text(encoding="utf-8"))


def _check(check_id: str, label: str, passed: bool, detail: str,
           remediation: str = "") -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "category": "project",
        "status": "pass" if passed else "fail",
        "detail": detail,
        "remediation": remediation,
    }


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return None
    return [item.strip() for item in value]


def _is_proxy_routed(resolver: str) -> bool:
    if "#" not in resolver:
        return False
    return resolver.rsplit("#", 1)[1] == "PROXY"


def _find_direct_path(name: str, groups: Mapping[str, list[str]],
                      stack: tuple[str, ...] = ()) -> list[str] | None:
    if name in stack:
        return None
    for member in groups.get(name, []):
        if member == "DIRECT":
            return [*stack, name, "DIRECT"]
        if member in groups:
            found = _find_direct_path(member, groups, (*stack, name))
            if found:
                return found
    return None


def audit_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Return project-controllable guardrail results for a parsed profile."""
    checks: list[dict[str, Any]] = []

    root_ipv6 = profile.get("ipv6")
    checks.append(_check(
        "root.ipv6",
        "根级 IPv6 已关闭",
        root_ipv6 is False,
        "ipv6: false" if root_ipv6 is False else f"实际为 {root_ipv6!r}",
        "在生成的 mihomo profile 中设置根级 ipv6: false。",
    ))

    dns = profile.get("dns")
    dns_mapping = dns if isinstance(dns, dict) else {}
    dns_ipv6 = dns_mapping.get("ipv6")
    checks.append(_check(
        "dns.ipv6",
        "DNS IPv6 已关闭",
        dns_ipv6 is False,
        "dns.ipv6: false" if dns_ipv6 is False else f"实际为 {dns_ipv6!r}",
        "在 dns 下设置 ipv6: false，避免解析与连接使用 AAAA 路径。",
    ))

    nameserver = _string_list(dns_mapping.get("nameserver"))
    if not nameserver:
        checks.append(_check(
            "dns.nameserver",
            "远端 DNS 全部经代理",
            False,
            "dns.nameserver 缺失、为空或包含非字符串条目",
            "提供非空的远端 DoH 列表，并为每一项追加精确的 #PROXY 路由标记。",
        ))
    else:
        bad = [index for index, value in enumerate(nameserver) if not _is_proxy_routed(value)]
        checks.append(_check(
            "dns.nameserver",
            "远端 DNS 全部经代理",
            not bad,
            "所有条目均以 #PROXY 结束" if not bad else f"{len(bad)} 个条目未使用精确的 #PROXY",
            "为每个 dns.nameserver 条目使用 #PROXY；代码不接受未经验证的其他组别名。",
        ))

    for key, check_id, label in (
        ("proxy-server-nameserver", "dns.proxy-server-nameserver", "连接 DNS 不经代理"),
        ("direct-nameserver", "dns.direct-nameserver", "直连 DNS 不经代理"),
    ):
        resolvers = _string_list(dns_mapping.get(key))
        if not resolvers:
            checks.append(_check(
                check_id,
                label,
                False,
                f"dns.{key} 缺失、为空或包含非字符串条目",
                f"提供非空的 dns.{key} 引导/直连解析器。",
            ))
            continue
        leaked = [index for index, value in enumerate(resolvers)
                  if PROXY_ROUTE_SUFFIX in value]
        checks.append(_check(
            check_id,
            label,
            not leaked,
            f"{len(resolvers)} 个条目均未使用 #PROXY" if not leaked
            else f"{len(leaked)} 个条目错误地使用了 #PROXY",
            f"从 dns.{key} 中移除 #PROXY，保持引导与直连解析不依赖代理组。",
        ))

    rules = profile.get("rules")
    final_rule = rules[-1] if isinstance(rules, list) and rules else None
    checks.append(_check(
        "rules.final",
        "最终规则为 MATCH,PROXY",
        isinstance(final_rule, str) and final_rule.strip() == "MATCH,PROXY",
        "最后一条规则是 MATCH,PROXY" if isinstance(final_rule, str) and
        final_rule.strip() == "MATCH,PROXY" else f"最后一条规则为 {final_rule!r}",
        "确保未匹配流量落到 PROXY，而不是 DIRECT 或其他出口。",
    ))

    groups_value = profile.get("proxy-groups")
    groups_list = groups_value if isinstance(groups_value, list) else []
    groups: dict[str, list[str]] = {}
    for group in groups_list:
        if not isinstance(group, dict) or not isinstance(group.get("name"), str):
            continue
        members = _string_list(group.get("proxies")) or []
        groups[group["name"]] = members
    proxy_members = groups.get("PROXY")
    if proxy_members is None:
        checks.append(_check(
            "groups.proxy.direct",
            "PROXY 组不含 DIRECT",
            False,
            "缺少 PROXY 组或组成员无效",
            "保留 PROXY 选择组，并仅放入代理节点或纯代理子组。",
        ))
    else:
        direct_path = _find_direct_path("PROXY", groups)
        checks.append(_check(
            "groups.proxy.direct",
            "PROXY 组不含 DIRECT",
            direct_path is None,
            "PROXY 组可达路径中没有 DIRECT" if direct_path is None
            else "存在路径 " + " -> ".join(direct_path),
            "从 PROXY 组及其子组移除 DIRECT 成员。",
        ))

    proxies = profile.get("proxies")
    proxy_nodes: list[Mapping[str, Any]] = []
    proxy_ok = isinstance(proxies, list) and len(proxies) >= 2
    if isinstance(proxies, list):
        for node in proxies:
            if (not isinstance(node, dict)
                    or not isinstance(node.get("name"), str)
                    or not node["name"]):
                proxy_ok = False
                break
            proxy_nodes.append(node)
        names = [str(node["name"]) for node in proxy_nodes]
        if len(set(names)) != len(names):
            proxy_ok = False
    checks.append(_check(
        "profile.proxies",
        "双协议代理节点完整",
        proxy_ok,
        f"检测到 {len(proxy_nodes)} 个具名代理节点" if proxy_ok
        else "proxies 少于两个、存在无效条目或节点名重复",
        "保留 Xray 与 Hysteria2 两个具名代理节点。",
    ))

    group_names = [group.get("name") for group in groups_list if isinstance(group, dict)]
    missing_groups = [name for name in EXPECTED_GROUPS if name not in group_names]
    expected_groups = [
        group for group in groups_list
        if isinstance(group, dict) and group.get("name") in EXPECTED_GROUPS
    ]
    groups_ok = not missing_groups and all(
        bool(_string_list(group.get("proxies"))) for group in expected_groups
    )
    checks.append(_check(
        "profile.groups",
        "预期代理分组完整",
        groups_ok,
        "包含非空的 自动选择 与 PROXY 组" if groups_ok
        else "缺少或为空的分组: " + (", ".join(missing_groups) if missing_groups
                                      else "自动选择/PROXY"),
        "保留自动选择与 PROXY 组，并确保它们都有代理成员。",
    ))

    failed = [check["id"] for check in checks if check["status"] == "fail"]
    return {
        "passed": not failed,
        "checks": checks,
        "failed_ids": failed,
    }


def _normalise_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _classify_text(value: str, terms: Sequence[str]) -> bool:
    lowered = value.casefold()
    return any(term.casefold() in lowered for term in terms)


def _classify(item_id: str, label: str, category_id: str) -> tuple[str, str]:
    token = _normalise_id(item_id)
    if token in PROJECT_IDS:
        return "project", "id"
    if token in CLIENT_IDS:
        return "client", "id"
    if token in EGRESS_IDS:
        return "egress", "id"
    if _classify_text(label, PROJECT_TERMS):
        return "project", "label"
    if _classify_text(label, CLIENT_TERMS):
        return "client", "label"
    if _classify_text(label, EGRESS_TERMS):
        return "egress", "label"
    category = _normalise_id(category_id)
    if category in CATEGORY_CLASS:
        return CATEGORY_CLASS[category], "category"
    return "egress", "default"


def classify_finding(item_id: str, label: str = "",
                     category_id: str = "") -> str:
    """Classify one report finding as ``project``, ``client``, or ``egress``."""
    return _classify(item_id, label, category_id)[0]


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _rounded(value: float) -> int | float:
    rounded = round(value, 2)
    return int(rounded) if rounded.is_integer() else rounded


def classify_report(report: Mapping[str, Any] | None,
                    path: str | Path | None = None) -> dict[str, Any]:
    """Classify report category items and sum their raw category points.

    The totals are report weights, not the report's final score; they only show
    where the listed issues would have to be addressed.
    """
    result: dict[str, Any] = {
        "provided": report is not None,
        "path": str(path) if path is not None else None,
        "schema": None,
        "score": None,
        "grade": None,
        "verdict": None,
        "findings": [],
        "point_totals": {"project": 0, "client": 0, "egress": 0},
        "counts": {"project": 0, "client": 0, "egress": 0},
        "external_findings_present": False,
        "warnings": [],
    }
    if report is None:
        return result
    if not isinstance(report, Mapping):
        raise ValueError("egress report root must be a JSON object")

    schema = report.get("schema")
    result["schema"] = schema if isinstance(schema, str) else None
    if schema is None:
        result["warnings"].append("报告缺少 schema 字段，仍尝试读取 categories。")
    elif schema != EXPECTED_SCHEMA:
        result["warnings"].append(
            f"报告 schema 为 {schema!r}，预期 {EXPECTED_SCHEMA!r}；仅作兼容性读取。"
        )

    score = report.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        result["score"] = _rounded(float(score))
    grade = report.get("grade")
    if isinstance(grade, dict) and isinstance(grade.get("letter"), str):
        result["grade"] = grade["letter"]
    elif isinstance(grade, str):
        result["grade"] = grade
    verdict = report.get("verdict")
    if isinstance(verdict, str):
        result["verdict"] = verdict

    categories = report.get("categories")
    if categories is None:
        result["warnings"].append("报告缺少 categories，未归因任何问题点。")
        categories = []
    elif not isinstance(categories, list):
        result["warnings"].append("报告 categories 不是数组，未归因任何问题点。")
        categories = []

    totals = {"project": 0.0, "client": 0.0, "egress": 0.0}
    counts = {"project": 0, "client": 0, "egress": 0}
    findings: list[dict[str, Any]] = []
    for category_index, category in enumerate(categories):
        if not isinstance(category, dict):
            result["warnings"].append(f"categories[{category_index}] 不是对象，已跳过。")
            continue
        category_id = str(category.get("id") or "")
        category_label = str(category.get("label") or category_id)
        items = category.get("items", [])
        if items is None:
            continue
        if not isinstance(items, list):
            result["warnings"].append(
                f"categories[{category_index}].items 不是数组，已跳过。"
            )
            continue
        for item_index, item in enumerate(items):
            if not isinstance(item, dict):
                result["warnings"].append(
                    f"categories[{category_index}].items[{item_index}] 不是对象，已跳过。"
                )
                continue
            item_id = str(item.get("id") or item.get("key") or f"item-{item_index}")
            label = str(item.get("label") or item.get("title") or item_id)
            points = _number(item.get("pts", item.get("points", 0)))
            attribution, basis = _classify(item_id, label, category_id)
            findings.append({
                "category": attribution,
                "category_label": category_label,
                "id": item_id,
                "label": label,
                "points": _rounded(points),
                "source": f"categories[{category_index}].items[{item_index}]",
                "basis": basis,
            })
            totals[attribution] += points
            counts[attribution] += 1

    result["findings"] = findings
    result["point_totals"] = {key: _rounded(value) for key, value in totals.items()}
    result["counts"] = counts
    result["external_findings_present"] = bool(counts["client"] or counts["egress"])
    if not findings and not result["warnings"]:
        result["warnings"].append("报告没有可归因的问题项。")
    return result


def build_result(profile_path: str | Path, profile: Mapping[str, Any],
                 report_path: str | Path | None = None,
                 report: Mapping[str, Any] | None = None) -> dict[str, Any]:
    profile_result = audit_profile(profile)
    report_result = classify_report(report, report_path)
    return {
        "tool": "egress_audit",
        "tool_version": TOOL_VERSION,
        "profile": str(profile_path),
        "profile_audit": profile_result,
        "report": report_result,
        "summary": {
            "profile_guardrails_passed": profile_result["passed"],
            "project_guardrail_failures": len(profile_result["failed_ids"]),
            "external_findings_present": report_result["external_findings_present"],
            "report_finding_counts": report_result["counts"],
        },
        "limitations": list(LIMITATIONS),
    }


def _format_points(value: int | float) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def format_text(result: Mapping[str, Any]) -> str:
    """Render a concise Chinese human-readable audit report."""
    profile = result["profile_audit"]
    report = result["report"]
    lines = [
        "出口纯净度项目侧审计",
        f"Profile: {result['profile']}",
        f"配置护栏: {'通过' if profile['passed'] else '失败'} "
        f"({len(profile['checks']) - len(profile['failed_ids'])}/{len(profile['checks'])})",
        "",
        "配置检查：",
    ]
    for check in profile["checks"]:
        marker = "通过" if check["status"] == "pass" else "失败"
        lines.append(f"- [{marker}] {check['label']}: {check['detail']}")
        if check["status"] == "fail" and check["remediation"]:
            lines.append(f"  修正: {check['remediation']}")

    lines.append("")
    if not report["provided"]:
        lines.append("报告归因：未提供 egress-report，仅执行 profile 检查。")
    else:
        lines.append(f"报告归因：{report['path']}")
        metadata = []
        if report["score"] is not None:
            metadata.append(f"分数 {report['score']}")
        if report["grade"]:
            metadata.append(f"评级 {report['grade']}")
        if report["schema"]:
            metadata.append(f"schema {report['schema']}")
        if metadata:
            lines.append("- " + "，".join(metadata))
        lines.append(
            "- 归因只说明问题类型，不代表已修复；报告权重合计不等于最终得分，"
            "也不能证明当前 profile 是原因。"
        )
        for attribution in ("project", "client", "egress"):
            classified = [item for item in report["findings"]
                          if item["category"] == attribution]
            lines.append(
                f"- {CLASS_LABELS[attribution]}: {report['counts'][attribution]} 项，"
                f"报告权重合计 {_format_points(report['point_totals'][attribution])} 分"
            )
            for item in classified:
                lines.append(
                    f"  - {item['label']} ({_format_points(item['points'])} 分; "
                    f"{item['source']})"
                )
        for warning in report["warnings"]:
            lines.append(f"- 警告: {warning}")

    lines.extend(["", "不可由本项目改善："])
    lines.extend(f"- {item}" for item in result["limitations"])
    if profile["passed"]:
        lines.extend(["", "结论: 当前 profile 通过项目侧护栏；外部问题仍需客户端或更换出口处理。"])
    else:
        lines.extend(["", "结论: 存在项目侧护栏失败，请修正并重新渲染 profile。"])
    return "\n".join(lines)


def _load_report(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("egress report root must be a JSON object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="离线审计 mihomo profile 的 DNS、IPv6 与分流护栏，并归因 egress-report 问题点。"
    )
    parser.add_argument("--profile", required=True, type=Path,
                        help="生成的 mihomo YAML profile")
    parser.add_argument("--report", type=Path,
                        help="可选的 egress-report/v3 JSON")
    parser.add_argument("--json", action="store_true",
                        help="输出机器可读 JSON")
    parser.add_argument("--fail-on-external", action="store_true",
                        help="报告含客户端或出口问题时返回退出码 3")
    args = parser.parse_args(argv)

    try:
        profile = load_profile(args.profile)
        report = _load_report(args.report) if args.report else None
        result = build_result(args.profile, profile, args.report, report)
    except (OSError, UnicodeError, ValueError) as exc:
        message = f"审计失败: {exc}"
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(message, file=sys.stderr)
        return EXIT_INPUT

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_text(result))

    if not result["profile_audit"]["passed"]:
        return EXIT_PROJECT_GUARDRAIL
    if args.fail_on_external and result["report"]["external_findings_present"]:
        return EXIT_EXTERNAL
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
