# Manual screenshots

The pictures in `docs/manual/images/` come from the real cards, driven in a
local Chrome by `shoot.mjs`. Run it again after a UI change.

- `index.html` and `harness.js` stand in for a Home Assistant dashboard: a
  `ha-card` and `ha-icon` like Home Assistant's, the default theme colours,
  and a `hass` object whose service calls go to `server.py`.
- `server.py` serves the bundle and the timetable indexes, and answers
  `hungarian_transport.futar` (with `BKK_API_KEY`) and
  `hungarian_transport.elvira_between` (through a running Home Assistant).
  The key and the token stay on the server.
- `shoot.mjs` sets every card up through its own editor, the way a user
  would, and saves each scenario to `../images/<lang>/<name>.webp`.

## Run

```bash
# 1. Assets: the release's indexes, the current bundle and Leaflet.
mkdir -p docs/manual/tools/www && cd docs/manual/tools/www
gh release download -R bator/hungarian-transport -p hungarian-transport-frontend.zip
unzip -o hungarian-transport-frontend.zip && rm hungarian-transport-frontend.zip
cd - && npm run build
cp dist/hungarian-transport.js docs/manual/tools/www/
cp -r dist/leaflet docs/manual/tools/www/
# optional, for train routes: the mav-index.json.gz your Home Assistant built
curl -o docs/manual/tools/www/mav-index.json.gz http://homeassistant.local:8123/hungarian-transport/mav-index.json.gz

# 2. Server (integration test venv, for aiohttp).
BKK_API_KEY=... HA_URL=http://homeassistant.local:8123 HA_TOKEN=... \
  .venv/bin/python docs/manual/tools/server.py

# 3. Shots, in another shell.
cd docs/manual/tools && npm install
node shoot.mjs en
node shoot.mjs hu
node shoot.mjs en card-mav,map-   # only scenarios whose name starts so
```

`CHROME` points at the Chrome binary (default `/usr/bin/google-chrome`),
`PORT` changes the server port (default 8765).

The data is live, so every run shows different departures. Run on a weekday
morning for full boards. The planner scenarios ask Transitous, which can be
slow; a failed scenario is reported at the end and the others still run.
