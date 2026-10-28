import json

import pytest

from http_load_replayer.cli import main
from http_load_replayer.report import (
    render_benchmark_markdown,
    render_benchmark_text,
    render_json,
    render_markdown,
    render_text,
)
from http_load_replayer.runner import ReplayOutcome, RequestRecord
from http_load_replayer.stats import summarize


def outcome():
    records = [
        RequestRecord("browse", "GET", "/api/catalog", 200, 12.5),
        RequestRecord("browse", "GET", "/api/catalog", 200, 18.0),
        RequestRecord("search", "GET", "/api/search", 429, 41.0),
        RequestRecord("checkout", "POST", "/api/checkout", 500, 62.0),
        RequestRecord("dead", "GET", "/api/dead", None, 3.0, "connect:ConnectionRefusedError"),
    ]
    return ReplayOutcome(records=records, wall_seconds=0.25, concurrency=2,
                         base_url="http://127.0.0.1:9000")


def test_text_report_contains_every_section():
    summary = summarize(outcome(), sla_p95_ms=100, sla_error_rate=50)
    text = render_text(summary)
    assert "throughput" in text
    assert "Latency (ms)" in text
    assert "Responses" in text
    assert "transport" in text
    assert "SLA" in text
    assert "[PASS]" in text


def test_text_report_exposes_failures():
    summary = summarize(outcome(), sla_p95_ms=1, sla_error_rate=1)
    assert "[FAIL] p95 latency" in render_text(summary)
    assert "FAIL" in render_markdown(summary)


def test_markdown_report_tables():
    summary = summarize(outcome())
    markdown = render_markdown(summary)
    assert "| Requests | Throughput | Failures | p50 | p95 | p99 |" in markdown
    assert "| Endpoint | Requests | Failures | p50 | p95 |" in markdown
    assert "`browse`" in markdown


def test_json_report_round_trips():
    summary = summarize(outcome())
    payload = json.loads(render_json(summary))
    assert payload["requests"] == 5
    assert payload["classes"] == {"2xx": 2, "4xx": 1, "5xx": 1, "transport": 1}
    assert payload["sla"]["passed"] is None


def test_benchmark_renderers():
    rows = [summarize(outcome()) for _ in range(3)]
    text = render_benchmark_text(rows)
    assert "Concurrency sweep" in text
    markdown = render_benchmark_markdown(rows)
    assert "| Concurrency | Requests |" in markdown


def test_cli_validate(tmp_path, capsys):
    plan = tmp_path / "plan.jsonl"
    plan.write_text('{"name": "a", "path": "/api/catalog", "weight": 2}\n', encoding="utf-8")
    assert main(["validate", "--plan", str(plan)]) == 0
    output = capsys.readouterr().out
    assert "plan ok" in output
    assert "a" in output


def test_cli_validate_missing_plan(tmp_path, capsys):
    assert main(["validate", "--plan", str(tmp_path / "no.jsonl")]) == 2
    assert "plan error" in capsys.readouterr().err


@pytest.fixture()
def benchmark_plan(tmp_path):
    path = tmp_path / "plan.jsonl"
    path.write_text(
        '{"name": "catalog", "path": "/api/catalog", "weight": 2}\n'
        '{"name": "search", "path": "/api/search", "weight": 1}\n',
        encoding="utf-8",
    )
    return path


def test_cli_benchmark_default_text(benchmark_plan, capsys):
    code = main(["benchmark", "--plan", str(benchmark_plan), "--requests", "6",
                 "--concurrency", "1,2"])
    output = capsys.readouterr().out
    assert code == 0
    assert "Concurrency sweep" in output
    assert "conc" in output


def test_cli_benchmark_json(benchmark_plan, capsys):
    code = main(["benchmark", "--plan", str(benchmark_plan), "--requests", "4",
                 "--concurrency", "1", "--format", "json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["levels"][0]["requests"] == 4


def test_cli_benchmark_sla_failure(benchmark_plan, capsys):
    code = main(["benchmark", "--plan", str(benchmark_plan), "--requests", "3",
                 "--concurrency", "1", "--sla-p95", "0.001"])
    assert code == 1
    assert "SLA missed" in capsys.readouterr().out


def test_cli_benchmark_bad_levels(benchmark_plan, capsys):
    assert main(["benchmark", "--plan", str(benchmark_plan), "--concurrency", "0,2"]) == 2
    assert "positive integers" in capsys.readouterr().err


def test_cli_replay_against_mock(benchmark_plan, capsys):
    code = main(["replay", "--plan", str(benchmark_plan), "--url", "http://127.0.0.1:1",
                 "--requests", "1", "--concurrency", "1", "--timeout", "0.2"])
    assert code in (0, 1)
    assert "failures" in capsys.readouterr().out


def test_cli_mock_runs_for_a_short_duration(capsys):
    assert main(["mock", "--port", "0", "--duration", "0.2"]) == 0
    output = capsys.readouterr().out
    assert "mock target on" in output
    assert "served:" in output
