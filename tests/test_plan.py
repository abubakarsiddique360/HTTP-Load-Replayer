import json

import pytest

from http_load_replayer.plan import PlanError, load_plan, write_plan

VALID = """{"name": "browse", "method": "GET", "path": "/api/catalog", "weight": 5}
{"name": "order", "method": "POST", "path": "/api/orders", "body": {"sku": "A"}, "weight": 1}
"""


def write(tmp_path, text, name="plan.jsonl"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_templates_and_weights(tmp_path):
    plan = load_plan(write(tmp_path, VALID))
    assert [item.name for item in plan.requests] == ["browse", "order"]
    assert plan.total_weight == 6
    assert plan.requests[1].body == {"sku": "A"}
    assert plan.requests[1].body_bytes == b'{"sku":"A"}'


def test_missing_file(tmp_path):
    with pytest.raises(PlanError, match="not found"):
        load_plan(tmp_path / "no.jsonl")


def test_empty_plan(tmp_path):
    with pytest.raises(PlanError, match="no requests"):
        load_plan(write(tmp_path, "\n# only a comment\n"))


def test_broken_json_reports_line(tmp_path):
    path = write(tmp_path, VALID + "{oops}\n")
    with pytest.raises(PlanError, match="line 3"):
        load_plan(path)


@pytest.mark.parametrize(
    "entry,message",
    [
        ({"method": "GET", "path": "/x"}, "name"),
        ({"name": "a", "method": "TRACE", "path": "/x"}, "method"),
        ({"name": "a", "method": "GET", "path": "x"}, "start with /"),
        ({"name": "a", "method": "GET", "path": "/x", "weight": 0}, "weight"),
        ({"name": "a", "method": "GET", "path": "/x", "headers": []}, "headers"),
        ({"name": "a", "method": "GET", "path": "/x", "body": "raw"}, "body"),
        ({"name": "a", "method": "GET", "path": "/x", "timeout": 3}, "unknown field"),
    ],
)
def test_template_validation(tmp_path, entry, message):
    path = write(tmp_path, json.dumps(entry) + "\n")
    with pytest.raises(PlanError, match=message):
        load_plan(path)


def test_duplicate_names_are_rejected(tmp_path):
    path = write(tmp_path, VALID + json.dumps(
        {"name": "browse", "method": "GET", "path": "/other"}) + "\n")
    with pytest.raises(PlanError, match="duplicate request name"):
        load_plan(path)


def test_body_size_limit(tmp_path):
    huge = {"name": "a", "method": "POST", "path": "/x", "body": {"blob": "x" * 70_000}}
    with pytest.raises(PlanError, match="exceeds"):
        load_plan(write(tmp_path, json.dumps(huge) + "\n"))


def test_headers_are_lowercased(tmp_path):
    line = json.dumps({"name": "a", "path": "/x", "headers": {"X-Trace": "1"}})
    plan = load_plan(write(tmp_path, line + "\n"))
    assert plan.requests[0].headers == {"x-trace": "1"}


def test_expanded_follows_weights(tmp_path):
    plan = load_plan(write(tmp_path, VALID))
    names = [item.name for item in plan.expanded(12)]
    assert names.count("browse") == 10
    assert names.count("order") == 2
    assert len(names) == 12


def test_expanded_handles_zero_and_large_counts(tmp_path):
    plan = load_plan(write(tmp_path, VALID))
    assert plan.expanded(0) == []
    assert len(plan.expanded(37)) == 37


def test_summary_shares_add_up(tmp_path):
    plan = load_plan(write(tmp_path, VALID))
    rows = plan.summary()
    assert sum(row["weight"] for row in rows) == plan.total_weight
    assert round(sum(row["share"] for row in rows), 1) == 100.0


def test_write_plan_round_trip(tmp_path):
    plan = load_plan(write(tmp_path, VALID))
    out = tmp_path / "copy.jsonl"
    write_plan(plan, out)
    again = load_plan(out)
    assert [item.as_dict() for item in again.requests] == [
        item.as_dict() for item in plan.requests
    ]
