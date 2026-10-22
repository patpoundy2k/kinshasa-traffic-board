#!/usr/bin/env python3
"""
Kinshasa Traffic Board — FIH departures/arrivals board (AeroDataBox).

Fetches the N'djili airport (FIH / FZAA) departures and arrivals board
from the AeroDataBox API (via RapidAPI) and merges a "flightsBoard"
block into data/board.json, then regenerates index.html (via
build.py --skip-overpass, which preserves the block). The page renders
the block as a departures/arrivals card and falls back to an honest
"unavailable" line when the block is absent or older than 24 h.

Why a separate script instead of a build.py mode: same reason as
scripts/live_refresh.py — different cadence, different failure
domain, must fail in complete isolation. A broken fetch leaves the
previous flightsBoard block (or its absence) exactly as it was.

Data source and plan: AeroDataBox on the FREE Basic plan (RapidAPI).
$0, no card, hard cap of 400 API units/month; attribution to
AeroDataBox is REQUIRED on the free plan (the card's header line
carries it) and commercial use is excluded — the board is a free,
non-commercial community site. The FIDS endpoint used here
("Airport departures and arrivals (by time range)") is TIER 2 =
2 units per call.

QUOTA ARITHMETIC (hard constraint — never exceed the 400 cap):
  2 calls per fetch (departures + arrivals) × 2 units = 4 units/fetch
  3 fetches/day × 4 units                          = 12 units/day
  worst month: 31 days × 12 units                  = 372 units ≤ 400
CADENCE (Pat's rule): checks ONLY between 16:00 and 23:00 Kinshasa
time — no fetches overnight or in the morning. Fetches run at 16:30,
19:30 and 22:30 Africa/Kinshasa (see .github/workflows/flights.yml).
Each fetch covers the NEXT 12 h, so the windows tile more than the
full day: 16:30→04:30, 19:30→07:30, 22:30→10:30 — the 22:30 fetch
alone keeps the board showing flights until 10:30 the next morning,
and each fetch also merges in the same-day rows the previous fetch
already found, so the day's board is cumulative, not just a window.

Response handling: AeroDataBox rows are NORMALIZED into our own
compact schema (see normalize_flight) so the page never depends on
raw API field names. Field lookups are defensive (documented names
first, then plausible fallbacks); a row that yields no scheduled time
or no flight number is skipped, never guessed at. Per-flight
"quality" decides honesty: "Live" rows may carry a revised time and
a real status; "Basic" rows are schedule-only and the page labels
them exactly that ("Horaire prévu").

Failure policy (deliberate, all-or-nothing, mirroring live_refresh):
if the API key is missing, or ANY call fails / returns an unexpected
shape / returns no flights at all, the script prints a warning,
exits 0, and leaves data/board.json and index.html untouched.

The API key is read ONLY from the RAPIDAPI_KEY environment variable
(a GitHub Actions secret in production). It is never written to
disk, never printed, and never reaches the generated page.

Uses only the Python 3 standard library.
"""

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "board.json"
BUILD_SCRIPT = ROOT / "scripts" / "build.py"

API_HOST = "aerodatabox.p.rapidapi.com"
API_BASE = f"https://{API_HOST}"
AIRPORT_ICAO = "FZAA"
USER_AGENT = "KinshasaTrafficBoard/1.0 (FIH flights board; contact: patpoundy2kb@gmail.com)"
HTTP_TIMEOUT = 60

# Kinshasa is UTC+1 year-round (no DST).
KINSHASA_TZ = timezone(timedelta(hours=1))

# Quota budget — see the header. Do not raise FETCHES_PER_DAY or the
# window without redoing this arithmetic against the 400/month cap.
UNITS_PER_CALL = 2        # FIDS endpoint is TIER 2
CALLS_PER_FETCH = 2       # departures + arrivals
FETCHES_PER_DAY = 3       # 16:30, 19:30, 22:30 Kinshasa — nothing overnight
UNITS_PER_DAY = UNITS_PER_CALL * CALLS_PER_FETCH * FETCHES_PER_DAY  # 12
WINDOW_HOURS = 12         # the FIDS endpoint caps one call at a 12 h range
MAX_ROWS_PER_DIRECTION = 80  # safety valve; FIH sees ~25 flights/direction/day


# ---------------------------------------------------------------------------
# AeroDataBox access
# ---------------------------------------------------------------------------

class FetchError(Exception):
    """Any failure that aborts the fetch (previous board data is kept)."""


def fmt_local(dt):
    """Local ISO minute precision, e.g. '2026-10-22T16:30' — the shape
    the FIDS path parameters take (airport-local time)."""
    return dt.strftime("%Y-%m-%dT%H:%M")


def fids_url(direction, window_start, window_end):
    """One FIDS call: both path times are FIH-local; the direction is a
    query parameter (the endpoint otherwise returns both directions).
    Cargo and private flights are excluded — this is a passenger
    board; cancelled and codeshared flights are kept (codeshares are
    real, bookable flights, and a cancellation is information)."""
    path = (f"/flights/airports/icao/{AIRPORT_ICAO}/"
            f"{fmt_local(window_start)}/{fmt_local(window_end)}")
    query = urllib.parse.urlencode({
        "direction": direction,          # "Departure" | "Arrival"
        # withLeg=false is contractual: rows then carry a single
        # "movement" object (the shape normalize_flight handles
        # first). Without it the response shape depends on the API's
        # default, which is how production silently broke once.
        "withLeg": "false",
        "withCancelled": "true",
        "withCodeshared": "true",
        "withCargo": "false",
        "withPrivate": "false",
    })
    return f"{API_BASE}{path}?{query}"


def http_get_json(url, api_key):
    req = urllib.request.Request(
        url,
        headers={
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": API_HOST,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — any failure aborts the fetch
        raise FetchError(f"AeroDataBox call failed: {exc}") from exc


def extract_rows(payload, key):
    """Pull the flight list for one direction out of a FIDS payload.
    Documented shape: {"departures": [...], "arrivals": [...]}.
    Fallbacks: a bare list, or a list nested one level down."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
        for alt in ("data", "flights", "results"):
            sub = payload.get(alt)
            if isinstance(sub, list):
                return sub
            if isinstance(sub, dict) and isinstance(sub.get(key), list):
                return sub[key]
    raise FetchError(f"unexpected FIDS payload shape: {str(payload)[:200]}")


# ---------------------------------------------------------------------------
# Normalization: raw AeroDataBox row -> our compact schema
#   {"flight": "SN 358", "airline": "Brussels Airlines",
#    "to"|"from": {"iata": "BRU", "name": "Brussels"},
#    "sched": "2026-10-22T21:15+01:00", "revised": ... | None,
#    "status": "Expected", "quality": "Live" | "Basic"}
# Times are the FIH-side times (departure times for departures,
# arrival times for arrivals), kept as the API's local ISO strings.
# ---------------------------------------------------------------------------

def _time_local(time_obj):
    """AeroDataBox times are {"utc": ..., "local": ...} objects; the
    local string carries the airport's offset. Fallbacks: a bare
    string, or the UTC string if no local one is present."""
    if time_obj is None:
        return None
    if isinstance(time_obj, str):
        return time_obj.strip() or None
    if isinstance(time_obj, dict):
        for key in ("local", "Local", "localTime"):
            value = time_obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        value = time_obj.get("utc")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _canon_time(value):
    """Canonicalize an AeroDataBox time string to ISO with a "T"
    separator. Real payload strings look like "2026-10-22 18:45+01:00"
    (SPACE separator); parse_iso and the page's JS expect the
    "2026-10-22T18:45+01:00" form the original fixtures used.
    Already-canonical and "Z" strings pass through unchanged apart
    from the separator fix; non-strings pass through untouched."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) > 10 and text[10] == " ":
        text = text[:10] + "T" + text[11:]
    return text


def _movement(item, key):
    move = item.get(key)
    return move if isinstance(move, dict) else None


def _airport_of(movement):
    if movement is None:
        return {"iata": "", "name": ""}
    ap = movement.get("airport")
    if not isinstance(ap, dict):
        return {"iata": "", "name": ""}
    name = ap.get("name") or ap.get("shortName") or ap.get("municipalityName") or ""
    iata = ap.get("iata") or ""
    return {"iata": str(iata), "name": str(name)}


def _flight_number(item):
    for key in ("number", "flightNumber", "iata", "flight"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and value:
            return str(value)
    airline = item.get("airline")
    if isinstance(airline, dict):
        code = airline.get("iata") or airline.get("icao") or ""
        num = item.get("numberSuffix") or ""
        if code:
            return f"{code} {num}".strip()
    return ""


def _quality(item, movement, revised):
    """Normalize the per-flight quality. Raw value may be a string
    ("Live"/"Basic"), a list of such strings, on the flight or on the
    movement. A present revised time is itself proof of live data, so
    it wins over a "Basic" label."""
    raw = None
    if movement is not None:
        raw = movement.get("quality")
    if raw is None:
        raw = item.get("quality")
    values = []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = [str(v) for v in raw]
    lowered = [v.strip().lower() for v in values]
    if "live" in lowered or revised:
        return "Live"
    return "Basic"


def normalize_flight(item, direction):
    """direction is "Departure" or "Arrival" (the FIDS query value).
    Returns the normalized row, or None when the row cannot be shown
    honestly (no FIH-side scheduled time or no flight number)."""
    if not isinstance(item, dict):
        return None
    if item.get("isCargo") is True:
        return None  # passenger board (the query also excludes cargo)
    movement = item.get("movement")
    if isinstance(movement, dict):
        # Real FIDS shape (withLeg=false, pinned in fids_url): a
        # single "movement" object. For BOTH directions its
        # scheduledTime is the FIH-side time (it carries the +01:00
        # Kinshasa offset even on the other airport's row), and its
        # "airport" is the OTHER airport — destination for a
        # departure, origin for an arrival.
        fih_side = movement
        other_airport = _airport_of(movement)
        place_key = "to" if direction == "Departure" else "from"
    elif direction == "Departure":
        # Leg shape (withLeg=true): per-leg departure/arrival objects.
        fih_side = _movement(item, "departure")
        other_airport = _airport_of(_movement(item, "arrival"))
        place_key = "to"
    else:
        fih_side = _movement(item, "arrival")
        other_airport = _airport_of(_movement(item, "departure"))
        place_key = "from"
    if fih_side is None:
        return None
    sched = _canon_time(_time_local(fih_side.get("scheduledTime")))
    if not sched:
        return None
    revised = None
    for key in ("revisedTime", "actualTime", "runwayTime",
                "predictedTime", "estimatedTime"):
        revised = _canon_time(_time_local(fih_side.get(key)))
        if revised:
            break
    number = _flight_number(item)
    if not number:
        return None
    airline = item.get("airline")
    airline_name = ""
    if isinstance(airline, dict):
        airline_name = (airline.get("name") or airline.get("iata")
                        or airline.get("icao") or "")
    status = item.get("status")
    if not isinstance(status, str):
        status = ""
    return {
        "flight": number,
        "airline": str(airline_name),
        place_key: other_airport,
        "sched": sched,
        "revised": revised,
        "status": status,
        "quality": _quality(item, fih_side, revised),
    }


def normalize_all(items, direction):
    rows, skipped = [], 0
    for item in items:
        row = normalize_flight(item, direction)
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    if skipped:
        print(f"  note: skipped {skipped} unusable {direction} row(s).")
    return rows


# ---------------------------------------------------------------------------
# Merge: the board is cumulative for the service day. Rows from the
# previous block stay (with their last known status) while their
# scheduled time is inside [start of the fetch day, window end]; fresh
# rows overlay by (flight, sched). This is what keeps a morning
# departure visible on the board all day even though later fetches
# only look forward.
# ---------------------------------------------------------------------------

def parse_iso(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KINSHASA_TZ)
    return dt


def merge_rows(previous, new, keep_from, keep_to):
    merged = {}
    for row in previous or []:
        if not isinstance(row, dict):
            continue
        dt = parse_iso(row.get("sched"))
        if dt is None or dt < keep_from or dt > keep_to:
            continue
        merged[(row.get("flight", ""), row.get("sched", ""))] = row
    for row in new:
        merged[(row["flight"], row["sched"])] = row
    out = sorted(
        merged.values(),
        key=lambda r: (parse_iso(r.get("sched")) or keep_from,
                       r.get("flight", "")),
    )
    return out[:MAX_ROWS_PER_DIRECTION]


# ---------------------------------------------------------------------------

def fetch_board(api_key, now):
    """Run both FIDS calls for the next-12 h window from `now`.
    Returns (window_start, window_end, departures, arrivals) with rows
    normalized. Raises FetchError on any failure."""
    window_start = now.astimezone(KINSHASA_TZ).replace(second=0, microsecond=0)
    window_end = window_start + timedelta(hours=WINDOW_HOURS)
    dep_payload = http_get_json(
        fids_url("Departure", window_start, window_end), api_key)
    arr_payload = http_get_json(
        fids_url("Arrival", window_start, window_end), api_key)
    departures = normalize_all(extract_rows(dep_payload, "departures"),
                               "Departure")
    arrivals = normalize_all(extract_rows(arr_payload, "arrivals"),
                             "Arrival")
    if not departures and not arrivals:
        # FIH has ~50 movements a day; an all-empty answer is a feed
        # problem, not a fact. Keep the previous board rather than
        # blanking it.
        raise FetchError("AeroDataBox returned no flights at all")
    return window_start, window_end, departures, arrivals


def run(api_key, now):
    """One fetch cycle. Returns a process exit code; the fail-safe
    paths return 0 without touching any file."""
    if not DATA_FILE.exists():
        print(f"ERROR: {DATA_FILE} not found.", file=sys.stderr)
        return 1
    board = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    try:
        window_start, window_end, departures, arrivals = fetch_board(
            api_key, now)
    except FetchError as exc:
        print(f"WARNING: flights fetch aborted ({exc}) — previous "
              "flights board kept.", file=sys.stderr)
        return 0
    except ValueError as exc:
        print(f"WARNING: flights fetch aborted ({exc}) — previous "
              "flights board kept.", file=sys.stderr)
        return 0

    previous = board.get("flightsBoard")
    if not isinstance(previous, dict):
        previous = {}
    day_start = window_start.replace(hour=0, minute=0)
    block = {
        "checkedAt": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "AeroDataBox",
        "departures": merge_rows(previous.get("departures"), departures,
                                  day_start, window_end),
        "arrivals": merge_rows(previous.get("arrivals"), arrivals,
                                day_start, window_end),
    }
    board["flightsBoard"] = block
    DATA_FILE.write_text(
        json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Flights board OK: {len(block['departures'])} departures, "
          f"{len(block['arrivals'])} arrivals, window "
          f"{fmt_local(window_start)} → {fmt_local(window_end)} (Kinshasa), "
          f"checkedAt {block['checkedAt']}.")

    # Regenerate index.html so the baked page data carries the block
    # (build.py --skip-overpass preserves it). A failure here is a
    # real build error, not a data failure: fail loudly.
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--skip-overpass"],
        cwd=str(ROOT), capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print("ERROR: index.html regeneration failed.", file=sys.stderr)
        return 1
    return 0


def main():
    api_key = os.environ.get("RAPIDAPI_KEY", "").strip()
    if not api_key:
        print("WARNING: RAPIDAPI_KEY is not set — flights fetch "
              "skipped, previous flights board kept.", file=sys.stderr)
        return 0
    return run(api_key, datetime.now(timezone.utc))


if __name__ == "__main__":
    sys.exit(main())
