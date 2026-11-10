# Glossary

Domain terms used in the code, the tests and the reviews. Architecture words
(module, interface, seam, adapter, depth) keep their usual meaning.

**Stop pair**: the origin and destination stops a card is set up for. Every
departure board, journey plan and ride map answers for one stop pair.

**Departure row**: one departure from the origin towards the destination, as
the cards show it: line label, headsign, scheduled and live departure, ride
time, and where it came from (`source`: FUTÁR, GTFS, ELVIRA or MOTIS). Rows
from different sources are merged into one list; a row's trip id keeps its
source prefix (`gtfs:`, `elvira:`, `motis:`) as a key, but decisions read
`source`.

**ELVIRA row**: a departure row built from MÁV's ELVIRA station boards by the
integration. `destHit` says the destination's arrival board confirmed the
train calls there; a MÁV card lists only those trains.

**Departure board**: the ordered departure rows a hop card shows for its stop
pair at one moment, with its banner (why rows are missing or old) and its
phase (partial, filled). Polling produces a new board from the previous one.

**MÁV board**: the departure board of a card in MÁV mode, built from ELVIRA
rows rather than FUTÁR.

**Route pattern**: the stop sequences (variants) of one route, used to tell
whether a route's trips from the origin reach the destination. Proved
patterns are kept in the browser for the day.

**Served MÁV index**: the MÁV route index the integration currently serves to
the cards. A newer build replaces it only by the shared same-feed rules.

**Ride map**: the map of one departure row's trip between the stop pair: the
route shape, and where the rider boards and alights.

**Journey plan**: the planner card's multi-leg answer for a stop pair, either
for now (replanned as time passes) or for a chosen time.
