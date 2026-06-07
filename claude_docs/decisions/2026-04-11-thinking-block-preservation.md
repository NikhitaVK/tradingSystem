# Decision: Preserve Claude extended-thinking blocks as signed objects across turns

**Date**: 2026-04-11

## Decision
`ClaudeClient.chat()` and all multi-turn callers serialise thinking blocks as full signed objects via `b.model_dump()`, store them as JSON in `reasoning_logs`, and reinject them verbatim on the next turn — never as plain text.

## Reason
Anthropic's extended thinking returns cryptographically signed content blocks. The signature is verified server-side on the next turn; if blocks are stringified or summarised, the API rejects the request. Pass-through is invisible to higher-level agent code and isolates this fragile contract in the client wrapper.

## Alternatives Considered
- **Summarise thinking before storing** — rejected: would invalidate the signature
- **Throw thinking away between turns** — rejected: loses interpretability for audit and degrades multi-turn reasoning quality


## Related

- MOC: [[agents]]
- [[2026-04-10-claude-max-tokens-raised-to-16000]]
