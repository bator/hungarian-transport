"""The codes a card service answers with instead of failing the call.

The websocket API logs every failed call at ERROR, and every open card
polls every 45 s: an outage is part of the answer, as {"error": code}. A
response code is not looked up in exceptions.*, so each service's
description (translations/*.json) explains its own codes; the contract
test reads CODES.
"""

from __future__ import annotations

from typing import Any

from .const import SERVICE_ELVIRA_BETWEEN, SERVICE_FUTAR, SERVICE_REALTIME

ELVIRA_UNAVAILABLE = "elvira_unavailable"
# The integration's own request limit, not MÁV being down.
ELVIRA_RATE_LIMITED = "elvira_rate_limited"
RT_UNAVAILABLE = "rt_unavailable"
RT_NO_FEED = "rt_no_feed"
FUTAR_AUTH = "futar_auth"
FUTAR_RATE_LIMITED = "futar_rate_limited"
FUTAR_BAD_REQUEST = "futar_bad_request"
FUTAR_UNAVAILABLE = "futar_unavailable"

CODES: dict[str, tuple[str, ...]] = {
    SERVICE_ELVIRA_BETWEEN: (ELVIRA_UNAVAILABLE, ELVIRA_RATE_LIMITED),
    SERVICE_REALTIME: (RT_UNAVAILABLE, RT_NO_FEED),
    SERVICE_FUTAR: (FUTAR_AUTH, FUTAR_RATE_LIMITED, FUTAR_BAD_REQUEST, FUTAR_UNAVAILABLE),
}


def error_answer(empty: dict[str, Any], code: str, detail: str | None = None) -> dict[str, Any]:
    """The service's answer without data: its empty fields, then the code
    and, when there is one, the detail (the order the cards were sent)."""
    out = {**empty, "error": code}
    if detail is not None:
        out["detail"] = detail
    return out
