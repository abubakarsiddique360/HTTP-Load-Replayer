"""Render one replay, or a whole concurrency sweep, without hiding the bad numbers."""

from __future__ import annotations

import json


def render_text(summary: dict, *, title: str = "Replay result") -> str:
    latency = summary["latency"]
    lines = [
        f"{title}",
        "=" * 66,
        f"target        {summary['base_url']}",
        f"requests      {summary['requests']:,}   concurrency {summary['concurrency']}",
        f"wall          {summary['wall_seconds']:.3f} s   "
        f"throughput {summary['throughput_rps']:.2f} req/s",
        f"failures      {summary['failures']:,} ({summary['error_rate']:.2f}%)",
        "",
        "Latency (ms)      avg      p50      p90      p95      p99      max",
        "             " + "".join(f"{latency[key]:>9.1f}" for key in
                                 ("avg", "p50", "p90", "p95", "p99", "max")),
        "",
        "Responses",
        "  " + "  ".join(f"{name}: {count}" for name, count in summary["classes"].items()),
    ]
    if summary["transport_errors"]:
        lines.append(
            "  transport: "
            + ", ".join(f"{name} x{count}" for name, count in summary["transport_errors"].items())
        )
    if summary["endpoints"]:
        lines.append("")
        lines.append(f"{'endpoint':<24}{'requests':>9}{'failures':>10}{'p50':>9}{'p95':>9}")
        for endpoint in summary["endpoints"]:
            lines.append(
                f"{endpoint['name'][:23]:<24}{endpoint['requests']:>9}"
                f"{endpoint['failures']:>10}{endpoint['p50']:>9.1f}{endpoint['p95']:>9.1f}"
            )
    checks = summary["sla"]["checks"]
    if checks:
        lines.append("")
        lines.append("SLA")
        for check in checks:
            state = "PASS" if check["passed"] else "FAIL"
            lines.append(
                f"  [{state}] {check['name']}: {check['observed']}{check['unit']} "
                f"(limit {check['limit']}{check['unit']})"
            )
    return "\n".join(lines)


def render_markdown(summary: dict, *, title: str = "Replay result") -> str:
    latency = summary["latency"]
    lines = [f"### {title}", ""]
    lines.append(f"Target `{summary['base_url']}`, concurrency {summary['concurrency']}.")
    lines.append("")
    lines.append("| Requests | Throughput | Failures | p50 | p95 | p99 |")
    lines.append("| ---: | ---: | ---: | ---: | ---: | ---: |")
    lines.append(
        f"| {summary['requests']:,} | {summary['throughput_rps']:.2f} req/s | "
        f"{summary['failures']:,} ({summary['error_rate']:.2f}%) | "
        f"{latency['p50']:.1f} ms | {latency['p95']:.1f} ms | {latency['p99']:.1f} ms |"
    )
    if summary["endpoints"]:
        lines.append("")
        lines.append("| Endpoint | Requests | Failures | p50 | p95 |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for endpoint in summary["endpoints"]:
            lines.append(
                f"| `{endpoint['name']}` | {endpoint['requests']} | {endpoint['failures']} | "
                f"{endpoint['p50']:.1f} ms | {endpoint['p95']:.1f} ms |"
            )
    checks = summary["sla"]["checks"]
    if checks:
        lines.append("")
        lines.append("| SLA | Limit | Observed | Result |")
        lines.append("| --- | ---: | ---: | --- |")
        for check in checks:
            lines.append(
                f"| {check['name']} | {check['limit']}{check['unit']} | "
                f"{check['observed']}{check['unit']} | "
                f"{'PASS' if check['passed'] else 'FAIL'} |"
            )
    return "\n".join(lines)


def render_json(summary: dict) -> str:
    return json.dumps(summary, indent=2, sort_keys=True)


def render_benchmark_text(rows: list[dict], *, title: str = "Concurrency sweep") -> str:
    lines = [
        title,
        "=" * 66,
        f"{'conc':>5}{'requests':>10}{'req/s':>10}{'p50 ms':>10}{'p95 ms':>10}{'errors':>9}",
    ]
    for row in rows:
        lines.append(
            f"{row['concurrency']:>5}{row['requests']:>10}{row['throughput_rps']:>10.1f}"
            f"{row['latency']['p50']:>10.1f}{row['latency']['p95']:>10.1f}"
            f"{row['error_rate']:>8.2f}%"
        )
    failures = [row for row in rows if row["sla"]["passed"] is False]
    if failures:
        lines.append("")
        lines.append(
            "SLA missed at concurrency: "
            + ", ".join(str(row["concurrency"]) for row in failures)
        )
    return "\n".join(lines)


def render_benchmark_markdown(rows: list[dict], *, title: str = "Concurrency sweep") -> str:
    lines = [f"### {title}", ""]
    lines.append("| Concurrency | Requests | Throughput | p50 | p95 | p99 | Error rate |")
    lines.append("| ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in rows:
        lines.append(
            f"| {row['concurrency']} | {row['requests']:,} | {row['throughput_rps']:.1f} req/s | "
            f"{row['latency']['p50']:.1f} ms | {row['latency']['p95']:.1f} ms | "
            f"{row['latency']['p99']:.1f} ms | {row['error_rate']:.2f}% |"
        )
    return "\n".join(lines)
