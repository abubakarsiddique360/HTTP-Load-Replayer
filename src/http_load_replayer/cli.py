"""Command line entry point for ``load-replay``."""

from __future__ import annotations

import argparse
import sys
import time

from . import __version__
from .mock import MockTarget
from .plan import PlanError, load_plan
from .report import (
    render_benchmark_markdown,
    render_benchmark_text,
    render_json,
    render_markdown,
    render_text,
)
from .runner import replay
from .stats import summarize


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="load-replay",
        description="Replay an HTTP request mix and report what came back.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_sla(command: argparse.ArgumentParser) -> None:
        command.add_argument("--sla-p95", type=float, help="fail when p95 latency exceeds this")
        command.add_argument("--sla-error-rate", type=float,
                             help="fail when the error rate exceeds this percentage")

    replay_cmd = sub.add_parser("replay", help="replay a plan against a running target")
    replay_cmd.add_argument("--plan", required=True)
    replay_cmd.add_argument("--url", required=True, help="http://host:port")
    replay_cmd.add_argument("--requests", type=int, default=100)
    replay_cmd.add_argument("--concurrency", type=int, default=4)
    replay_cmd.add_argument("--timeout", type=float, default=5.0)
    replay_cmd.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    add_sla(replay_cmd)

    bench = sub.add_parser("benchmark", help="sweep concurrency against a bundled mock target")
    bench.add_argument("--plan", default="examples/workload.jsonl")
    bench.add_argument("--requests", type=int, default=60, help="requests per level")
    bench.add_argument("--concurrency", default="1,2,4,8")
    bench.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    add_sla(bench)

    mock = sub.add_parser("mock", help="run the mock target on its own")
    mock.add_argument("--host", default="127.0.0.1")
    mock.add_argument("--port", type=int, default=8099)
    mock.add_argument("--duration", type=float, default=0,
                      help="stop after N seconds (0 = forever)")

    validate = sub.add_parser("validate", help="check a plan and show its weight mix")
    validate.add_argument("--plan", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "mock":
        target = MockTarget(args.host, args.port)
        target.start()
        print(f"mock target on {target.base_url} (Ctrl+C to stop)")
        try:
            if args.duration:
                time.sleep(args.duration)
            else:
                while True:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            target.stop()
            counts = ", ".join(f"{path}={count}" for path, count in target.counters().items())
            print(f"\nserved: {counts or 'nothing'}")
        return 0

    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        print(f"plan error: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        print(f"plan ok: {args.plan} ({len(plan.requests)} request templates, "
              f"total weight {plan.total_weight})")
        print(f"{'name':<24}{'method':<8}{'weight':>7}{'share':>8}  path")
        for row in plan.summary():
            print(f"{row['name'][:23]:<24}{row['method']:<8}{row['weight']:>7}"
                  f"{row['share']:>7.1f}%  {row['path']}")
        return 0

    sla_p95 = getattr(args, "sla_p95", None)
    sla_error = getattr(args, "sla_error_rate", None)

    if args.command == "replay":
        try:
            outcome = replay(
                plan,
                args.url,
                requests=args.requests,
                concurrency=args.concurrency,
                timeout=args.timeout,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        summary = summarize(outcome, sla_p95_ms=sla_p95, sla_error_rate=sla_error)
        if args.format == "json":
            print(render_json(summary))
        elif args.format == "markdown":
            print(render_markdown(summary))
        else:
            print(render_text(summary))
        return 1 if summary["sla"]["passed"] is False else 0

    if args.command == "benchmark":
        levels = _parse_levels(args.concurrency)
        if levels is None:
            print("error: --concurrency must be a comma separated list of positive integers",
                  file=sys.stderr)
            return 2
        target = MockTarget()
        target.start()
        rows: list[dict] = []
        try:
            for level in levels:
                outcome = replay(plan, target.base_url, requests=args.requests, concurrency=level)
                rows.append(summarize(outcome, sla_p95_ms=sla_p95, sla_error_rate=sla_error))
        finally:
            target.stop()

        if args.format == "json":
            print(render_json({"levels": rows}))
        elif args.format == "markdown":
            print(render_benchmark_markdown(rows))
        else:
            print(render_benchmark_text(rows))
        return 1 if any(row["sla"]["passed"] is False for row in rows) else 0

    return 2


def _parse_levels(raw: str) -> list[int] | None:
    levels: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or int(part) < 1:
            return None
        levels.append(int(part))
    return levels or None
