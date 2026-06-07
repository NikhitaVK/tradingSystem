# Decision: Graphify Evaluation

**Date**: 2026-04-13
**Task**: `claude_docs/tasks/active/evaluate-graphify`

## Summary

**Decision: DEFER**

Graphify is a legitimate, well-engineered tool. It could meaningfully reduce context overhead for a project this size. However, the timing is wrong given the project's current phase, and the existing `claude_docs/` system already covers the core navigation problem it would solve. Defer until after Module 3 is complete.

---

## What Graphify Is

Graphify (safishamsi/graphify, 22k+ GitHub stars) is a Claude Code skill that converts codebases into queryable knowledge graphs. It uses:

1. **AST Pass (tree-sitter)**: Extracts code structure — classes, functions, imports, call graphs — entirely locally. No LLM needed for this step.
2. **LLM Pass (Claude subagents)**: Extracts concepts and relationships from docs, papers, images.
3. **Leiden community detection**: Clusters the graph topologically (no embeddings required).
4. **Confidence tagging**: Every edge is tagged `EXTRACTED` (explicit in source), `INFERRED` (deduced), or `AMBIGUOUS` (flagged for human review).

**Output**: `graph.json` (queryable), interactive HTML graph, `GRAPH_REPORT.md`, and optionally an Obsidian vault.

**Token reduction claim**: 71.5x fewer tokens per query on a 52-file corpus. Scales with corpus size (6 files ~1x, 52 files 71x).

---

## How It Would Apply to This Codebase

### Graph Structure

Graphify would build the following for `tradingSystemv0.01`:

**Nodes**:
- File nodes: each `.py` file as a node
- Function/method nodes: `run_backtest()`, `build_signals()`, `compute_rsi()`, `run_loop1()`, etc.
- Concept nodes: "strategy spec", "degradation threshold", "walk-forward backtest", "ATR position sizing"
- Doc nodes: extracted content from `claude_docs/` files

**Edges**:
- `calls`: `loop1.py` → `strategy_agent.py`
- `imports`: `backtest/engine.py` → `data/schema.py`
- `uses_schema`: strategy_runner uses the strategy spec contract
- `triggers`: degradation monitor triggers Loop 1 restart
- `EXTRACTED`/`INFERRED` confidence on each

**Key relationships Graphify would reveal**:
- The `src/data/` → `src/backtest/` dependency via `ohlcv_history` table
- The strategy spec contract as a shared node between backtest engine and agents
- Loop 1 / Loop 2 orchestration paths through `loop1.py` / `loop2.py`
- MCP client fallback chain (TradingView MCP → TA-Lib)

### Token Reduction

Currently, to work on Module 2 (backtest engine), Claude Code loads:
- `claude_docs/tasks/module2_backtest_engine.md` (task-level)
- `claude_docs/modules/backtesting.md` (full module spec, ~150 lines)
- `claude_docs/architecture/overview.md` (architecture context)
- Relevant `.claude/rules/` files

This is ~500–800 tokens of context loaded manually per task.

With Graphify: a query like "how does the backtest engine compute degradation threshold" would return a graph traversal path rather than full doc files. Expected reduction: 5–10x for targeted queries on this codebase (smaller corpus than the 52-file benchmark, so 71.5x doesn't fully apply).

**Important caveat**: Graphify's token savings only materialize after the graph is built and you're querying it. The initial graph build sends all docs/images to the Anthropic API (code stays local). For `claude_docs/` files, that's ~2500 tokens per full build.

### Source of Truth

Graphify's source of truth is the generated `graph.json` — a persistent queryable index built from the current state of files. It uses SHA256 caching to rebuild only changed files.

This means **Graphify is a derived artefact, not the source**. The source remains the `.py` files, `.md` docs, and database schema. If `claude_docs/modules/backtesting.md` is updated but Graphify isn't re-run, the graph is stale. The confidence tagging (`EXTRACTED` vs `INFERRED`) is helpful for knowing what's authoritative vs. deduced.

This is a meaningful limitation for a project where `claude_docs/` is actively maintained as the authoritative documentation.

### Complement or Replace `claude_docs/`?

**Complement, not replace.**

Graphify extracts from existing docs but is not designed to be the authoritative authoring layer. The `claude_docs/` system will remain the primary documentation source because:
- Decisions, rationale, and architectural rules are authored there intentionally
- Task files (`claude_docs/tasks/*.md`) are ephemeral scratchpads that Graphify would pollute with stale references
- The existing decision log (`claude_docs/decisions/`) tracks *why* choices were made — Graphify captures *what* connects, not *why*

Graphify would add: fast structural navigation, cross-module dependency visibility, call-chain tracing.

`claude_docs/` would remain: architectural decisions, task context, testing methodology, human-oriented rationale.

### Integration Plan (for when deferred adoption happens)

**Step 1 — Install and initial build**:
```bash
pip install graphifyy && graphify install
cd 13DIT/tradingSystemv0.01
graphify . --obsidian
```
This produces `graphify-out/` with `graph.json`, HTML graph, and an Obsidian vault.

**Step 2 — Claude Code PreToolUse hook**:
`graphify claude install` adds a hook to `settings.json` that fires before Glob/Grep calls. When Claude Code tries to read files for context, Graphify intercepts and queries `graph.json` instead. This makes the knowledge graph the first point of navigation.

**Step 3 — Obsidian sync**:
The `--obsidian` flag produces a vault that can replace or merge with any manual Obsidian notes already in use. The existing `claude_docs/` structure would remain as the human-authored source; the Graphify Obsidian output would be a supplementary structural view.

**Step 4 — Git hook for auto-sync**:
```bash
graphify hook install
```
Adds a post-commit hook that rebuilds the graph on file changes. Keeps `graph.json` fresh.

**Step 5 — Task file exclusion**:
Graphify should skip `claude_docs/tasks/` from the build — these are ephemeral and would create stale graph nodes. Add to `.graphifyignore`:
```
claude_docs/tasks/
```

---

## What Would Be Gained

- **Instant structural awareness**: "which module calls `run_backtest`?" answered via graph query, not grep
- **Cross-module edge detection**: Graphify would automatically surface the data_pipeline → backtest → agents dependency chain that the human-maintained docs capture manually
- **Token reduction on targeted queries**: 5–10x for structural queries (vs loading full module docs)
- **Code change impact visibility**: after editing a function, graph shows what depends on it

---

## Why Defer

**1. Project phase**: Modules 2, 3, and 4 are not built yet. The codebase is small (~20 source files). Graphify's token savings scale with corpus size — on 6 files the reduction is ~1x, not 71x. The benefit is smaller now than it would be at full scale.

**2. Maintenance overhead**: The existing `claude_docs/` system requires manual sync when module specs change. Adding Graphify on top means syncing both. Until the module specs are stable (post-Module 3), this overhead isn't justified.

**3. `claude_docs/` already solves the navigation problem**: The structured context system (decisions 2026-04-13) was specifically implemented to reduce context overload. Module docs are already split by concern. The routing table in `CLAUDE.md` already sends you to the right doc. Graphify would layer a second navigation system on top of one that already works.

**4. Staleness risk**: Graphify's `graph.json` is a derived artefact. `claude_docs/` is the authoritative source. If we adopt Graphify now, we inherit a second system that needs active maintenance (rebuilds, sync hooks) before it has earned its keep.

**5. Doc/image API usage**: Every graph rebuild (on file changes) sends your `claude_docs/` content to Anthropic's API under Graphify's own key. For a project that cares about data locality and explicit API usage, this is worth being conscious of.

---

## Revisit When

- After Module 3 (Strategy Agents) is complete and the codebase has stabilized
- When working on Module 4 (Execution Loop) requires tracing cross-module dependencies across all four modules
- When the codebase exceeds ~40 source files and token context loading becomes consistently expensive
- When `claude_docs/` structure is stable and the overhead of maintaining two systems is offset by Graphify's navigation benefits

---

## Sources

- [Graphify GitHub (v2)](https://github.com/safishamsi/graphify/blob/v2/README.md)
- [Graphify Architecture](https://github.com/safishamsi/graphify/blob/v2/ARCHITECTURE.md)
- [Graphify AI Agents SkillsLLM page](https://skillsllm.com/skill/graphify)


## Related

- MOC: [[_architecture]]
- [[2026-04-15-finmem-layered-memory]]
