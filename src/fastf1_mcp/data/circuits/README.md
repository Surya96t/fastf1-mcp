# Bundled circuit outlines

`f1-circuits.geojson` and `calendars/*.json` come from
[bacinger/f1-circuits](https://github.com/bacinger/f1-circuits) (commit
`394d8fb`, 2026-02-05), MIT licensed — see `LICENSE-f1-circuits.md`.

Each circuit is a closed GeoJSON LineString (lon/lat) that starts on the
start/finish line. Two circuits are stored against the direction of racing
(Marina Bay `sg-2008`, Paul Ricard `fr-1969`); `utils/circuits.py` reverses
them. The calendars list each season's circuits in round order.

The data is unofficial and not endorsed by Formula One Licensing B.V.
