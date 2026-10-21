# Kinshasa Traffic Board — public website package

A complete, publish-ready static website for the **Kinshasa Traffic Board**:
traffic in Kinshasa (DR Congo) at a glance — the 10 main arteries on a real
OpenStreetMap map, typical congestion by time of day, roadworks reported by
the Congolese press (with source and date), live weather, community-mapped
cell towers, and a report form. **Bilingual: French by default, EN|FR toggle**
(the choice is remembered in the visitor's browser).

This is the public companion to Pat's private, server-backed
**Kinshasa Traffic Board app** (Project Two). Hosting is **free**
(GitHub Pages) and the baked-in data **refreshes itself daily** via
GitHub Actions. SEO is switched on: content is baked into the static HTML,
and `robots.txt` explicitly welcomes all search engines **and** AI crawlers.

## What's in the package

| Path | What it is |
|---|---|
| `index.html` | The public page, with artery geometry, roadworks, meta/Open Graph tags and JSON-LD baked in. Generated — do not hand-edit (edit the template instead). |
| `templates/index.template.html` | The page template the generator fills in. All page behaviour (map, weather, towers, reports, FR/EN) lives here. |
| `data/board.json` | Artery GeoJSON (real OpenStreetMap road geometry) + the 5 curated roadworks + data-honesty notes. Refreshed daily. The page also embeds a copy inline, so it works even if the JSON fetch fails. |
| `scripts/build.py` | The generator (Python 3, standard library only). Fetches artery geometry from Overpass, rewrites `data/board.json`, `index.html` and the sitemap date. |
| `.github/workflows/update.yml` | Daily GitHub Actions job (~06:45 Africa/Kinshasa): run the generator → commit the fresh data → deploy to GitHub Pages. |
| `robots.txt` | Allows every crawler, including AI crawlers (GPTBot, ChatGPT-User, ClaudeBot, Claude-Web, PerplexityBot, Google-Extended, CCBot, …). |
| `sitemap.xml` | Sitemap with FR/EN hreflang alternates (lastmod refreshed by the generator). |

## How the page gets its data

- **Road geometry** — fetched at build time from OpenStreetMap via the
  Overpass API and baked into `data/board.json`, so viewing the page does
  not depend on Overpass for the roads.
- **Weather** — fetched live by the visitor's browser from the keyless
  Open-Meteo API (Kinshasa, −4.325, 15.322), with a rain / flood-risk note
  when heavy rain is likely.
- **Cell towers** — fetched live by the browser from Overpass when the
  visitor ticks the toggle (communication towers/masts around Kinshasa,
  operator name where OSM has it tagged).
- **Congestion colours** — computed in the browser from the current time
  in Kinshasa (Africa/Kinshasa): an hourly *typical* pattern per artery.
- **Roadworks** — a curated list researched from Congolese press, baked
  into the data file; every item shows its source and date.

## Run the generator locally

```bash
python3 scripts/build.py            # full rebuild (fetches Overpass)
python3 scripts/build.py --skip-overpass   # page-only rebuild, reuse geometry
```

If an Overpass query fails during a run, the generator keeps the previous
geometry for that artery instead of wiping it, and prints a warning.

Preview locally:

```bash
python3 -m http.server 8000 --directory .
# then open http://localhost:8000/
```

## Publish it on GitHub Pages (free, under your own account)

Publishing needs **your own free GitHub account** — the site and the daily
update job live under your account (same as the Toronto Rideshare Board).

1. **Create a new repository** named exactly **`kinshasa-traffic-board`**
   (public, no README needed) at <https://github.com/new>.
2. **Upload this package**: either push the folder with git —
   ```bash
   cd kinshasa-traffic-site
   git init && git add -A && git commit -m "Kinshasa Traffic Board site"
   git branch -M main
   git remote add origin https://github.com/patpoundy2k/kinshasa-traffic-board.git
   git push -u origin main
   ```
   — or upload the files through the GitHub web UI.
3. **Turn on Pages**: in the repo, **Settings → Pages → Build and
   deployment → Source: GitHub Actions**.
4. **Run the workflow once**: **Actions → Update Kinshasa board & deploy →
   Run workflow**. It regenerates the data, commits it, and deploys.
   After that it runs by itself every day at ~06:45 Kinshasa time.
5. The site goes live at
   **https://patpoundy2k.github.io/kinshasa-traffic-board/**

## Honest data limits — read before sharing the link

- **There is no live traffic data on this site, and none is claimed.**
  No free live traffic feed exists for Kinshasa. The artery colours are a
  *typical* hourly pattern model, labelled "habituel / typical — not live"
  on the page itself.
- **Reports are not shared between visitors on the website.** A static
  page has no server, so the report form saves to the visitor's own
  browser (localStorage), shows the report as a pin and list entry, and
  expires it after 4 hours. The page says this plainly in FR and EN.
  Shared live reporting exists in the Kinshasa Traffic Board app.
- **Cell towers are community-mapped** OpenStreetMap data and may be
  incomplete; OpenCelliD (the other big tower database) requires an
  account key, so it is not used. The page labels the tower layer as
  community data.
- **Roadworks come from press reports**, each with source and date; they
  are not an official works feed and should be confirmed on site. Edit
  the `ROADWORKS` list in `scripts/build.py` to add, update or remove
  items, then re-run the generator.
