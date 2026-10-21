# The Margin — V2 reading edition

A daily, finite reading edition: 6–8 existing articles chosen from RSS feeds, shown with the publisher's own excerpt and a link to the original. Part of `~/Projects/daily-paper` (V1 print paper and weekly editions live alongside it, untouched).

- **Runs by itself**: GitHub Actions, 07:00 Europe/Berlin daily (`.github/workflows/daily.yml`), deploys to GitHub Pages.
- **No paid services, no model calls**: selection is fixed code in `margin/select.py`.
- **Nothing personal**: no Notion data, profile or keys are in this repo.

## Pieces

| File | Does |
|---|---|
| `margin/sources.yaml` | 76 sources (51 from the RSS Research Library, 25 from V1); `active: true` ones are fetched. Every feed URL's origin is in `how`. |
| `margin/ingest.py` | Fetches feeds (ported from `weekly/collect.py`), cleans text, takes the publisher's excerpt. |
| `margin/select.py` | Filters, de-duplicates, scores and composes the edition. All rules are in the module docstring. |
| `margin/update.py` | One daily run: fetch → select → write `data/editions/<date>.json` + `data/status.json`. Never overwrites or deletes an edition. |
| `margin/build.py` | Static site → `_site/`: today, every past edition, archive, saved, sources. |
| `tools/discover.py` | Re-runs the source census (`reports/feed-validation.json`). |
| `tools/mobilecheck.mjs` | Phone-layout check through Chrome DevTools (overflow, text size, tap targets, fonts). |
| `tests/` | Offline tests: duplicates, volume caps, balance, failure recovery, archive, idempotency, the 07:00 rule. |

## Failure policy

An update fails — and the previous edition stays live, with a notice — if fewer than half the active feeds answer or fewer than 6 articles pass selection. The status line on every edition shows the last update and failing sources; `/sources/` lists each failure with its error. A failed run also marks the Actions job red, which GitHub emails to the repo owner.

## By hand

```sh
python -m unittest discover -s tests
python -m margin.update            # today's edition (skips if it exists; --force rebuilds)
python -m margin.build             # → _site/
gh workflow run daily.yml          # the real path: same job the schedule runs
```

## Adding a source

Add it to `tools/candidates.yaml` with the publisher's homepage (or a feed URL the publisher states), run `python tools/discover.py`, and copy the tested result into `margin/sources.yaml`. Don't type feed URLs from memory.

Fonts: Source Serif 4 and Archivo, SIL Open Font License, self-hosted in `assets/fonts/`.
