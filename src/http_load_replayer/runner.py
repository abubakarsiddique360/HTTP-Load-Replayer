"""The replay engine: N workers, one keep-alive connection each, one record per request."""

from __future__ import annotations

import http.client
import queue
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .plan import ReplayPlan, RequestTemplate

USER_AGENT = "http-load-replayer/1.0"


@dataclass(frozen=True)
class RequestRecord:
    name: str
    method: str
    path: str
    status: int | None
    latency_ms: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and self.status < 400


@dataclass
class ReplayOutcome:
    records: list[RequestRecord] = field(default_factory=list)
    wall_seconds: float = 0.0
    concurrency: int = 1
    base_url: str = ""

    @property
    def requests(self) -> int:
        return len(self.records)

    @property
    def statuses(self) -> Counter:
        return Counter(record.status for record in self.records if record.status is not None)

    @property
    def errors(self) -> Counter:
        return Counter(record.error for record in self.records if record.error)


def replay(
    plan: ReplayPlan,
    base_url: str,
    *,
    requests: int = 100,
    concurrency: int = 4,
    timeout: float = 5.0,
) -> ReplayOutcome:
    if requests < 1:
        raise ValueError("requests must be at least 1")
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"base url must be http(s)://host[:port], got {base_url!r}")
    prefix = parsed.path.rstrip("/")
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    use_tls = parsed.scheme == "https"

    schedule = plan.expanded(requests)
    work: queue.Queue[RequestTemplate] = queue.Queue()
    for template in schedule:
        work.put(template)

    records: list[RequestRecord] = []
    records_lock = threading.Lock()
    workers = min(concurrency, len(schedule))

    def worker() -> None:
        connection: http.client.HTTPConnection | None = None
        while True:
            try:
                template = work.get_nowait()
            except queue.Empty:
                return
            record, connection = _execute(
                connection, template, host, port, prefix, timeout, use_tls
            )
            with records_lock:
                records.append(record)
            work.task_done()

    started = time.perf_counter()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=max(30.0, timeout * requests))
    wall = round(time.perf_counter() - started, 4)

    return ReplayOutcome(
        records=records,
        wall_seconds=wall,
        concurrency=workers,
        base_url=base_url,
    )


def _execute(
    connection: http.client.HTTPConnection | None,
    template: RequestTemplate,
    host: str,
    port: int,
    prefix: str,
    timeout: float,
    use_tls: bool,
) -> tuple[RequestRecord, http.client.HTTPConnection | None]:
    path = f"{prefix}{template.path}"
    body = template.body_bytes
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **template.headers}
    if body is not None:
        headers.setdefault("Content-Type", "application/json")

    fresh = connection is None
    if connection is None:
        connection = _connect(host, port, timeout, use_tls)

    started = time.perf_counter()
    try:
        connection.request(template.method, path, body=body, headers=headers)
        response = connection.getresponse()
        response.read()
        elapsed = (time.perf_counter() - started) * 1000
        if response.will_close:
            connection.close()
            connection = None
        return (
            RequestRecord(template.name, template.method, path, response.status, round(elapsed, 3)),
            connection,
        )
    except (http.client.HTTPException, OSError, ValueError) as exc:
        elapsed = (time.perf_counter() - started) * 1000
        if connection is not None:
            connection.close()
        error = "timeout" if isinstance(exc, TimeoutError) else type(exc).__name__
        if fresh and not isinstance(exc, TimeoutError):
            error = f"connect:{error}"
        return (
            RequestRecord(template.name, template.method, path, None, round(elapsed, 3), error),
            None,
        )


def _connect(host: str, port: int, timeout: float, use_tls: bool) -> http.client.HTTPConnection:
    if use_tls:
        import ssl

        return http.client.HTTPSConnection(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
    return http.client.HTTPConnection(host, port, timeout=timeout)
