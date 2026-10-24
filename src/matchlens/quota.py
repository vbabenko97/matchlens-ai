"""Shared daily quota for live Foundry checks: a reservation is taken before any model call.

``TableCounter`` keeps one count per UTC day in an Azure Table Storage entity, reached with
a table SAS URL (stdlib HTTP only, no SDK). Every reservation is a read followed by a
conditional write (``If-Match`` on the entity's ETag, or an insert that fails if the entity
exists), retried on conflict, so concurrent instances and restarts never exceed the limit.
Any storage failure raises ``CounterError``, and the caller makes no live call (fail closed).

``MemoryCounter`` is the same contract in process memory, for tests and a local
``matchlens serve --live-api`` with the operator's own key.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urlsplit

COUNTER_URL_ENV = "MATCHLENS_LIVE_COUNTER_URL"
ROW_KEY = "live-checks"
MAX_CONFLICTS = 8  # concurrent reservations that may race one another before giving up

# (method, url, headers, body, timeout) -> (status, lower-cased headers, body)
HttpTransport = Callable[
    [str, str, dict[str, str], bytes | None, float], tuple[int, dict[str, str], bytes]
]


class CounterError(RuntimeError):
    """The quota store could not be read or updated. Messages never include the SAS token."""


class CounterTimeout(CounterError):
    """The caller's time ran out before the reservation finished; no unit was taken."""


MIN_CALL_S = 1.0  # do not start a store call with less time than this left
TimeLeft = Callable[[], float]  # seconds left of the caller's deadline


class DailyCounter(Protocol):
    def reserve(self, day: str, limit: int, time_left: TimeLeft | None = None) -> int | None:
        """Take one unit for ``day``: the new count, or ``None`` when ``limit`` is reached.

        ``time_left`` bounds the whole reservation; when it runs out, ``CounterTimeout``.
        """
        ...


class MemoryCounter:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def reserve(self, day: str, limit: int, time_left: TimeLeft | None = None) -> int | None:
        with self._lock:  # in memory: nothing to wait for
            used = self._counts.get(day, 0)
            if used >= limit:
                return None
            self._counts[day] = used + 1
            return used + 1


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # a SAS URL never redirects; refuse to follow
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urllib_http(method, url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read()


class TableCounter:
    """Daily counts in Azure Table Storage: entity (PartitionKey=<UTC day>, RowKey=ROW_KEY)."""

    def __init__(self, sas_url: str, transport: HttpTransport | None = None, timeout_s=5.0):
        parts = urlsplit(sas_url)
        table = parts.path.strip("/")
        if parts.scheme != "https" or not parts.netloc:
            raise ValueError(f"{COUNTER_URL_ENV} must be an https:// table SAS URL")
        if not table or "/" in table or "(" in table or "sig=" not in parts.query:
            raise ValueError(
                f"{COUNTER_URL_ENV} must be a table SAS URL: https://<account>"
                ".table.core.windows.net/<table>?<sas token>"
            )
        self._base = f"https://{parts.netloc}/{table}"
        self._query = parts.query
        self._http = transport or _urllib_http
        self._timeout = timeout_s

    def __repr__(self) -> str:  # never show the SAS token
        return f"TableCounter({self._base})"

    def _call(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict | None = None,
        time_left: TimeLeft | None = None,
    ):
        headers = {
            "Accept": "application/json;odata=nometadata",
            "x-ms-version": "2019-02-02",
            "DataServiceVersion": "3.0;NetFx",
            **headers,
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        timeout = self._timeout
        if time_left is not None:
            left = time_left()
            if left <= MIN_CALL_S:
                raise CounterTimeout("no time left to reserve quota")
            timeout = min(timeout, left)
        try:
            return self._http(method, f"{url}?{self._query}", headers, data, timeout)
        except Exception as exc:  # network: the type only, the URL carries the token
            if time_left is not None and time_left() <= MIN_CALL_S:
                raise CounterTimeout("quota store too slow for the deadline") from None
            raise CounterError(f"quota store unreachable ({type(exc).__name__})") from None

    def reserve(self, day: str, limit: int, time_left: TimeLeft | None = None) -> int | None:
        if limit <= 0:
            return None
        entity = f"{self._base}(PartitionKey='{day}',RowKey='{ROW_KEY}')"
        for _ in range(MAX_CONFLICTS):
            status, headers, raw = self._call("GET", entity, {}, time_left=time_left)
            if status == 404:
                row = {"PartitionKey": day, "RowKey": ROW_KEY, "Count": 1}
                status, _, _ = self._call(
                    "POST", self._base, {"Prefer": "return-no-content"}, row, time_left
                )
                if status in (201, 204):
                    return 1
                if status == 409:  # another instance created it first: read again
                    continue
                raise CounterError(f"quota store insert failed (HTTP {status})")
            if status != 200:
                raise CounterError(f"quota store read failed (HTTP {status})")
            try:
                used = json.loads(raw)["Count"]
            except (ValueError, KeyError, TypeError):
                raise CounterError("quota store entity is malformed") from None
            if not isinstance(used, int) or isinstance(used, bool):
                raise CounterError("quota store entity is malformed")
            if used >= limit:
                return None
            etag = headers.get("etag")
            if not etag:
                raise CounterError("quota store reply has no ETag")
            row = {"PartitionKey": day, "RowKey": ROW_KEY, "Count": used + 1}
            status, _, _ = self._call("PUT", entity, {"If-Match": etag}, row, time_left)
            if status == 204:
                return used + 1
            if status == 412:  # changed since the read: another reservation won, read again
                continue
            raise CounterError(f"quota store update failed (HTTP {status})")
        raise CounterError("quota store is too contended")
