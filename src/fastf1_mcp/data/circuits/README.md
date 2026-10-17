# Bundled circuit outlines

`f1-circuits.geojson` and `calendars/*.json` come from
[bacinger/f1-circuits](https://github.com/bacinger/f1-circuits) (commit
`394d8fb`, 2026-02-05), MIT licensed — see `LICENSE-f1-circuits.md`.

Each circuit is a closed GeoJSON LineString (lon/lat) that starts on the
start/finish line. Two circuits are stored against the direction of racing
(Marina Bay `sg-2008`, Paul Ricard `fr-1969`); `utils/circuits.py` reverses
them.

The calendars have been corrected from upstream to list **one entry per
championship round, in round order** (upstream lists each venue once per
season), so `resolve_circuit("12", 2024)` maps to round 12:

- 2020: added the second Red Bull Ring (Styrian GP), Silverstone (70th
  Anniversary GP) and Bahrain (Sakhir GP) rounds — 17 rounds. The Sakhir GP
  used Bahrain's Outer layout, which is not in the outline data; the
  standard Bahrain outline is shown.
- 2021: added the Styrian GP (Red Bull Ring) — 22 rounds.
- 2023: removed the cancelled Emilia Romagna GP (Imola) — 22 rounds.
- 2026: removed the cancelled Bahrain and Saudi Arabian GPs and added the
  Bahrain GP held at Sepang (2-4 October, round 16) — 23 rounds.

The data is unofficial and not endorsed by Formula One Licensing B.V.
