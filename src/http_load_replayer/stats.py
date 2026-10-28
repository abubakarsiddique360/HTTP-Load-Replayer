"""Turn raw replay records into the numbers people actually argue about."""

from __future__ import annotations

from collections import Counter, defaultdict

from .runner import ReplayOutcome


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 3)


def classify(status: int | None) -> str:
    if status is None:
        return "transport"
    if status < 300:
        return "2xx"
    if status < 400:
        return "3xx"
    if status < 500:
        return "4xx"
    return "5xx"


def summarize(outcome: ReplayOutcome, *, sla_p95_ms: float | None = None,
              sla_error_rate: float | None = None) -> dict:
    records = outcome.records
    latencies = [record.latency_ms for record in records if record.status is not None]
    classes = Counter(classify(record.status) for record in records)
    failures = sum(1 for record in records if not record.ok)
    error_rate = round(failures / len(records) * 100, 2) if records else 0.0

    per_endpoint: dict[str, dict] = defaultdict(
        lambda: {"requests": 0, "failures": 0, "latencies": []}
    )
    for record in records:
        bucket = per_endpoint[record.name]
        bucket["requests"] += 1
        bucket["latencies"].append(record.latency_ms)
        if not record.ok:
            bucket["failures"] += 1

    endpoints = []
    for name, bucket in sorted(per_endpoint.items()):
        endpoints.append(
            {
                "name": name,
                "requests": bucket["requests"],
                "failures": bucket["failures"],
                "p50": percentile(bucket["latencies"], 0.5),
                "p95": percentile(bucket["latencies"], 0.95),
            }
        )

    summary = {
        "base_url": outcome.base_url,
        "concurrency": outcome.concurrency,
        "requests": len(records),
        "wall_seconds": outcome.wall_seconds,
        "throughput_rps": round(len(records) / outcome.wall_seconds, 2)
        if outcome.wall_seconds
        else 0.0,
        "failures": failures,
        "error_rate": error_rate,
        "classes": dict(sorted(classes.items())),
        "statuses": {str(status): count for status, count in sorted(outcome.statuses.items())},
        "transport_errors": dict(outcome.errors),
        "latency": {
            "sample": len(latencies),
            "avg": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
            "p50": percentile(latencies, 0.5),
            "p90": percentile(latencies, 0.9),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "endpoints": endpoints,
    }
    summary["sla"] = _evaluate(summary, sla_p95_ms, sla_error_rate)
    return summary


def _evaluate(summary: dict, sla_p95_ms: float | None, sla_error_rate: float | None) -> dict:
    checks = []
    if sla_p95_ms is not None:
        checks.append(
            {
                "name": "p95 latency",
                "limit": sla_p95_ms,
                "observed": summary["latency"]["p95"],
                "unit": "ms",
                "passed": summary["latency"]["p95"] <= sla_p95_ms,
            }
        )
    if sla_error_rate is not None:
        checks.append(
            {
                "name": "error rate",
                "limit": sla_error_rate,
                "observed": summary["error_rate"],
                "unit": "%",
                "passed": summary["error_rate"] <= sla_error_rate,
            }
        )
    passed = all(check["passed"] for check in checks) if checks else None
    return {"checks": checks, "passed": passed}
