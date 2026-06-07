# Decision: Structured Context System

**Date**: 2026-04-13

## Decision

Replace the monolithic `.claude/PROJECT_CONTEXT.md` with a layered, modular documentation system inside `claude_docs/`. The system has grown from a single-file context dump into a multi-module codebase requiring context-specific documentation for different work areas.

## Reason

The original `.claude/PROJECT_CONTEXT.md` contained:
- Project-level rules and architecture (needed by every module)
- Module-specific specs scattered throughout (module1-4)
- Testing methodology across three separate files
- A 20k+ token `PLANNED_IMPROVEMENTS.md` that mixed planning with implementation specs

Loading this single file for any task wastes context on irrelevant modules and makes it hard to find specific guidance. Different tasks (working on Module 2 backtest vs Module 4 execution) need different subsets of documentation.

## Alternatives Considered

### 1. Keep single file with section anchors
**Rejected**: Still requires loading entire file to navigate. Section anchors help humans but don't help coding models that typically load context all at once.

### 2. Separate files per concern, flat structure
**Rejected**: A flat list of files (architecture.md, testing.md, module1.md, module2.md...) still makes it hard to discover what exists. Layered structure with clear directories is more maintainable.

### 3. Full Confluence/Notion-style wiki
**Rejected**: Over-engineered for this project. Markdown files in the repo are more portable and version-controlled alongside the code.

### 4. Modular system in claude_docs/ (chosen)
**Why**: Clear hierarchy, each file has one responsibility, easy to find and load only what's relevant. Works in Obsidian for the human and in Claude Code context for the AI.

## Structure Created

```
claude_docs/
├── architecture/
│   └── overview.md              # Project purpose, components, data flow, state
├── modules/
│   ├── data_pipeline.md         # Module 1: responsibilities, files, schema, IO
│   ├── backtesting.md           # Module 2: strategy spec contract, walk-forward, cost model
│   ├── agents.md                # Module 3: tool schemas, prompt versioning, Loop 1 flow
│   └── execution.md             # Module 4: risk agent, degradation monitor, Loop 2 flow
├── decisions/
│   └── 2026-04-13-structured-context-system.md  # This decision
├── standards/
│   ├── coding_rules.md          # Config over hardcoding, no circular imports, etc.
│   └── testing_rules.md         # Ablation methodology, isolation test requirements
└── tasks/
    └── improve_data_validation.md  # Current active task
```

## Consequences

**Positive**:
- Coding models can load only the relevant module doc for the task at hand
- Human developers get structured Obsidian-friendly notes
- Old `.claude/` files preserved for reference (not deleted per task request)
- Decision log enables future tracing of why architectural choices were made

**Neutral**:
- Two sources of truth until old `.claude/` files are eventually migrated or removed
- `CLAUDE.md` will be updated to route to new docs instead of `.claude/PROJECT_CONTEXT.md`

**Negative**:
- Need to remember to keep `claude_docs/` and `.claude/rules/modules/` in sync when module specs change
- Migration is not automatic — someone must update both when implementation changes


## Related

- MOC: [[_architecture]]
- [[2026-04-18-init-skill-claude-md-centralization]]
