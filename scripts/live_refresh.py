#!/usr/bin/env python3
"""
Kinshasa Traffic Board — live traffic sweep (Google Routes API).

Fetches current, traffic-aware travel times for the board's arteries
from the Google Routes API and merges a "live" block into
data/board.json, then regenerates index.html (via build.py
--skip-overpass, which preserves the live block). The page shows live
levels while the block is fresh (< 3 h) and silently falls back to the
typical hourly model otherwise.

Why a separate script instead of a build.py mode: build.py is the
daily full generator (Overpass geometry + curated content). A live
sweep runs up to ~21 times a day, must be fast, must never touch the
geometry/curated data, and must fail in complete isolation — a broken
sweep leaves the previous live block (or its absence) exactly as it
was. Different cadence, different failure domain, different script.

API-call budget (Google's free tier is 5,000 calls/month): one
computeRoutes call per artery would be 16 calls/sweep ≈ 8,500/month —
over budget. So arteries are grouped into 6 geographic CHAINS; each
chain is a single computeRoutes call whose waypoints thread through
its arteries (sampled points on each artery's own geometry), and the
per-leg durations Google returns are summed per artery. 6 calls/sweep
× ~535 sweeps/month ≈ 3,200 calls/month. See live.yml for the cadence
and CHAINS below for the grouping rationale.

Failure policy (deliberate, all-or-nothing): if the API key is
missing, or ANY chain call fails / returns an unexpected shape, the
script prints a warning, exits 0, and leaves data/board.json and
index.html untouched — a sweep either lands complete or not at all,
and a failed sweep can never break the site or the workflow.

The API key is read ONLY from the ROUTES_API_KEY environment variable
(a GitHub Actions secret in production). It is never written to disk,
never printed, and never reaches the generated page.

Uses only the Python 3 standard library.
"""

import json
import math
import os
import statistics
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "board.json"
BUILD_SCRIPT = ROOT / "scripts" / "build.py"

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.legs.duration,routes.legs.staticDuration"
USER_AGENT = "KinshasaTrafficBoard/1.0 (live sweep; contact: patpoundy2kb@gmail.com)"
HTTP_TIMEOUT = 60

# ---------------------------------------------------------------------------
# Chains: 6 computeRoutes calls cover all 16 arteries.
#
# Arteries are grouped by geography so that one routed path can thread
# through them in sequence, entering each artery near where the previous
# one ends (the fetcher orients each artery's sampled points greedily
# from the actual geometry, so the orders below are corridors, not
# turn-by-turn directions). The leg between two different arteries (the
# "connector") is discarded — only legs whose two endpoints both lie on
# the same artery are summed into that artery's live time.
#
#  1. gombe-north — the northern river corridor, west→east: Avenue du
#     Tourisme (river road from the west) flows into Boulevard du
#     Colonel Tshatshi (Gombe riverfront), which meets the west end of
#     Boulevard du 30 Juin (downtown Gombe axis). Short connectors.
#  2. east — Limete/Masina: Route des Poids Lourds (port/industrial,
#     west end of the eastern cluster) feeds Boulevard Lumumba, the
#     long eastern axis to Ndjili airport. One call covers the whole
#     Gombe→Limete test corridor Pat validated by hand.
#  3. centre — the dense central grid: Avenue des Huileries (N–S)
#     drops into Boulevard Triomphal (Palais du Peuple), then Avenue
#     de la Victoire and Boulevard Sendwe (Kalamu, stadium), finishing
#     along Avenue Kasa-Vubu whose north-eastern end closes the loop
#     ~200 m from Triomphal's east end.
#  4. west — Ngaliema/Kintambo: Avenue Colonel Mondjiba and Avenue
#     Nguma share a junction (Mondjiba's west end ≈ Nguma's north
#     end), so one call covers both western communes.
#  5. south — the southern fan: Avenue de la Libération (long N–S
#     axis) ends near the west end of the Avenue Elengesa / By-Pass
#     southern arc, whose east end meets the north end of Avenue de
#     l'Université down to UNIKIN.
#  6. rn1 — RN1 Route de Matadi stands alone: it leaves the city to
#     the south-west with no neighbouring board artery to chain with,
#     so it gets its own call (counted honestly in the budget).
#
# Stop counts per chain stay ≤ 19, under the Routes API waypoint limit.
CHAINS = [
    ("gombe-north", ["av-tourisme", "bd-tshatshi", "bd-30-juin"]),
    ("east", ["poids-lourds", "bd-lumumba"]),
    ("centre", ["av-huileries", "bd-triomphal", "av-victoire",
                "bd-sendwe", "av-kasa-vubu"]),
    ("west", ["av-mondjiba", "av-nguma"]),
    ("south", ["av-liberation", "av-elengesa-bypass", "av-universite"]),
    ("rn1", ["rn1-matadi"]),
]

MAX_STOPS_PER_CALL = 25  # Routes API waypoint ceiling (origin+intermediates+destination)


# ---------------------------------------------------------------------------
# Geometry: build a small set of probe points along each artery.
#
# The baked OSM geometry is a bag of way segments: name regexes pull in
# stray same-name streets elsewhere in the city, and divided boulevards
# appear as twin carriageways. So per artery we (1) cluster segments by
# endpoint proximity and keep the longest connected cluster (the real
# corridor), then (2) sample points spread along the cluster's
# principal axis (PCA) — every sample is a real geometry point lying on
# the artery, so a route threaded through them follows the artery.
# ---------------------------------------------------------------------------

def haversine_km(p1, p2):
    """p = [lon, lat]."""
    r = 6371.0
    la1, lo1, la2, lo2 = (math.radians(p1[1]), math.radians(p1[0]),
                          math.radians(p2[1]), math.radians(p2[0]))
    dla, dlo = la2 - la1, lo2 - lo1
    a = (math.sin(dla / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _segment_length(line):
    return sum(haversine_km(line[i], line[i + 1]) for i in range(len(line) - 1))


def main_cluster(lines, link_km=0.4):
    """Longest endpoint-connected cluster of segments (the corridor)."""
    segs = [list(l) for l in lines if len(l) >= 2]
    if not segs:
        return [], 0.0
    parent = list(range(len(segs)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ends = [(s[0], s[-1]) for s in segs]
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if any(haversine_km(a, b) <= link_km
                   for a in ends[i] for b in ends[j]):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    clusters = {}
    for i in range(len(segs)):
        clusters.setdefault(find(i), []).append(i)
    best = max(clusters.values(),
               key=lambda idxs: sum(_segment_length(segs[i]) for i in idxs))
    chosen = [segs[i] for i in best]
    return chosen, sum(_segment_length(s) for s in chosen)


def sample_points(lines, n_target):
    """n_target probe points spread along the artery's main corridor."""
    cluster, cluster_km = main_cluster(lines)
    if not cluster:
        return []
    pts, seen = [], set()
    for line in cluster:
        for p in line:
            key = (round(p[0], 5), round(p[1], 5))
            if key not in seen:
                seen.add(key)
                pts.append(p)
    if len(pts) < 2:
        return pts
    # Principal axis of the corridor (PCA on lon/lat, lon scaled by cos(lat)).
    mlon = statistics.mean(p[0] for p in pts)
    mlat = statistics.mean(p[1] for p in pts)
    cs = math.cos(math.radians(mlat))
    xy = [((p[0] - mlon) * cs, p[1] - mlat) for p in pts]
    sxx = sum(x * x for x, _ in xy)
    syy = sum(y * y for _, y in xy)
    sxy = sum(x * y for x, y in xy)
    theta = 0.5 * math.atan2(2 * sxy, sxx - syy)
    ax, ay = math.cos(theta), math.sin(theta)
    proj = sorted((x * ax + y * ay, p) for (x, y), p in zip(xy, pts))
    lo, hi = proj[0][0], proj[-1][0]
    if hi - lo < 1e-9:
        return [proj[0][1]]
    picked = []
    for i in range(n_target):
        target = lo + (hi - lo) * i / (n_target - 1)
        picked.append(min(proj, key=lambda t: abs(t[0] - target))[1])
    # Keep consecutive probes ≥ 250 m apart so no leg is degenerate,
    # but always keep both corridor ends.
    out = [picked[0]]
    for p in picked[1:-1]:
        if haversine_km(out[-1], p) >= 0.25:
            out.append(p)
    if picked[-1] != out[-1]:
        out.append(picked[-1])
    return out


def _sample_count(cluster_km):
    if cluster_km < 4:
        return 3
    if cluster_km < 10:
        return 4
    if cluster_km < 18:
        return 5
    return 6


def build_chains(board):
    """Return [{name, stops: [(artery_id, [lon, lat]), ...]}, ...].

    Each artery's probe points are oriented greedily: whichever end is
    nearer the previous artery's last stop becomes the entry point, so
    connector legs stay short. Raises ValueError on any inconsistency
    (missing artery, too few points, too many stops).
    """
    features = {f["properties"]["id"]: f
                for f in board["arteries"]["features"]}
    chains = []
    covered = set()
    for name, artery_ids in CHAINS:
        stops = []
        for aid in artery_ids:
            if aid not in features:
                raise ValueError(f"artery {aid} missing from board.json")
            if aid in covered:
                raise ValueError(f"artery {aid} chained twice")
            covered.add(aid)
            lines = features[aid]["geometry"]["coordinates"]
            _, cluster_km = main_cluster(lines)
            pts = sample_points(lines, _sample_count(cluster_km))
            if len(pts) < 2:
                raise ValueError(f"artery {aid}: only {len(pts)} probe points")
            if stops:  # orient: nearest end to the chain's current tail
                tail = stops[-1][1]
                if haversine_km(tail, pts[-1]) < haversine_km(tail, pts[0]):
                    pts = pts[::-1]
            stops.extend((aid, p) for p in pts)
        if len(stops) > MAX_STOPS_PER_CALL:
            raise ValueError(
                f"chain {name}: {len(stops)} stops > {MAX_STOPS_PER_CALL}")
        chains.append({"name": name, "stops": stops})
    missing = set(features) - covered
    if missing:
        raise ValueError(f"arteries not covered by any chain: {sorted(missing)}")
    return chains


# ---------------------------------------------------------------------------
# Google Routes API
# ---------------------------------------------------------------------------

class SweepError(Exception):
    """Any failure that aborts the sweep (previous live data is kept)."""


def _parse_duration(value):
    """Routes API durations are strings like '2523s' (may be fractional)."""
    if not isinstance(value, str) or not value.endswith("s"):
        raise SweepError(f"unexpected duration value: {value!r}")
    try:
        return float(value[:-1])
    except ValueError as exc:
        raise SweepError(f"unparseable duration: {value!r}") from exc


def post_routes(api_key, stops):
    """One computeRoutes call for a chain's stops; returns the legs list."""
    def latlng(p):
        return {"location": {"latLng": {"latitude": p[1], "longitude": p[0]}}}

    points = [p for _, p in stops]
    departure = (datetime.now(timezone.utc) + timedelta(seconds=120))
    body = {
        "origin": latlng(points[0]),
        "destination": latlng(points[-1]),
        "intermediates": [latlng(p) for p in points[1:-1]],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "departureTime": departure.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    req = urllib.request.Request(
        ROUTES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": FIELD_MASK,
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — any failure aborts the sweep
        raise SweepError(f"Routes API call failed: {exc}") from exc
    routes = payload.get("routes")
    if not routes:
        raise SweepError(f"Routes API returned no routes: {str(payload)[:300]}")
    legs = routes[0].get("legs")
    if legs is None:
        raise SweepError("Routes API response has no legs")
    return legs


def level_for_ratio(ratio):
    if ratio < 1.08:
        return "fluid"
    if ratio < 1.25:
        return "slow"
    if ratio < 1.50:
        return "heavy"
    return "blocked"


def _round_half_up_1dp(x):
    return math.floor(x * 10 + 0.5) / 10


def aggregate_chain(chain, legs):
    """Sum leg durations per artery. Leg i joins stops i and i+1; it
    belongs to an artery only if both stops are on that artery —
    otherwise it is a connector leg and is discarded."""
    stops = chain["stops"]
    if len(legs) != len(stops) - 1:
        raise SweepError(
            f"chain {chain['name']}: {len(legs)} legs for {len(stops)} stops")
    totals = {}  # artery_id -> [duration_s, static_s]
    for i, leg in enumerate(legs):
        a_here, a_next = stops[i][0], stops[i + 1][0]
        if a_here != a_next:
            continue  # connector leg between two arteries
        try:
            dur = _parse_duration(leg["duration"])
            sta = _parse_duration(leg["staticDuration"])
        except KeyError as exc:
            raise SweepError(
                f"chain {chain['name']}: leg {i} missing {exc}") from exc
        slot = totals.setdefault(a_here, [0.0, 0.0])
        slot[0] += dur
        slot[1] += sta
    return totals


def fetch_live(board, api_key):
    """Run every chain and build the live block. Raises SweepError on
    any failure; the caller then keeps the previous live data."""
    chains = build_chains(board)
    merged = {}
    for chain in chains:
        legs = post_routes(api_key, chain["stops"])
        for aid, (dur, sta) in aggregate_chain(chain, legs).items():
            if sta <= 0:
                raise SweepError(f"artery {aid}: non-positive static duration")
            merged[aid] = (dur, sta)
    arteries = {}
    for aid, (dur, sta) in sorted(merged.items()):
        ratio = dur / sta
        arteries[aid] = {
            "ratio": round(ratio, 2),
            "level": level_for_ratio(ratio),
            "mins": _round_half_up_1dp(dur / 60),
            "staticMins": _round_half_up_1dp(sta / 60),
        }
    return {
        "checkedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Google Routes",
        "arteries": arteries,
    }


# ---------------------------------------------------------------------------

def main():
    api_key = os.environ.get("ROUTES_API_KEY", "").strip()
    if not api_key:
        print("WARNING: ROUTES_API_KEY is not set — live sweep skipped, "
              "previous live data kept.", file=sys.stderr)
        return 0
    if not DATA_FILE.exists():
        print(f"ERROR: {DATA_FILE} not found.", file=sys.stderr)
        return 1
    board = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    try:
        live = fetch_live(board, api_key)
    except SweepError as exc:
        print(f"WARNING: live sweep aborted ({exc}) — previous live "
              "data kept.", file=sys.stderr)
        return 0
    except ValueError as exc:
        print(f"WARNING: live sweep aborted ({exc}) — previous live "
              "data kept.", file=sys.stderr)
        return 0

    board["live"] = live
    DATA_FILE.write_text(
        json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Live sweep OK: {len(live['arteries'])} arteries, "
          f"checkedAt {live['checkedAt']}.")

    # Regenerate index.html so the baked page data carries the live
    # block (build.py --skip-overpass preserves it). A failure here is
    # a real build error, not a data failure: fail loudly.
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--skip-overpass"],
        cwd=str(ROOT), capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print("ERROR: index.html regeneration failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
