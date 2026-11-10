"""The ELVIRA rows the cards read: one shared case (tests/fixtures/elvira),
which test-rows.mjs feeds through the cards' ELVIRA adapter."""

from datetime import datetime, timedelta
import json
from pathlib import Path
import zoneinfo

from custom_components.hungarian_transport.elvira import rows_from_stationinfo

CASE = Path(__file__).parent / "fixtures" / "elvira" / "between.json"


def test_rows_are_the_ones_the_cards_are_checked_with() -> None:
    case = json.loads(CASE.read_text(encoding="utf-8"))
    tz = zoneinfo.ZoneInfo(case["tz"])
    now = datetime.fromisoformat(case["now"])
    rows = rows_from_stationinfo(
        case["origin_departures"],
        case["dest_arrivals"],
        tz,
        now,
        now + timedelta(minutes=case["minutes_after"]),
        case["dest_name"],
    )
    # Compared as JSON: the service answers JSON, and a tuple or an int
    # where the cards expect a list or a bool would still compare equal.
    assert json.dumps(rows, ensure_ascii=False) == json.dumps(case["rows"], ensure_ascii=False)
    # Every row says whether the destination's board lists the train: the
    # cards count only true.
    assert all(isinstance(row["destHit"], bool) for row in rows)
