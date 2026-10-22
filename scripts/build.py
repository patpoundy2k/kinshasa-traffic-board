#!/usr/bin/env python3
"""
Kinshasa Traffic Board — static site generator.

Fetches the real road geometry for Kinshasa's 10 main arteries from
OpenStreetMap via the Overpass API, merges it with the curated roadworks
list (researched from Congolese press, each item carrying its source and
date), and writes:

  - data/board.json              (arteries GeoJSON + roadworks + metadata)
  - index.html                   (rendered from templates/index.template.html
                                  with the same data baked in, so search
                                  engines and AI crawlers see real content
                                  without running JavaScript)
  - sitemap.xml                  (lastmod refreshed)

Live data that does NOT need this generator (fetched by the browser at
view time, keyless): weather from Open-Meteo.
Crowd reports on the public site are stored per-visitor in localStorage
(a static page cannot share them); shared live reporting lives in the app.

Uses only the Python 3 standard library so it runs unattended anywhere,
including GitHub Actions.

Usage:
  python3 scripts/build.py [--skip-overpass]
    --skip-overpass   Reuse the geometry already in data/board.json
                      (offline rebuild of the page only).
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "board.json"
TEMPLATE_FILE = ROOT / "templates" / "index.template.html"
INDEX_FILE = ROOT / "index.html"
SITEMAP_FILE = ROOT / "sitemap.xml"

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]
USER_AGENT = "KinshasaTrafficBoard/1.0 (static site build; contact: patpoundy2kb@gmail.com)"

# Kinshasa bounding box (south, west, north, east) — generous, covers
# Ndjili airport in the east and the Matadi road leaving to the southwest.
BBOX = "-4.50,15.10,-4.20,15.60"

# The 10 main arteries. "osm_name_regex" matches how the road is named in
# OpenStreetMap; "peak" is a 0..1 weight for how jam-prone the artery is
# at peak hours (used by the page's *typical* congestion model — it is a
# pattern model, never live data, and the page labels it as such).
ARTERIES = [
    {
        "id": "bd-30-juin",
        "name": "Boulevard du 30 Juin",
        "osm_name_regex": "30 Juin",
        "zones_fr": "Gombe — centre-ville, ministères et ambassades",
        "zones_en": "Gombe — downtown, ministries and embassies",
        "peak": 0.85,
    },
    {
        "id": "bd-lumumba",
        "name": "Boulevard Lumumba",
        "osm_name_regex": "Lumumba",
        "zones_fr": "Limete, Kingabwa, Masina — axe est vers l'aéroport de Ndjili",
        "zones_en": "Limete, Kingabwa, Masina — eastern axis toward Ndjili Airport",
        "peak": 0.95,
    },
    {
        "id": "rn1-matadi",
        "name": "RN1 — Route de Matadi",
        "osm_name_regex": "Matadi|^N1$|Nationale 1",
        "zones_fr": "Ngaliema, Selembao — sortie sud-ouest vers Matadi",
        "zones_en": "Ngaliema, Selembao — southwest exit toward Matadi",
        "peak": 0.8,
    },
    {
        "id": "av-liberation",
        "name": "Avenue de la Libération (ex-24 Novembre)",
        "osm_name_regex": "Libération|24 Novembre",
        "zones_fr": "Kasa-Vubu, Ngiri-Ngiri, Bandalungwa — axe nord-sud",
        "zones_en": "Kasa-Vubu, Ngiri-Ngiri, Bandalungwa — north–south axis",
        "peak": 0.9,
    },
    {
        "id": "av-elengesa-bypass",
        "name": "Avenue Elengesa / By-Pass",
        "osm_name_regex": "Elengesa|By-Pass|Bypass",
        "zones_fr": "Mont Ngafula, Makala, Ngaba — rocade sud",
        "zones_en": "Mont Ngafula, Makala, Ngaba — southern bypass",
        "peak": 0.75,
    },
    {
        "id": "poids-lourds",
        "name": "Route des Poids Lourds",
        "osm_name_regex": "Poids Lourd",
        "zones_fr": "Limete, Kingabwa — desserte portuaire et industrielle",
        "zones_en": "Limete, Kingabwa — port and industrial access",
        "peak": 0.7,
    },
    {
        "id": "bd-sendwe",
        "name": "Boulevard Sendwe",
        "osm_name_regex": "Sendwe",
        "zones_fr": "Kalamu, Kasa-Vubu — stade des Martyrs",
        "zones_en": "Kalamu, Kasa-Vubu — Martyrs Stadium area",
        "peak": 0.85,
    },
    {
        "id": "bd-triomphal",
        "name": "Boulevard Triomphal",
        "osm_name_regex": "Triomphal",
        "zones_fr": "Kasa-Vubu, Lingwala — Palais du Peuple",
        "zones_en": "Kasa-Vubu, Lingwala — Palais du Peuple",
        "peak": 0.8,
    },
    {
        "id": "av-universite",
        "name": "Avenue de l'Université",
        "osm_name_regex": "Université",
        "zones_fr": "Lemba, Ngaba — Université de Kinshasa (UNIKIN)",
        "zones_en": "Lemba, Ngaba — University of Kinshasa (UNIKIN)",
        "peak": 0.75,
    },
    {
        "id": "av-kasa-vubu",
        "name": "Avenue Kasa-Vubu",
        "osm_name_regex": "Kasa-Vubu",
        "zones_fr": "Kasa-Vubu, Ngiri-Ngiri, Bandalungwa",
        "zones_en": "Kasa-Vubu, Ngiri-Ngiri, Bandalungwa",
        "peak": 0.8,
    },
]

# Roadworks researched from Congolese press (Oct 2026). Each item carries
# its source and date and is labelled "reported" — never presented as
# live sensor data. Points are approximate locations for map pins.
ROADWORKS = [
    {
        "id": "deuxieme-republique",
        "status": "active",
        "title_fr": "Route Deuxième République — réhabilitation (Kimbanseke)",
        "title_en": "Deuxième République Road — rehabilitation (Kimbanseke)",
        "detail_fr": "Réhabilitation et construction de 9,16 km, lancées le 13 octobre 2026 pour 18 mois. Entreprise : Shandong Construction International, supervision ACGT. Chantier actif.",
        "detail_en": "Rehabilitation and construction of 9.16 km, launched 13 October 2026 for 18 months. Contractor: Shandong Construction International, oversight ACGT. Works active.",
        "source": "Presse congolaise (lancement officiel, ministère des ITP)",
        "date": "2026-10-13",
        "points": [[-4.386, 15.418]],
    },
    {
        "id": "pont-ngunza",
        "status": "stopped",
        "title_fr": "Pont Ngunza — travaux à l'arrêt (av. Pierre Elengesa, vers le By-Pass)",
        "title_en": "Pont Ngunza — works stopped (Pierre Elengesa Ave., toward the By-Pass)",
        "detail_fr": "Travaux de modernisation à l'arrêt depuis environ 5 mois, site déserté selon la presse. Circulation difficile signalée aux abords.",
        "detail_en": "Modernisation works stopped for about 5 months, site deserted according to press reports. Difficult traffic reported nearby.",
        "source": "Le Potentiel",
        "date": "2026-10-21",
        "points": [[-4.371, 15.302]],
    },
    {
        "id": "barumbu-drainage",
        "status": "stalled",
        "title_fr": "Drainage presque à l'arrêt — av. Kasaï, Croix-Rouge, Bas-Congo, Bokasa",
        "title_en": "Drainage works nearly stopped — Kasaï, Croix-Rouge, Bas-Congo, Bokasa avenues",
        "detail_fr": "Travaux d'assainissement et de drainage pratiquement à l'arrêt dans les communes de Barumbu, Kasa-Vubu et Ngiri-Ngiri, selon des rapports de mi-octobre 2026.",
        "detail_en": "Sanitation and drainage works practically stopped in the Barumbu, Kasa-Vubu and Ngiri-Ngiri communes, according to mid-October 2026 reports.",
        "source": "Rapports de presse congolaise",
        "date": "2026-10-14",
        "points": [[-4.312, 15.319]],
    },
    {
        "id": "masina-limete",
        "status": "progressing",
        "title_fr": "Liaison Masina–Limete (Lubuaku–Ndanu) — travaux en cours",
        "title_en": "Masina–Limete link road (Lubuaku–Ndanu) — works progressing",
        "detail_fr": "Travaux en progression sur la liaison Masina–Limete ; un pont est prévu sur le tracé.",
        "detail_en": "Works progressing on the Masina–Limete link; a bridge is planned on the route.",
        "source": "Presse congolaise (rapport de terrain)",
        "date": "2026-10-11",
        "points": [[-4.392, 15.386]],
    },
    {
        "id": "spots-degrades",
        "status": "degraded",
        "title_fr": "Points dégradés — Landu × Birmanie (Bumbu) et Libération × Landu",
        "title_en": "Degraded spots — Landu × Birmanie (Bumbu) and Libération × Landu",
        "detail_fr": "Sections de chaussée dégradées créant des embouteillages aux heures de pointe, signalées début octobre 2026.",
        "detail_en": "Degraded road sections creating peak-hour jams, reported in early October 2026.",
        "source": "MediaCongo",
        "date": "2026-10-05",
        "points": [[-4.356, 15.287], [-4.341, 15.301]],
    },
]


# Congo River items researched from Congolese press and official/UN
# reports (Oct 2026). Same discipline as the roadworks: every item carries
# its source, source URL and date, and is labelled "reported" — there is
# no live river-level or vessel feed for Kinshasa, and the page says so.
RIVER = [
    {
        "id": "baleinieres-bois-interdites",
        "tag_fr": "Navigation",
        "tag_en": "Navigation",
        "title_fr": "Baleinières en bois interdites à partir du 15 novembre 2026",
        "title_en": "Wooden baleinières banned from 15 November 2026",
        "detail_fr": "Le ministère des Transports interdit les baleinières en bois sur le fleuve Congo et l'ensemble des voies navigables dès le 15 novembre 2026 ; les armateurs disposent d'un mois pour les remplacer par des embarcations conformes.",
        "detail_en": "The Ministry of Transport bans wooden baleinières (whaleboats) on the Congo River and all waterways from 15 November 2026; owners have one month to replace them with compliant vessels.",
        "source": "Bankable",
        "source_url": "https://bankable.africa/en/climat-des-affaires/0910-3766-navigation-kinshasa-donne-un-mois-aux-armateurs-pour-remplacer-leurs-baleinieres-en-bois",
        "date": "2026-10-09",
    },
    {
        "id": "crue-quartiers-riverains",
        "tag_fr": "Crue",
        "tag_en": "Flooding",
        "title_fr": "Le fleuve Congo déborde sur les quartiers riverains",
        "title_en": "Congo River overflows into riverside neighbourhoods",
        "detail_fr": "Après de fortes pluies, le fleuve est sorti de son lit : Kingabwa et la Cité du Fleuve (Limete) ainsi que Pompage (Ngaliema) sont inondés, et des habitants ont abandonné leurs maisons.",
        "detail_en": "After heavy rains, the river burst its banks: Kingabwa and Cité du Fleuve (Limete) as well as Pompage (Ngaliema) are flooded, and residents have left their homes.",
        "source": "Le Quotidien RDC",
        "source_url": "https://lequotidienrdc.com/kinshasa-le-fleuve-congo-deborde-sur-quelques-quartiers-cotiers/",
        "date": "2026-10-21",
    },
    {
        "id": "beach-ngobila-controle-sanitaire",
        "tag_fr": "Traversée",
        "tag_en": "Crossing",
        "title_fr": "Beach Ngobila — le bouclier sanitaire se renforce à la traversée",
        "title_en": "Beach Ngobila — health screening strengthened at the crossing",
        "detail_fr": "Au Beach Ngobila, principal point de traversée entre Kinshasa et Brazzaville, la technologie renforce le contrôle sanitaire entre les deux capitales, selon l'OMS.",
        "detail_en": "At Beach Ngobila, the main crossing point between Kinshasa and Brazzaville, technology is strengthening health screening between the two capitals, according to WHO.",
        "source": "ReliefWeb (OMS)",
        "source_url": "https://reliefweb.int/report/democratic-republic-congo/au-beach-ngobila-de-kinshasa-la-technologie-renforce-le-bouclier-sanitaire-entre-kinshasa-et-brazzaville",
        "date": "2026-10-19",
    },
    {
        "id": "pont-route-rail-brazzaville",
        "tag_fr": "Travaux",
        "tag_en": "Works",
        "title_fr": "Pont route-rail Kinshasa–Brazzaville — travaux prévus dès juin 2027",
        "title_en": "Kinshasa–Brazzaville road-rail bridge — works planned from June 2027",
        "detail_fr": "L'accord signé le 9 octobre 2026 avec Africa50 prévoit le démarrage des travaux du pont route-rail entre les deux capitales en juin 2027.",
        "detail_en": "Under the agreement signed on 9 October 2026 with Africa50, works on the road-rail bridge between the two capitals are planned to start in June 2027.",
        "source": "Congo Quotidien",
        "source_url": "https://www.congoquotidien.com/2026/10/12/accord-pont-rail-africa50-kinshasa-brazzaville/",
        "date": "2026-10-12",
    },
    {
        "id": "bac-popokabaka-reprise",
        "tag_fr": "Traversée",
        "tag_en": "Crossing",
        "title_fr": "Kwango — le bac de Popokabaka reprend après 4 ans d'arrêt",
        "title_en": "Kwango — Popokabaka ferry resumes after 4 years",
        "detail_fr": "Le trafic fluvial a repris au bac de Popokabaka, sur la rivière Kwango, après quatre ans d'interruption ; ce bac alimente le corridor de la RN16.",
        "detail_en": "River traffic has resumed at the Popokabaka ferry on the Kwango River after a four-year shutdown; the ferry feeds the RN16 corridor.",
        "source": "Actualite.cd (via Congo Quotidien)",
        "source_url": "https://www.congoquotidien.com/2026/10/10/reprise-bac-popokabaka-riviere-kwango/",
        "date": "2026-10-10",
    },
]


def overpass_query(regex):
    """Return the list of OSM ways whose name matches `regex` in the bbox."""
    ql = (
        "[out:json][timeout:60];"
        f'way["name"~"{regex}",i]({BBOX});'
        "out geom;"
    )
    data = urllib.parse.urlencode({"data": ql}).encode()
    last_exc = None
    for url in OVERPASS_URLS:
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — try the next mirror
            last_exc = exc
    raise last_exc


def ways_to_lines(elements):
    """Convert Overpass way geometries to simplified GeoJSON coordinates."""
    lines = []
    for el in elements:
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = []
        for pt in el["geometry"]:
            pair = [round(pt["lon"], 5), round(pt["lat"], 5)]
            if not coords or coords[-1] != pair:
                coords.append(pair)
        if len(coords) >= 2:
            lines.append(coords)
    return lines


def centre_of(lines):
    pts = [p for line in lines for p in line]
    if not pts:
        return None
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    return [round(lat, 5), round(lon, 5)]


def fetch_geometry(previous):
    """Fetch geometry for every artery; fall back to previous geometry."""
    features = []
    report = []
    for art in ARTERIES:
        lines = []
        try:
            result = overpass_query(art["osm_name_regex"])
            lines = ways_to_lines(result.get("elements", []))
        except Exception as exc:  # noqa: BLE001 — report and fall back
            report.append(f"  ! {art['id']}: Overpass error: {exc}")
        if not lines and previous and art["id"] in previous:
            lines = previous[art["id"]]
            report.append(f"  ~ {art['id']}: reused previous geometry ({len(lines)} segments)")
        pts = sum(len(l) for l in lines)
        report.append(f"  - {art['id']}: {len(lines)} segments, {pts} points")
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": art["id"],
                    "name": art["name"],
                    "zones_fr": art["zones_fr"],
                    "zones_en": art["zones_en"],
                    "peak": art["peak"],
                    "centre": centre_of(lines),
                },
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": lines,
                },
            }
        )
        time.sleep(1)  # be polite to the shared Overpass instance
    return features, report


def load_previous_geometry():
    if not DATA_FILE.exists():
        return {}
    try:
        old = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        out = {}
        for feat in old.get("arteries", {}).get("features", []):
            out[feat["properties"]["id"]] = feat["geometry"]["coordinates"]
        return out
    except Exception:  # noqa: BLE001
        return {}


def arteries_list_html(features):
    """Baked-in (French, default language) artery list for SEO/no-JS."""
    items = []
    for feat in features:
        p = feat["properties"]
        items.append(
            f'        <li><strong>{p["name"]}</strong> — {p["zones_fr"]}</li>'
        )
    return "\n".join(items)


def roadworks_list_html():
    items = []
    for rw in ROADWORKS:
        items.append(
            "        <li>"
            f'<strong>{rw["title_fr"]}</strong><br>{rw["detail_fr"]}<br>'
            f'<em>Source : {rw["source"]} — {rw["date"]} (signalé, à confirmer sur place)</em>'
            "</li>"
        )
    return "\n".join(items)


def river_list_html():
    items = []
    for rv in RIVER:
        items.append(
            "        <li>"
            f'<span class="pill p-river">{rv["tag_fr"]}</span> '
            f'<strong>{rv["title_fr"]}</strong><br>{rv["detail_fr"]}<br>'
            f'<em>Source : <a href="{rv["source_url"]}" target="_blank" rel="noopener">{rv["source"]}</a>'
            f' — {rv["date"]} (signalé, à confirmer sur place)</em>'
            "</li>"
        )
    return "\n".join(items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-overpass", action="store_true",
                    help="reuse geometry already in data/board.json")
    args = ap.parse_args()

    previous = load_previous_geometry()
    if args.skip_overpass:
        features = []
        for art in ARTERIES:
            lines = previous.get(art["id"], [])
            features.append({
                "type": "Feature",
                "properties": {
                    "id": art["id"], "name": art["name"],
                    "zones_fr": art["zones_fr"], "zones_en": art["zones_en"],
                    "peak": art["peak"], "centre": centre_of(lines),
                },
                "geometry": {"type": "MultiLineString", "coordinates": lines},
            })
        print("Skipped Overpass; reused previous geometry.")
    else:
        print("Fetching artery geometry from OpenStreetMap (Overpass)…")
        features, report = fetch_geometry(previous)
        print("\n".join(report))

    missing = [f["properties"]["id"] for f in features
               if not f["geometry"]["coordinates"]]
    if missing:
        print(f"WARNING: no geometry for: {', '.join(missing)}", file=sys.stderr)

    board = {
        "generated": date.today().isoformat(),
        "city": {"name": "Kinshasa", "lat": -4.325, "lon": 15.322},
        "arteries": {"type": "FeatureCollection", "features": features},
        "roadworks": ROADWORKS,
        "river": RIVER,
        "dataNotes": {
            "congestion": "Typical pattern model by time of day (Africa/Kinshasa) — NOT live traffic data. No free live traffic feed exists for Kinshasa.",
            "towers": "Cell towers are fetched live by the page from OpenStreetMap via Overpass: community-mapped data, possibly incomplete.",
            "roadworks": "Researched from Congolese press; each item carries its source and date.",
            "river": "Researched from Congolese press and official/UN reports; each item carries its source, source URL and date. No live river-level or vessel feed exists for Kinshasa.",
            "reports": "On this public website, reports are stored only in the visitor's own browser (localStorage). Shared live reporting is in the Kinshasa Traffic Board app.",
        },
    }

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"Wrote {DATA_FILE} ({DATA_FILE.stat().st_size} bytes)")

    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    page = (
        template
        .replace("{{GENERATED_DATE}}", board["generated"])
        .replace("{{ARTERIES_LIST_HTML}}", arteries_list_html(features))
        .replace("{{ROADWORKS_LIST_HTML}}", roadworks_list_html())
        .replace("{{RIVER_LIST_HTML}}", river_list_html())
        .replace("{{BOARD_JSON}}", json.dumps(board, ensure_ascii=False))
    )
    INDEX_FILE.write_text(page, encoding="utf-8")
    print(f"Wrote {INDEX_FILE} ({INDEX_FILE.stat().st_size} bytes)")

    if SITEMAP_FILE.exists():
        text = SITEMAP_FILE.read_text(encoding="utf-8")
        import re
        text = re.sub(r"<lastmod>.*?</lastmod>",
                      f"<lastmod>{board['generated']}</lastmod>", text)
        SITEMAP_FILE.write_text(text, encoding="utf-8")
        print("Refreshed sitemap.xml lastmod.")


if __name__ == "__main__":
    main()
