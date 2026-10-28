"""Regenerate README assets from a real concurrency sweep.

Usage:  python scripts/render_assets.py
Writes: docs/benchmark.json, docs/images/benchmark-report.png, docs/images/sweep.svg
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from http_load_replayer.mock import MockTarget  # noqa: E402
from http_load_replayer.plan import load_plan  # noqa: E402
from http_load_replayer.report import render_benchmark_text  # noqa: E402
from http_load_replayer.runner import replay  # noqa: E402
from http_load_replayer.stats import summarize  # noqa: E402

BG = "#0d1117"
CHROME = "#161b22"
FG = "#e6edf3"
MUTED = "#8b949e"
LEVELS = (1, 2, 4, 8)
REQUESTS = 60
ENDPOINT_COLORS = {
    "browse catalog": "#2f81f7",
    "search": "#d29922",
    "create order": "#3fb950",
    "checkout": "#f85149",
    "health": "#8b949e",
}


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"):
        candidate = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def render_terminal(text: str, out_path: Path, title: str) -> None:
    font = load_font(16)
    lines = text.expandtabs(4).splitlines() or [""]
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    char_width = probe.textlength("M", font=font)
    line_height = 22
    pad = 24
    width = int(max(len(line) for line in lines) * char_width) + pad * 2
    height = pad * 2 + len(lines) * line_height + 44
    image = Image.new("RGB", (max(width, 720), height), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, image.width, 34], fill=CHROME)
    for index, color in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        draw.ellipse([16 + index * 22, 10, 28 + index * 22, 22], fill=color)
    draw.text((92, 9), title, font=load_font(13), fill=MUTED)

    for row, line in enumerate(lines):
        color = FG
        if row == 0:
            color = "#79c0ff"
        elif line.strip().startswith("conc"):
            color = MUTED
        elif line.startswith("SLA missed"):
            color = "#f85149"
        elif ".00%" in line or line.strip().endswith("%") and "0.00%" in line:
            color = "#3fb950"
        draw.text((pad, 50 + row * line_height), line, font=font, fill=color)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)


def render_sweep(rows: list[dict], last_outcome, out_path: Path) -> None:
    conc = [row["concurrency"] for row in rows]
    throughput = [row["throughput_rps"] for row in rows]
    p95 = [row["latency"]["p95"] for row in rows]
    p50 = [row["latency"]["p50"] for row in rows]

    fig, (ax_sweep, ax_cdf) = plt.subplots(
        1, 2, figsize=(13.2, 4.6), gridspec_kw={"width_ratios": [1, 1.1]}
    )
    fig.patch.set_facecolor("#ffffff")

    ax_sweep.plot(conc, throughput, marker="o", color="#2f81f7", label="throughput (req/s)")
    ax_sweep.set_xlabel("concurrency")
    ax_sweep.set_ylabel("requests / second", color="#2f81f7")
    ax_sweep.tick_params(axis="y", labelcolor="#2f81f7")
    ax_sweep.set_xticks(conc)
    ax_sweep.set_title("Throughput scales, latency stays flat", fontsize=11, loc="left")
    ax_sweep.grid(linestyle=":", alpha=0.45)

    twin = ax_sweep.twinx()
    twin.plot(conc, p50, marker="s", linestyle="--", color="#3fb950", label="p50 latency")
    twin.plot(conc, p95, marker="^", linestyle="--", color="#d29922", label="p95 latency")
    twin.set_ylabel("latency (ms)")
    twin.set_ylim(0, max(max(p95), 1) * 1.25)
    lines = ax_sweep.get_lines() + twin.get_lines()
    ax_sweep.legend(lines, [line.get_label() for line in lines], fontsize=8, frameon=False,
                    loc="upper left")
    for axis in (ax_sweep, twin):
        for spine in ("top",):
            axis.spines[spine].set_visible(False)

    by_name: dict[str, list[float]] = {}
    for record in last_outcome.records:
        by_name.setdefault(record.name, []).append(record.latency_ms)
    for name, values in sorted(by_name.items()):
        ordered = sorted(values)
        total = len(ordered)
        points = [(index + 1) / total * 100 for index in range(total)]
        ax_cdf.step(ordered, points, where="post", linewidth=1.8,
                    color=ENDPOINT_COLORS.get(name, "#57606a"), label=name)
    ax_cdf.set_xlabel("latency (ms)")
    ax_cdf.set_ylabel("requests at or below (%)")
    ax_cdf.set_title(f"Latency CDF at concurrency {rows[-1]['concurrency']}", fontsize=11,
                     loc="left")
    ax_cdf.grid(linestyle=":", alpha=0.45)
    ax_cdf.legend(fontsize=8, frameon=False, loc="lower right")
    for spine in ("top", "right"):
        ax_cdf.spines[spine].set_visible(False)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, format=out_path.suffix.lstrip(".") or "svg", transparent=False)
    plt.close(fig)


def main() -> int:
    images = ROOT / "docs" / "images"
    images.mkdir(parents=True, exist_ok=True)
    plan = load_plan(ROOT / "examples" / "workload.jsonl")
    target = MockTarget()
    target.start()
    rows: list[dict] = []
    last_outcome = None
    try:
        for level in LEVELS:
            last_outcome = replay(plan, target.base_url, requests=REQUESTS, concurrency=level)
            rows.append(summarize(last_outcome, sla_p95_ms=150, sla_error_rate=10))
    finally:
        target.stop()

    (ROOT / "docs" / "benchmark.json").write_text(
        json.dumps({"plan": str(ROOT / "examples" / "workload.jsonl"), "levels": rows}, indent=2),
        encoding="utf-8",
    )
    header = (
        "load-replay benchmark --plan examples/workload.jsonl "
        "--requests 60 --concurrency 1,2,4,8 --sla-p95 150\n\n"
    )
    render_terminal(header + render_benchmark_text(rows), images / "benchmark-report.png",
                    "load-replay benchmark  -  loopback mock target")
    assert last_outcome is not None
    render_sweep(rows, last_outcome, images / "sweep.svg")
    print(
        "levels=" + ", ".join(f"{row['concurrency']}:{row['throughput_rps']}rps"
                              for row in rows)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
