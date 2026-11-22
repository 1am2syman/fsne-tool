# Agent instructions

## UI scope and validation
- Use light mode for all UI work and screenshots. This app does not need dark mode; do not implement, refine, or validate it.
- Design and validate UI changes for widescreen laptop layouts only.
- Validate UI changes with screenshots at widescreen laptop widths only. The e2e suites (`tests/e2e_*`) are out of scope for UI work unless the user explicitly requests them.
- Mobile and tablet layouts, responsive refinements, and mobile validation are out of scope unless the user explicitly requests them.

## Preview must stay current
- The authoritative UI is `ui/template.html`, served by the normal app server at `https://sazzadvps.taildd3bd9.ts.net/fsne-ui/`. There are no separate overview prototypes.
- Edit `ui/template.html`; `ui/index.html` is generated.
- After template-only edits (`ui/template.html`, `ui/onboarding.js`, `ui/doh_overlay.js`), reload a completed live run. For the bundled sample, run `uv run python -m ui.rerender ui/index.html` to keep its existing data; use the full build only if the sample page is absent.
- After data/pipeline edits, run `POLARS_MAX_THREADS=2 uv run python ui/build.py` in the background. See README for the movement history inputs. Previous settled months and completed runs keep their frozen calculations and snapshot prices.
- Publish the normal localhost app with the `preview` helper so HTML and the run API share a route. The server compresses large HTML responses; no temporary gzip server is needed.
- Validate the canonical URL after publishing and print it in the reply.
