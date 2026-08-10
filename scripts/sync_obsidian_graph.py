"""
sync_obsidian_graph.py — Generate Obsidian "code note" companions for the graph.

Obsidian only parses wikilinks inside .md files, so .py files can never emit
links of their own. This script solves that: it walks the source tree, parses
each file's imports with the stdlib `ast` module, and writes one tiny markdown
companion note per file under claude_docs/code/. Each note links to the real
.py file, to the notes of the internal modules it imports (recreating the
import graph as real Obsidian nodes), and up to the relevant module doc.

The notes are a *derived artefact* — the .py files remain the source of truth.
Run this whenever code changes; the git pre-commit hook does it automatically.

Usage:
    python -m scripts.sync_obsidian_graph
"""

from __future__ import annotations

import ast
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
CODE_DIR = REPO_ROOT / "claude_docs" / "code"
SCAN_ROOTS = ["src", "config", "scripts", "tests"]

BANNER = "> ⚠️ **AUTO-GENERATED** by `scripts/sync_obsidian_graph.py` — do not edit by hand."

# Top-level package prefixes that count as "internal" imports worth linking.
INTERNAL_PREFIXES = ("src", "config", "scripts", "tests")


# ── Classification (module / layer / kind — drives Bases, graph colours, Canvas) ──
# Test files map to the module they exercise, for the coverage view.
TEST_MODULE = {
    "test_data_pipeline": "data_pipeline",
    "test_knowledge_base": "data_pipeline",
    "test_memory_feedback": "data_pipeline",
    "test_backtest": "backtesting",
    "test_empirical_search": "agents",
    "test_candidate_generator": "agents",
    "test_loop1": "agents",
    "test_loop2": "execution",
    "test_binance_live": "execution",
}

# The four real module docs (everything else has no dedicated module doc).
MODULE_DOCS = {"data_pipeline", "backtesting", "agents", "execution"}


def _is_execution(rel_path: str) -> bool:
    """Module 4 files live in src/agents/ and src/monitor/ but belong to execution."""
    name = rel_path.rsplit("/", 1)[-1]
    return (
        rel_path == "src/loop2.py"
        or rel_path.startswith("src/monitor/")
        or name in {"execution_agent.py", "risk_agent.py"}
    )


def _source_module(rel_path: str) -> str:
    if _is_execution(rel_path):
        return "execution"
    if rel_path.startswith("src/data/"):
        return "data_pipeline"
    if rel_path.startswith("src/backtest/"):
        return "backtesting"
    if rel_path.startswith("src/agents/") or rel_path == "src/loop1.py":
        return "agents"
    return "infra"


def classify(rel_path: str) -> dict:
    """Return {module, layer, kind, package, doc} for a source file."""
    stem = rel_path.rsplit("/", 1)[-1][:-3]
    if rel_path.startswith("tests/"):
        kind = "test"
    elif rel_path.startswith("scripts/"):
        kind = "script"
    elif rel_path.startswith("config/"):
        kind = "config"
    else:
        kind = "source"

    module = TEST_MODULE.get(stem, "infra") if kind == "test" else _source_module(rel_path)

    if kind == "test":
        layer = "tests"
    elif rel_path.startswith("src/data/"):
        layer = "data"
    elif rel_path.startswith("src/backtest/"):
        layer = "backtest"
    elif _is_execution(rel_path):
        layer = "execution"
    elif rel_path.startswith("src/agents/"):
        layer = "agents"
    elif rel_path in {"src/loop1.py", "src/main.py"}:
        layer = "orchestration"
    else:
        layer = "infra"

    package = rel_path.rsplit("/", 1)[0] if "/" in rel_path else "."
    doc = module if module in MODULE_DOCS else None
    return {"module": module, "layer": layer, "kind": kind, "package": package, "doc": doc}


# ── Discovery ──────────────────────────────────────────────────────────────────
def discover_py_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        root_path = REPO_ROOT / root
        if root_path.exists():
            files.extend(sorted(root_path.rglob("*.py")))
    return files


def dotted_name(rel_path: str) -> str:
    """src/data/schema.py -> src.data.schema ; src/agents/__init__.py -> src.agents"""
    mod = rel_path[:-3].replace("/", ".")  # strip .py
    if mod.endswith(".__init__"):
        mod = mod[: -len(".__init__")]
    return mod


def first_docstring_line(tree: ast.Module) -> str:
    doc = ast.get_docstring(tree)
    if not doc:
        return "_No module docstring._"
    for line in doc.splitlines():
        line = line.strip()
        if line:
            # Strip a leading "filename.py — " / "filename.py - " prefix if present.
            for sep in (" — ", " – ", " - "):
                if sep in line and line.split(sep, 1)[0].endswith(".py"):
                    line = line.split(sep, 1)[1]
                    break
            return line
    return "_No module docstring._"


def internal_imports(tree: ast.Module, known_modules: set[str]) -> set[str]:
    """Return the set of internal dotted module names this file imports."""
    found: set[str] = set()

    def consider(candidate: str) -> None:
        if not candidate.startswith(INTERNAL_PREFIXES):
            return
        # Resolve to the most specific known module (file or package) that matches.
        if candidate in known_modules:
            found.add(candidate)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                consider(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                continue  # no relative imports exist in this codebase
            consider(node.module)
            # `from src.agents import strategy_agent` -> src.agents.strategy_agent
            for alias in node.names:
                consider(f"{node.module}.{alias.name}")
    return found


# ── Note rendering ───────────────────────────────────────────────────────────
def render_note(rel_path: str, mod: str, blurb: str, imports: set[str], meta: dict) -> str:
    name = rel_path.rsplit("/", 1)[-1]
    lines = [
        "---",
        f"tags: [code-note, auto, module/{meta['module']}]",
        f"module: {meta['module']}",
        f"layer: {meta['layer']}",
        f"kind: {meta['kind']}",
        f"package: {meta['package']}",
        f"imports_count: {len(imports)}",
        f"source: {rel_path}",
        "---",
        "",
        BANNER,
        "",
        f"# `{name}`",
        "",
        f"> {blurb}",
        "",
        "## Source",
        f"- [[{rel_path}|{name}]]",
        "",
        "## Imports",
    ]
    if imports:
        lines += [f"- [[{m}]]" for m in sorted(imports)]
    else:
        lines.append("- _No internal imports._")
    lines += ["", "## Documented in"]
    if meta["doc"]:
        lines.append(f"- [[{meta['doc']}]]")
    lines.append("- [[_code]]")
    lines.append("")
    return "\n".join(lines)


def render_moc(entries: list[tuple[str, str, str]]) -> str:
    """entries: list of (rel_path, mod, blurb) sorted."""
    lines = [
        "---",
        "tags: [moc, code-moc, auto]",
        "---",
        "",
        "# Code Map",
        "",
        BANNER,
        "",
        "**Up**: [[dashboard]]",
        "**Across**: [[_architecture]] · [[_modules]] · [[_decisions]] · "
        "[[_standards]] · [[_tasks]] · [[_issues]] · [[_trials]]",
        "",
        "One node per source file. Edges mirror real `import` statements. "
        "Open **Graph View** to see how the modules wire together, or use the "
        "live table below (sortable / groupable).",
        "",
        "![[code-map.base]]",
        "",
    ]
    # Group by parent directory.
    groups: dict[str, list[tuple[str, str, str]]] = {}
    for rel, mod, blurb in entries:
        group = rel.rsplit("/", 1)[0] if "/" in rel else "(root)"
        groups.setdefault(group, []).append((rel, mod, blurb))
    for group in sorted(groups):
        lines.append(f"## `{group}/`")
        for rel, mod, blurb in sorted(groups[group]):
            lines.append(f"- [[{mod}]] — {blurb}")
        lines.append("")
    return "\n".join(lines)


def write_if_changed(path: Path, content: str) -> bool:
    """Write only when content differs. Returns True if the file changed."""
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


# ── Main ────────────────────────────────────────────────────────────────────
def main() -> None:
    CODE_DIR.mkdir(parents=True, exist_ok=True)

    py_files = discover_py_files()

    # Pass 1: parse everything, build the known-module set.
    parsed: dict[str, ast.Module] = {}
    rel_by_mod: dict[str, str] = {}
    for f in py_files:
        rel = f.relative_to(REPO_ROOT).as_posix()
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            print(f"  skip (parse error): {rel} — {exc}")
            continue
        mod = dotted_name(rel)
        parsed[rel] = tree
        rel_by_mod[mod] = rel
    known_modules = set(rel_by_mod)

    # Pass 2: decide which files get a note (skip empty package markers), so that
    # `## Imports` only ever links to notes that actually exist.
    specs: list[tuple[str, ast.Module, str, set[str]]] = []
    for rel, tree in parsed.items():
        mod = dotted_name(rel)
        imports = internal_imports(tree, known_modules) - {mod}
        name = rel.rsplit("/", 1)[-1]
        # Skip empty package markers: __init__.py with no docstring and no imports.
        if name == "__init__.py" and not ast.get_docstring(tree) and not imports:
            continue
        specs.append((rel, tree, mod, imports))
    emitted_mods = {mod for _, _, mod, _ in specs}

    # Pass 3: render the notes, linking only to modules that got their own note.
    expected: set[str] = {"_code.md"}
    moc_entries: list[tuple[str, str, str]] = []
    changed = 0
    for rel, tree, mod, imports in specs:
        imports = imports & emitted_mods
        blurb = first_docstring_line(tree)
        meta = classify(rel)
        note_path = CODE_DIR / f"{mod}.md"
        expected.add(note_path.name)
        if write_if_changed(note_path, render_note(rel, mod, blurb, imports, meta)):
            changed += 1
        moc_entries.append((rel, mod, blurb))

    # MOC.
    if write_if_changed(CODE_DIR / "_code.md", render_moc(sorted(moc_entries))):
        changed += 1

    # Prune stale notes (renamed/deleted sources, or now-skipped __init__ files).
    pruned = 0
    for existing in CODE_DIR.glob("*.md"):
        if existing.name not in expected:
            existing.unlink()
            pruned += 1

    print(
        f"sync_obsidian_graph: {len(moc_entries)} code notes "
        f"({changed} written/updated, {pruned} pruned) in {CODE_DIR.relative_to(REPO_ROOT)}"
    )


if __name__ == "__main__":
    main()
