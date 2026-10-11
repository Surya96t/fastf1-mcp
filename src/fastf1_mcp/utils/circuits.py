"""
Stored circuit outlines (bundled GeoJSON, works offline).

Source: bacinger/f1-circuits (MIT) — see data/circuits/README.md. Each
outline is a closed lon/lat polyline starting on the start/finish line.
Outline lengths match the official figures to within ~1% for every circuit
raced since 2018, and the direction of each outline was checked against the
known direction of racing and the direction of Turn 1.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from difflib import get_close_matches
from functools import lru_cache
from importlib import resources

import numpy as np

from .errors import ErrorCode, FastF1MCPError

_EARTH_RADIUS_M = 6_371_008.8

# Stored against the direction of racing (verified via circuit guides).
_REVERSED = {"sg-2008", "fr-1969"}
# Direction labels that the polygon orientation can't express.
_DIRECTION_OVERRIDE = {"jp-1962": "figure-eight"}

# Grand Prix names, nicknames and venue names → circuit id. Matching is
# accent/case-insensitive and ignores "grand prix" / "gp" / "circuit".
_ALIASES: dict[str, list[str]] = {
    "au-1953": ["australia", "australian", "albert park", "melbourne"],
    "bh-2002": ["bahrain", "sakhir", "bahrain international"],
    "cn-2004": ["china", "chinese", "shanghai"],
    "es-1991": ["barcelona", "catalunya", "barcelona-catalunya", "montmelo"],
    "es-2026": ["madrid", "madring"],
    "mc-1929": ["monaco", "monte carlo", "monte-carlo"],
    "ca-1978": ["canada", "canadian", "montreal", "gilles villeneuve"],
    "fr-1969": ["france", "french", "paul ricard", "le castellet"],
    "at-1969": [
        "austria",
        "austrian",
        "styria",
        "styrian",
        "red bull ring",
        "spielberg",
    ],
    "gb-1948": [
        "britain",
        "british",
        "great britain",
        "silverstone",
        "70th anniversary",
    ],
    "de-1932": ["germany", "german", "hockenheim", "hockenheimring"],
    "hu-1986": ["hungary", "hungarian", "hungaroring", "budapest"],
    "be-1925": ["belgium", "belgian", "spa", "spa-francorchamps", "spa francorchamps"],
    "it-1922": ["italy", "italian", "monza"],
    "sg-2008": ["singapore", "marina bay"],
    "ru-2014": ["russia", "russian", "sochi"],
    "jp-1962": ["japan", "japanese", "suzuka"],
    "us-2012": [
        "united states",
        "usa",
        "us",
        "austin",
        "cota",
        "circuit of the americas",
        "americas",
    ],
    "mx-1962": [
        "mexico",
        "mexican",
        "mexico city",
        "mexico city grand prix",
        "hermanos rodriguez",
    ],
    "br-1940": ["brazil", "brazilian", "sao paulo", "interlagos"],
    "ae-2009": ["abu dhabi", "yas marina", "yas island"],
    "it-1953": ["imola", "emilia romagna", "emilia-romagna", "san marino"],
    "de-1927": ["nurburgring", "eifel"],
    "pt-2008": ["portugal", "portuguese", "portimao", "algarve"],
    "it-1914": ["tuscany", "tuscan", "mugello"],
    "my-1999": ["malaysia", "malaysian", "sepang"],
    "tr-2005": ["turkey", "turkish", "istanbul", "istanbul park"],
    "nl-1948": ["netherlands", "dutch", "zandvoort"],
    "qa-2004": ["qatar", "losail", "lusail"],
    "sa-2021": ["saudi arabia", "saudi arabian", "jeddah", "saudi"],
    "us-2022": ["miami"],
    "az-2016": ["azerbaijan", "baku"],
    "us-2023": ["las vegas", "vegas"],
    "fr-1960": ["magny-cours", "magny cours"],
    "pt-1972": ["estoril"],
    "br-1977": ["jacarepagua", "rio de janeiro"],
    "us-1909": ["indianapolis", "indy"],
    "ar-1952": ["argentina", "argentine", "buenos aires"],
    "za-1961": ["south africa", "south african", "kyalami"],
    "us-1956": ["watkins glen"],
}
# Names whose circuit changed over time: (alias, first year, circuit id).
_YEAR_ALIASES = [("spanish", 2026, "es-2026"), ("spain", 2026, "es-2026")]
_YEAR_ALIASES_BEFORE = {"spanish": "es-1991", "spain": "es-1991"}


@dataclass(frozen=True)
class Circuit:
    id: str
    name: str
    location: str
    length_m: int
    opened: int | None
    first_gp: int | None
    altitude_m: float | None
    lonlat: np.ndarray  # (n, 2), closed loop without the repeated last point

    @property
    def xy(self) -> np.ndarray:
        """Outline in metres (x east, y north) around the circuit centroid."""
        lon, lat = self.lonlat[:, 0], self.lonlat[:, 1]
        lat0 = math.radians(float(lat.mean()))
        x = np.radians(lon - lon.mean()) * _EARTH_RADIUS_M * math.cos(lat0)
        y = np.radians(lat - lat.mean()) * _EARTH_RADIUS_M
        return np.column_stack([x, y])

    @property
    def measured_length_m(self) -> float:
        xy = self.xy
        closed = np.vstack([xy, xy[:1]])
        return float(np.hypot(*np.diff(closed, axis=0).T).sum())

    @property
    def direction(self) -> str:
        if self.id in _DIRECTION_OVERRIDE:
            return _DIRECTION_OVERRIDE[self.id]
        x, y = self.xy.T
        area = 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))
        return "anticlockwise" if area > 0 else "clockwise"


def smooth_loop(xy: np.ndarray, n: int = 1200) -> np.ndarray:
    """
    Resample a closed polyline into a smooth loop (periodic cubic spline
    through every stored point), starting at the same start/finish point.
    """
    from scipy.interpolate import splev, splprep

    keep = np.concatenate(([True], np.hypot(*np.diff(xy, axis=0).T) > 0.5))
    pts = xy[keep]
    loop = np.vstack([pts, pts[:1]])
    tck, _ = splprep([loop[:, 0], loop[:, 1]], s=0, per=True)
    u = np.linspace(0, 1, n, endpoint=False)
    x, y = splev(u, tck)
    return np.column_stack([x, y])


_NOISE = re.compile(r"\b(grand prix|gp|formula 1|f1|circuit|de|of the|the)\b")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    text = _NOISE.sub(" ", text.lower().replace("-", " "))
    return " ".join(text.split())


@lru_cache(maxsize=1)
def load_circuits() -> dict[str, Circuit]:
    root = resources.files("fastf1_mcp") / "data" / "circuits"
    data = json.loads((root / "f1-circuits.geojson").read_text(encoding="utf-8"))
    out = {}
    for feat in data["features"]:
        p = feat["properties"]
        coords = np.asarray(feat["geometry"]["coordinates"], dtype=float)
        if len(coords) > 1 and np.allclose(coords[0], coords[-1]):
            coords = coords[:-1]
        if p["id"] in _REVERSED:
            coords = np.vstack([coords[:1], coords[1:][::-1]])
        out[p["id"]] = Circuit(
            id=p["id"],
            name=p.get("Name", p["id"]),
            location=p.get("Location", ""),
            length_m=int(p.get("length") or 0),
            opened=p.get("opened"),
            first_gp=p.get("firstgp"),
            altitude_m=p.get("altitude"),
            lonlat=coords,
        )
    return out


@lru_cache(maxsize=None)
def _calendar(year: int) -> list[str]:
    root = resources.files("fastf1_mcp") / "data" / "circuits" / "calendars"
    try:
        entries = json.loads((root / f"{year}.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    return [e["id"] for e in entries]


def calendar_years() -> list[int]:
    return [y for y in range(2020, 2031) if _calendar(y)]


def _alias_index() -> dict[str, str]:
    index = {}
    for cid, c in load_circuits().items():
        index[_norm(cid)] = cid
        index[_norm(c.name)] = cid
        index[_norm(c.location)] = cid
        for alias in _ALIASES.get(cid, []):
            index[_norm(alias)] = cid
    return index


def resolve_circuit(query: str | int | None, year: int | None = None) -> Circuit:
    """
    Find a stored circuit from a name, Grand Prix name, nickname, circuit id
    or (with `year`) a round number.
    """
    circuits = load_circuits()
    if query is None or str(query).strip() == "":
        raise FastF1MCPError(
            ErrorCode.INVALID_PARAMETER, "Give a circuit or Grand Prix name."
        )

    text = str(query).strip()
    if text.isdigit():
        rnd = int(text)
        cal = _calendar(year) if year else []
        if not cal:
            raise FastF1MCPError(
                ErrorCode.INVALID_PARAMETER,
                f"Round numbers need a year with a stored calendar "
                f"({', '.join(map(str, calendar_years()))}); got year={year}.",
                suggestions=["Pass the Grand Prix or circuit name instead"],
            )
        if not 1 <= rnd <= len(cal):
            raise FastF1MCPError(
                ErrorCode.INVALID_PARAMETER,
                f"{year} has {len(cal)} rounds in the stored calendar; got {rnd}.",
            )
        return circuits[cal[rnd - 1]]

    q = _norm(text)
    if not q:
        raise FastF1MCPError(
            ErrorCode.INVALID_PARAMETER,
            f"{text!r} doesn't name a circuit.",
            suggestions=["Use a Grand Prix name (e.g. 'Monaco') or nickname ('COTA')"],
        )
    for alias, first_year, cid in _YEAR_ALIASES:
        if q == alias:
            if year is not None and year >= first_year:
                return circuits[cid]
            return circuits[_YEAR_ALIASES_BEFORE[alias]]
    index = _alias_index()
    if q in index:
        return circuits[index[q]]
    # Substring match in either direction (e.g. "Monaco Grand Prix 2024").
    hits = {
        cid for key, cid in index.items() if len(key) >= 4 and (key in q or q in key)
    }
    if len(hits) == 1:
        return circuits[hits.pop()]
    close = get_close_matches(q, list(index), n=3, cutoff=0.75)
    if close and len({index[c] for c in close}) == 1:
        return circuits[index[close[0]]]
    suggestions = sorted(
        {circuits[index[c]].name for c in close} | {circuits[h].name for h in hits}
    )[:5]
    raise FastF1MCPError(
        ErrorCode.INVALID_PARAMETER,
        f"No stored circuit matches {text!r}.",
        suggestions=(
            [f"Did you mean: {', '.join(suggestions)}?"] if suggestions else []
        )
        + [
            "Use a Grand Prix name (e.g. 'Monaco'), circuit name or nickname "
            "(e.g. 'COTA', 'Spa')"
        ],
    )
