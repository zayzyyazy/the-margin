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

## V3 additions

| File | Does |
|---|---|
| `margin/world.py` | "The World, Explained": clusters general-news items into developments, chooses by coverage + impact + geographic spread + novelty, reads the articles, drafts with a model, checks, and falls back to an attributed brief. |
| `margin/world_sources.yaml` | 8 general-news feeds (live-tested 2026-10-21). |
| `margin/llm.py`, `margin/models.yaml` | Model calls behind a hard budget guard. Paid models are off (`paid_approved: false`). |
| `margin/notion.py` | Read-only Notion queries for the private pages (Active projects, Learning notes). Never used by the public build or by any model. |
| `private/` | The private edition: one Vercel Python function with password sign-in. Deploy with `tools/deploy_private.sh`. |
| `assets/app.js` | Saved reading + the paged reader (spreads, keys, swipe, contents with page numbers). |

## Private edition — setup (secrets go into Vercel, never into this repo or a chat)

1. **Notion integration (read-only).** notion.so/profile/integrations → *New integration* → type *Internal*, workspace = your
   Work & Growth workspace → *Capabilities*: tick **Read content** only (untick Update/Insert content, no user information) → Save →
   copy the *Internal Integration Secret*.
2. **Share exactly two databases with it.** In Notion open **Projects** (the database) → ••• → *Connections* → add the integration.
   Repeat for **Learning Notes**. Nothing else needs access.
3. **Enter three secrets in Vercel.** vercel.com → project **the-margin-private** → *Settings* → *Environment Variables* → for each,
   Environment = **Production**, mark **Sensitive**:
   - `NOTION_TOKEN` — the integration secret from step 1
   - `MARGIN_PASSWORD` — a long password you choose (the login is rate-limited, but per server instance)
   - `MARGIN_SESSION_KEY` — 64 random characters, e.g. paste the output of `openssl rand -hex 32` run in your own terminal
4. **Redeploy** so the function sees them: project → *Deployments* → latest → ••• → *Redeploy* (or `tools/deploy_private.sh`).
5. Open https://the-margin-private.vercel.app → sign in.

To revoke: delete the integration in Notion, or change `MARGIN_SESSION_KEY` in Vercel and redeploy (signs every browser out).
