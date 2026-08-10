# Decision: Make the graph a useful decomposition tool via Canvas + Bases + coloured graph

**Date**: 2026-07-28

## Decision

The Obsidian Graph View "just sat there" — a force-directed blob nobody used. Replace that
passive picture with three complementary, native-Obsidian surfaces (no community plugins;
Canvas and Bases are already-enabled core plugins), all fed by richer frontmatter from the
existing code-note generator:

1. **Curated Canvas** — `claude_docs/architecture/system-decomposition.canvas`: the four
   modules as coloured groups laid out along the data pipeline, the shared SQLite store, and
   labelled data-flow edges (`ohlcv_history`, `backtest results`, `validated strategy`,
   `degradation → restart`). The diagram you *present* as decomposition evidence.
2. **Bases dashboards** — `claude_docs/dashboards/code-map.base` (files by module / layer /
   imports, + a tests-by-module coverage view; embedded in `[[_code]]`) and
   `decisions.base` (the design-reasoning log). Live tables you *query*.
3. **Graph colour-groups** — `.obsidian/graph.json` tinted by the `module/*` tags so the
   graph is a legible module map.
4. **Enriched generator** — `scripts/sync_obsidian_graph.py` now writes
   `module / layer / kind / package / imports_count` + a `module/<module>` tag into every
   code note. One change powers the Bases filters, graph colours, and Canvas classification.
5. **Vendored the `kepano/obsidian-skills`** for `json-canvas`, `obsidian-bases`,
   `obsidian-markdown` into `.claude/skills/` so future agent edits author these formats
   correctly.

## Reason

The `graphify-evaluation` / `2026-07-27-obsidian-code-graph-companion-notes` work put the
code *into* the graph, but a graph is a discovery view, not a decomposition artefact — you
can't present a blob to a marker or query it. Canvas gives a deliberate, stable picture;
Bases give queryable structure that never goes stale (they read live frontmatter); graph
colours make the existing view legible for free. Building on core plugins keeps it
dependency-free and shareable via git. Splitting "curated" (Canvas) from "auto" (Bases,
colours) means the presentation diagram survives regeneration while the data stays current.

## Alternatives Considered

- **A `modules.base`** (planned) — dropped: the module docs carry no status frontmatter to
  query. Folded a *tests-by-module coverage* view into `code-map.base` instead, which is
  genuinely useful.
- **`npx skills add` to install the skills** — rejected in this environment: it prompts
  interactively. Vendored the three needed skills with `gh` instead (version-pinned, no
  network at runtime).
- **Auto-generating the Canvas on every commit** — rejected: it would clobber manual layout.
  The Canvas is seeded once, then hand-maintained; the hook never touches it.
- **The Code Graph community plugin** (see `2026-07-27-...`) — still deferred: richer/live
  but a separate view, desktop-only, and its 5.8 MB `main.js` breaks Obsidian Sync Standard.

## Related

- MOC: [[_architecture]]
- [[decomposition]] — how to read the three surfaces
- [[2026-07-27-obsidian-code-graph-companion-notes]] — the code graph this builds on
- [[graphify-evaluation]]
