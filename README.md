# Hungarian transport

Home Assistant Lovelace card for **BKK**, **MÁV** and **Volán** journeys.

Pick an origin and a destination. The card lists the next departures between
those two stops, using the live BKK FUTÁR API for city/rail traffic and the
official KTI Volán GTFS feed for long-distance coaches.

## Lovelace type

Existing dashboards keep working with:

```yaml
type: custom:bkk-stop-card-r3
```

In the card picker the name is **Hungarian transport**.

The visual row layout comes from `custom:bkk-stop-card-r2` (`layout_bpgo: true`).
Load that resource as well.

## Install

1. Copy `hungarian-transport-card.js` to
   `/config/www/community/bkk-stop-card/` (or add this repo as a HACS custom
   repository).
2. Add a Lovelace resource:

   ```yaml
   url: /local/community/bkk-stop-card/hungarian-transport-card.js?v=1
   type: module
   ```

3. For Volán long-distance coaches, build and deploy the GTFS index:

   ```bash
   python3 scripts/build_volan_index.py
   ```

   Place `volan-index.v26.json.gz` next to the card JS. The card fetches
   `/local/community/bkk-stop-card/volan-index.v26.json.gz`.

4. First-time editor setup asks for a BKK Open Data API key:
   https://opendata.bkk.hu/data-sources

## Modes

| Toggle | Source | Search |
|---|---|---|
| (none) | BKK FUTÁR | BKK stops |
| MÁV | BKK FUTÁR rail | MÁV stations |
| Volán | FUTÁR local coaches + KTI GTFS | Volán stops |

Destination search only offers stops that a vehicle from the origin actually
reaches.

Long-distance Volán (for example Székesfehérvár autóbusz-állomás → Budapest)
terminates at **Népliget**, not Kelenföld. There is no direct coach between
those two stops in the official GTFS.

## License

Private repository. Not published.
