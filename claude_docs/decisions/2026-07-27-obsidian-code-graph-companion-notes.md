# Decision: Render the codebase in the Obsidian graph via generated companion notes + a commit hook

**Date**: 2026-07-27

## Decision

Wire the `.py` source into the native Obsidian graph and keep it fresh automatically:

1. **Generator** — `scripts/sync_obsidian_graph.py` (stdlib `ast` only) walks `src/`,
   `config/`, `scripts/`, `tests/` and writes one small markdown **companion note** per
   file under `claude_docs/code/`. Each note links to the real `.py`, lists its internal
   imports as wikilinks (so the import graph becomes real edges), and links up to its
   module doc and the new `[[_code]]` MOC. Notes are auto-generated — never hand-edited —
   and the generator is idempotent and prunes notes for deleted/renamed sources.
2. **Auto-sync** — a committed `scripts/hooks/pre-commit` runs the generator and stages
   `claude_docs/code/` on every commit. Activated once per clone with
   `git config core.hooksPath scripts/hooks`.
3. **Link hygiene** — fixed the drifted `.md` graph at the same time: added the orphaned
   `[[_trials]]` and new `[[_code]]` to the dashboard, indexed the missing
   `[[2026-06-27-screener-error-recovery-fallback]]` ADR, standardised every MOC
   `**Across**` line, and repaired the broken `[[calibration_tests]]` link (its target
   lives in `.claude/`, a dotfolder Obsidian won't index).

The **Code Graph** community plugin was evaluated and **deferred** (see below).

## Reason

The `graphify-evaluation` ADR deferred auto-graphing the code "until after Module 3, >40
files, cross-module tracing needed" — all now true. We wanted the code *in* the graph, but
**Obsidian only parses wikilinks inside `.md` files**, so a `.py` can be linked *to* but can
never emit edges. A markdown companion note is the only way to express code→code import
edges in the native graph.

Generated-notes-in-git was chosen over a plugin because it is **durable and shareable**: the
graph is versioned, works on mobile and Obsidian Publish, needs no plugin, and merges the
code cluster into the existing hand-built `claude_docs/` MOC structure — docs and code in
one map. A git hook (rather than a manual script or a Claude Code hook) means it stays fresh
for **all** edits — mine or done by hand — and can never silently go stale.

## Alternatives Considered

- **Minimal `[[file.py]]` links from the module docs only** — rejected: code files show as
  dumb leaf nodes with no edges between them; doesn't reveal how modules connect.
- **Graphify skill** (`graphify-evaluation`) — still deferred: sends docs to an external API
  under its own key, and its `graph.json` is a derived artefact that goes stale against the
  authoritative `claude_docs/`.
- **Code Graph community plugin** — deferred as an *optional complement*, not the backbone.
  It is genuinely good: fully local (no API — this answers Graphify's main objection),
  live-parsed so never stale, and richer (tree-sitter call/inheritance/symbol edges,
  TODO/FIXME, and `@adr`/`@tested-by`/`@depends-on` doc tags that fit our decision-log
  culture). But it renders its **own separate view** rather than the native graph, is
  **desktop-only**, and its ~5.8 MB `main.js` **exceeds Obsidian Sync Standard's 5 MB cap**
  (Sync is enabled in this vault). Worth trialling later for deep code exploration; not a
  substitute for a git-tracked, everywhere-available graph.
- **Manual generator script only / Claude Code PostToolUse hook** — rejected: the first
  relies on remembering to run it; the second only fires when Claude edits and misses manual
  changes and new files.

## Related

- MOC: [[_architecture]]
- [[graphify-evaluation]]
- [[_code]]
