import json
import socket
import time
import urllib.error
import urllib.request

import pytest

from http_load_replayer.mock import MockTarget
from http_load_replayer.plan import ReplayPlan, RequestTemplate
from http_load_replayer.runner import replay
from http_load_replayer.stats import classify, percentile, summarize


@pytest.fixture()
def target():
    server = MockTarget()
    server.start()
    yield server
    server.stop()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_mock_endpoint_latency_and_status(target):
    started = time.perf_counter()
    status, payload = get(f"{target.base_url}/api/catalog")
    elapsed = (time.perf_counter() - started) * 1000
    assert status == 200
    assert payload["endpoint"] == "/api/catalog"
    assert elapsed >= 10


def test_mock_unknown_endpoint_is_404(target):
    status, payload = get(f"{target.base_url}/nope")
    assert status == 404
    assert payload["error"] == "no such endpoint"


def test_mock_search_rejects_every_seventh_call(target):
    statuses = [get(f"{target.base_url}/api/search")[0] for _ in range(7)]
    assert statuses.count(429) == 1
    assert statuses[-1] == 429


def test_mock_accepts_post_body(target):
    request = urllib.request.Request(
        f"{target.base_url}/api/orders",
        data=b'{"sku":"A"}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 201


def test_percentile_edges():
    assert percentile([], 0.9) == 0.0
    assert percentile([5.0], 0.99) == 5.0
    values = [float(value) for value in range(1, 101)]
    assert percentile(values, 0.5) == 51.0
    assert percentile(values, 0.99) == 99.0


@pytest.mark.parametrize("status,label", [(200, "2xx"), (302, "3xx"), (404, "4xx"),
                                          (503, "5xx"), (None, "transport")])
def test_classify(status, label):
    assert classify(status) == label


def make_plan():
    return ReplayPlan(
        requests=(
            RequestTemplate("fast", "GET", "/api/catalog", weight=3),
            RequestTemplate("slow", "GET", "/api/checkout", weight=1),
        )
    )


def test_replay_records_every_request(target):
    outcome = replay(make_plan(), target.base_url, requests=20, concurrency=4)
    assert outcome.requests == 20
    assert sum(outcome.statuses.values()) == 20
    assert outcome.wall_seconds > 0
    assert {record.name for record in outcome.records} == {"fast", "slow"}


def test_replay_is_weighted(target):
    outcome = replay(make_plan(), target.base_url, requests=40, concurrency=1)
    names = [record.name for record in outcome.records]
    assert names.count("fast") == 30
    assert names.count("slow") == 10


def test_summary_reports_sla_and_endpoints(target):
    outcome = replay(make_plan(), target.base_url, requests=20, concurrency=2)
    summary = summarize(outcome, sla_p95_ms=1000, sla_error_rate=5)
    assert summary["requests"] == 20
    assert summary["sla"]["passed"] is True
    assert len(summary["sla"]["checks"]) == 2
    assert {endpoint["name"] for endpoint in summary["endpoints"]} == {"fast", "slow"}
    assert summary["latency"]["p99"] >= summary["latency"]["p50"]


def test_summary_detects_missed_sla(target):
    outcome = replay(make_plan(), target.base_url, requests=10, concurrency=1)
    summary = summarize(outcome, sla_p95_ms=0.001, sla_error_rate=0.0)
    assert summary["sla"]["passed"] is False


def test_transport_error_is_classified(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    plan = ReplayPlan((RequestTemplate("dead", "GET", "/x"),))
    outcome = replay(plan, f"http://127.0.0.1:{port}", requests=2, concurrency=1, timeout=0.3)
    summary = summarize(outcome)
    assert summary["failures"] == 2
    assert "transport" in summary["classes"]
    assert summary["transport_errors"]


def test_replay_rejects_bad_arguments(target):
    plan = make_plan()
    with pytest.raises(ValueError, match="requests"):
        replay(plan, target.base_url, requests=0)
    with pytest.raises(ValueError, match="concurrency"):
        replay(plan, target.base_url, concurrency=0)
    with pytest.raises(ValueError, match="base url"):
        replay(plan, "ftp://example", requests=1)
