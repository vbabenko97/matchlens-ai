"""Azure Functions entry point for ``POST /api/live-check`` (Static Web Apps managed API).

The deploy workflow copies the ``matchlens`` package next to this folder (``api/matchlens``).
Azure OpenAI settings come from the Static Web App's application settings, never from the
repository. All request handling, limits and fallbacks live in ``matchlens.live``.
"""

import azure.functions as func

from matchlens.live import LiveChecker, checker_from_env, handle
from matchlens.replay import dumps

_checker: LiveChecker | None = None  # built on the first request, kept while the instance lives


def main(req: func.HttpRequest) -> func.HttpResponse:
    global _checker
    if _checker is None:
        _checker = checker_from_env()
    status, reply = handle(_checker, req.method, dict(req.headers), req.get_body())
    return func.HttpResponse(
        dumps(reply),
        status_code=status,
        mimetype="application/json",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
