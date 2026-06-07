You are helping me restructure my project documentation into a modular AI-development knowledge system.

Context:
- I have a project folder that already contains:
  - `.claude/PROJECT_CONTEXT.md`
  - `.claude/PLANNED_IMPROVEMENTS.md`
  - `CLAUDE.md`
  - source code in `src/`
  - tests in `tests/`
- I have already created this folder structure inside the project:
  - `claude_docs/architecture/`
  - `claude_docs/modules/`
  - `claude_docs/decisions/`
  - `claude_docs/tasks/`
  - `claude_docs/standards/`

Goal:
Restructure the existing project context into clean, modular markdown docs that are easier for humans and coding models to use. I do NOT want one giant context file. I want a layered documentation system.

Important rules:
1. Read these files first:
   - `.claude/PROJECT_CONTEXT.md`
   - `.claude/PLANNED_IMPROVEMENTS.md`
   - `CLAUDE.md`
2. Also inspect the codebase structure in:
   - `src/`
   - `tests/`
   - `config/`
   - `prompts/`
3. Create and populate documentation files inside `claude_docs/`.
4. Do not delete or overwrite the original `.claude` markdown files yet.
5. Do not change application code unless absolutely necessary.
6. Prefer creating new markdown files with clear headings and concise but useful detail.
7. If information is duplicated or messy in the source docs, consolidate it intelligently.
8. Convert brainstorming into structured final documentation where possible.
9. Keep documentation practical for coding agents: clear responsibilities, constraints, inputs/outputs, known issues, and next actions.

What I want created:
1. `claude_docs/architecture/overview.md`
   Include:
   - project purpose
   - core components
   - high-level data flow
   - architectural boundaries
   - current system state

2. One module file per major area you identify, likely including files such as:
   - `claude_docs/modules/data_pipeline.md`
   - `claude_docs/modules/backtesting.md`
   - `claude_docs/modules/agents.md`
   - `claude_docs/modules/monitoring.md`
   For each module include:
   - purpose
   - responsibilities
   - inputs
   - outputs
   - key files
   - dependencies
   - constraints
   - known issues
   - planned improvements

3. At least one decision log:
   - `claude_docs/decisions/2026-04-13-structured-context-system.md`
   Include:
   - decision
   - reason
   - alternatives considered
   - consequences

4. Initial task briefs inside `claude_docs/tasks/`
   Create a few practical task briefs based on the current system and planned improvements.
   For each task include:
   - goal
   - relevant context
   - requirements
   - files involved
   - done-when criteria

5. Optional standards docs if useful:
   - `claude_docs/standards/coding_rules.md`
   - `claude_docs/standards/testing_rules.md`
   - `claude_docs/standards/documentation_rules.md`

6. Update `CLAUDE.md` so it becomes a short routing document, not a giant context dump.
   It should:
   - briefly describe the project
   - list major modules
   - point to the relevant `claude_docs/` folders
   - instruct coding agents to read only the relevant task/module docs for the work at hand

Execution instructions:
- First, inspect the repo and infer the major system parts from the code.
- Then create the documentation files.
- Keep filenames simple and predictable.
- Use markdown only.
- Do not add filler.
- Do not delete old docs.
- After writing the files, provide a short summary of:
  - what files you created
  - how you mapped the old docs into the new structure
  - any ambiguities or gaps you found

Output style:
- Be structured and decisive.
- Make reasonable inferences from the codebase.
- Optimize for future use by Claude Code and by me in Obsidian.

Do not just summarize the old markdown files. Synthesize them into a clean operating knowledge base for an AI-assisted coding workflow. Prefer structured, implementation-useful documentation over diary-style notes.


## Related

- MOC: [[_tasks]]
- [[data_pipeline]]
