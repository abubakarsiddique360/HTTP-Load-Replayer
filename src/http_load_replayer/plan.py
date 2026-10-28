"""The replay plan: a weighted mix of requests, one JSON object per line.

Weights let a small file describe a realistic mix - five catalogue reads for every checkout -
so a 60 request run still looks like production traffic instead of a uniform loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
MAX_BODY_BYTES = 64 * 1024


class PlanError(ValueError):
    """Raised when a replay plan cannot be read or is not usable."""


@dataclass(frozen=True)
class RequestTemplate:
    name: str
    method: str
    path: str
    weight: int = 1
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None

    @property
    def body_bytes(self) -> bytes | None:
        if self.body is None:
            return None
        return json.dumps(self.body, separators=(",", ":")).encode("utf-8")

    def as_dict(self) -> dict:
        data: dict[str, Any] = {
            "name": self.name,
            "method": self.method,
            "path": self.path,
            "weight": self.weight,
        }
        if self.headers:
            data["headers"] = dict(self.headers)
        if self.body is not None:
            data["body"] = self.body
        return data


@dataclass(frozen=True)
class ReplayPlan:
    requests: tuple[RequestTemplate, ...]
    source: str = "<memory>"

    @property
    def total_weight(self) -> int:
        return sum(item.weight for item in self.requests)

    def expanded(self, count: int) -> list[RequestTemplate]:
        """Return ``count`` requests following the weights, round-robin by weight."""
        if count <= 0:
            return []
        schedule: list[RequestTemplate] = []
        remaining = {item.name: item.weight for item in self.requests}
        while len(schedule) < count:
            for item in self.requests:
                if remaining[item.name] <= 0:
                    continue
                schedule.append(item)
                remaining[item.name] -= 1
                if len(schedule) == count:
                    break
            if all(value <= 0 for value in remaining.values()):
                remaining = {item.name: item.weight for item in self.requests}
        return schedule[:count]

    def summary(self) -> list[dict]:
        total = self.total_weight or 1
        return [
            {
                "name": item.name,
                "method": item.method,
                "path": item.path,
                "weight": item.weight,
                "share": round(item.weight / total * 100, 1),
            }
            for item in self.requests
        ]


def load_plan(path: str | Path) -> ReplayPlan:
    source = Path(path)
    if not source.exists():
        raise PlanError(f"plan not found: {source}")
    templates: list[RequestTemplate] = []
    errors: list[str] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: not valid JSON ({exc.msg})")
            continue
        try:
            template = _parse_template(data)
        except PlanError as exc:
            errors.append(f"line {line_number}: {exc}")
            continue
        if template.name in seen:
            errors.append(f"line {line_number}: duplicate request name {template.name!r}")
            continue
        seen.add(template.name)
        templates.append(template)
    if errors:
        raise PlanError("; ".join(errors))
    if not templates:
        raise PlanError(f"plan contains no requests: {source}")
    return ReplayPlan(requests=tuple(templates), source=str(source))


def _parse_template(data: Any) -> RequestTemplate:
    if not isinstance(data, dict):
        raise PlanError("each line must be a JSON object")
    unknown = sorted(set(data) - {"name", "method", "path", "weight", "headers", "body"})
    if unknown:
        raise PlanError(f"unknown field(s): {', '.join(unknown)}")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PlanError("name must be a non-empty string")
    method = data.get("method", "GET")
    if not isinstance(method, str) or method.upper() not in METHODS:
        raise PlanError(f"method must be one of {', '.join(METHODS)}")
    path = data.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise PlanError("path must start with /")
    weight = data.get("weight", 1)
    if not isinstance(weight, int) or isinstance(weight, bool) or weight < 1:
        raise PlanError("weight must be an integer >= 1")
    headers = data.get("headers", {})
    if not isinstance(headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
    ):
        raise PlanError("headers must be an object of string values")
    body = data.get("body")
    if body is not None and not isinstance(body, dict):
        raise PlanError("body must be a JSON object")
    if body is not None:
        encoded = json.dumps(body)
        if len(encoded.encode("utf-8")) > MAX_BODY_BYTES:
            raise PlanError(f"body exceeds {MAX_BODY_BYTES} bytes")
    return RequestTemplate(
        name=name.strip(),
        method=method.upper(),
        path=path,
        weight=weight,
        headers={key.lower(): value for key, value in headers.items()},
        body=body,
    )


def write_plan(plan: ReplayPlan, path: str | Path) -> None:
    Path(path).write_text(
        "\n".join(json.dumps(item.as_dict()) for item in plan.requests) + "\n",
        encoding="utf-8",
    )
