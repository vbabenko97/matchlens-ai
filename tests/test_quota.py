"""Shared daily quota (Azure Table Storage over a SAS URL), against a fake Table service."""

import json
import re
import threading

import pytest

from matchlens.quota import ROW_KEY, CounterError, MemoryCounter, TableCounter

SAS = "https://acct.table.core.windows.net/livechecks?sv=2022-11-02&tn=livechecks&sp=raud&sig=SECRETSIG"
DAY = "2026-10-25"
ENTITY = re.compile(
    r"^https://acct\.table\.core\.windows\.net/livechecks\(PartitionKey='([\d-]+)',RowKey='([\w-]+)'\)$"
)


class FakeTable:
    """Table service semantics that matter here: ETags, If-Match (412), insert conflict (409)."""

    def __init__(self):
        self.rows, self.version, self.requests = {}, 0, []
        self.lock = threading.Lock()
        self.fail = None  # (method, status) to answer once

    def __call__(self, method, url, headers, body, timeout):
        path, _, query = url.partition("?")
        assert "sig=SECRETSIG" in query and timeout <= 5
        assert headers["Accept"].startswith("application/json")
        with self.lock:
            self.requests.append((method, path))
            if self.fail and self.fail[0] == method:
                status, self.fail = self.fail[1], None
                return status, {}, b"{}"
            if method == "POST":
                row = json.loads(body)
                key = (row["PartitionKey"], row["RowKey"])
                if key in self.rows:
                    return 409, {}, b"{}"
                self.version += 1
                self.rows[key] = (row["Count"], f'W/"{self.version}"')
                return 204, {}, b""
            key = ENTITY.match(path).groups()
            if method == "GET":
                if key not in self.rows:
                    return 404, {}, b"{}"
                count, etag = self.rows[key]
                return 200, {"etag": etag}, json.dumps({"Count": count, "RowKey": key[1]}).encode()
            if method == "PUT":
                if key not in self.rows or headers.get("If-Match") != self.rows[key][1]:
                    return 412, {}, b"{}"
                self.version += 1
                self.rows[key] = (json.loads(body)["Count"], f'W/"{self.version}"')
                return 204, {}, b""
        raise AssertionError(method)


def test_reserves_up_to_the_limit_then_refuses():
    table = FakeTable()
    counter = TableCounter(SAS, transport=table)
    assert [counter.reserve(DAY, 3) for _ in range(4)] == [1, 2, 3, None]
    assert table.rows[(DAY, ROW_KEY)][0] == 3
    assert counter.reserve("2026-10-26", 3) == 1  # a new UTC day starts at zero
    assert counter.reserve(DAY, 0) is None


def test_a_restart_or_second_instance_sees_the_same_count():
    table = FakeTable()
    TableCounter(SAS, transport=table).reserve(DAY, 2)
    TableCounter(SAS, transport=table).reserve(DAY, 2)
    assert TableCounter(SAS, transport=table).reserve(DAY, 2) is None


def test_concurrent_instances_never_exceed_the_limit():
    table = FakeTable()
    results, start = [], threading.Barrier(12)

    def worker():
        counter = TableCounter(SAS, transport=table)  # one "instance" per thread
        start.wait()
        for _ in range(3):
            try:
                results.append(counter.reserve(DAY, 20))
            except CounterError:
                results.append("contended")

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    granted = [r for r in results if isinstance(r, int)]
    assert len(granted) <= 20 and sorted(granted) == list(range(1, len(granted) + 1))
    assert table.rows[(DAY, ROW_KEY)][0] == len(granted)


def test_lost_races_are_retried():
    table = FakeTable()
    counter = TableCounter(SAS, transport=table)
    counter.reserve(DAY, 5)
    table.fail = ("PUT", 412)
    assert counter.reserve(DAY, 5) == 2
    fresh = FakeTable()
    fresh.fail = ("POST", 409)  # lost the insert race: read again and continue
    assert TableCounter(SAS, transport=fresh).reserve(DAY, 5) == 1
    assert [m for m, _ in fresh.requests] == ["GET", "POST", "GET", "POST"]


def test_endless_conflicts_give_up_instead_of_spinning():
    def always_conflict(method, url, headers, body, timeout):
        return (404, {}, b"{}") if method == "GET" else (409, {}, b"{}")

    with pytest.raises(CounterError, match="contended"):
        TableCounter(SAS, transport=always_conflict).reserve(DAY, 5)


@pytest.mark.parametrize(
    ("method", "status"), [("GET", 403), ("GET", 500), ("PUT", 500), ("POST", 403)]
)
def test_storage_errors_fail_closed_without_the_token(method, status):
    table = FakeTable()
    counter = TableCounter(SAS, transport=table)
    if method == "PUT":
        counter.reserve(DAY, 5)
    table.fail = (method, status)
    with pytest.raises(CounterError) as err:
        counter.reserve(DAY, 5)
    assert f"HTTP {status}" in str(err.value) and "SECRETSIG" not in str(err.value)


def test_network_errors_and_bad_entities_fail_closed():
    def down(*args):
        raise OSError("connection refused to https://acct...sig=SECRETSIG")

    with pytest.raises(CounterError) as err:
        TableCounter(SAS, transport=down).reserve(DAY, 5)
    assert "SECRETSIG" not in str(err.value) and err.value.__cause__ is None

    for raw, headers in (
        (b"not json", {"etag": "x"}),
        (b'{"Count": "3"}', {"etag": "x"}),
        (b'{"Count": true}', {"etag": "x"}),
        (b'{"Count": 1}', {}),
    ):

        def reply(method, url, h, body, timeout, raw=raw, headers=headers):
            return 200, headers, raw

        with pytest.raises(CounterError):
            TableCounter(SAS, transport=reply).reserve(DAY, 5)


@pytest.mark.parametrize(
    "url",
    [
        "http://acct.table.core.windows.net/livechecks?sig=x",
        "https://acct.table.core.windows.net/?sig=x",
        "https://acct.table.core.windows.net/livechecks",
        "https://acct.table.core.windows.net/a/b?sig=x",
    ],
)
def test_rejects_anything_but_a_table_sas_url(url):
    with pytest.raises(ValueError):
        TableCounter(url)


def test_repr_hides_the_token():
    assert "SECRETSIG" not in repr(TableCounter(SAS)) and "sv=" not in repr(TableCounter(SAS))


def test_memory_counter_has_the_same_contract():
    counter = MemoryCounter()
    assert [counter.reserve(DAY, 2) for _ in range(3)] == [1, 2, None]
