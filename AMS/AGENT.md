# Agent Handoff Protocol

Follow this protocol to maintain context across sessions.

In this project the protocol lives in `AMS/`, not the repo root. All paths
below are relative to the repo root.

## First-Time Setup

If this project doesn't already have a tool-specific instruction file, ask the user which AI tools they use and create the matching files in the repo root:

| Tool | Create file |
|------|------------|
| Claude Code | `CLAUDE.md` |
| OpenCode / Codex | `AGENTS.md` |
| Cursor | `.cursorrules` |
| GitHub Copilot | `.github/copilot-instructions.md` |
| Gemini CLI | `.gemini/styleguide.md` |

Each file should contain: `Read and follow AMS/AGENT.md in this project.`

## Directories

- `AMS/HANDOFF/` — Session journals, chronological
- `AMS/DOC/` — Reference docs, persistent knowledge by topic

Create these directories if they don't exist.

## Starting a Session

1. Read recent files in `AMS/HANDOFF/` (newest first)
2. Read relevant files in `AMS/DOC/`
3. Summarize current project state for the user
4. Ask what to work on next

## Ending a Session

Create a handoff document before the session ends.

**File:** `AMS/HANDOFF/handoff-[yyyy-mm-dd]-[task]-[author].md`

No spaces in filenames. Examples:
- `handoff-2026-01-20-auth-bug-claude.md`
- `handoff-2026-01-21-api-refactor-gemini.md`
- `handoff-2026-01-22-review-jane.md`

**Include:**
- What was attempted and the outcome
- What worked, what didn't
- Current state and blockers
- Open questions
- Files created or modified
- References to `AMS/DOC/` files and prior handoffs

## Updating DOC Files

When you learn something persistent about the project — architecture decisions, deploy steps, conventions, known issues — update or create a relevant file in `AMS/DOC/`. Prefer updating existing files over creating new ones.

When updating a DOC file, add or update a `Last updated: yyyy-mm-dd by [author]` line at the bottom.

## Version History

Source: https://github.com/cellear/agent-handoff

- **1.1** (2026-02-16) — Split into README + AGENT.md; added tool-specific setup; simplified DOC guidance
- **1.0** (2026-01-25) — Initial protocol: HANDOFF/ and DOC/ directories, session workflow, naming convention
