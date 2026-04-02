# Contributing to fastf1-mcp

Thank you for considering a contribution! This guide explains how to set up the project locally, follow coding conventions, and submit changes.

---

## Development Setup

**Requirements:** Python 3.10+, [uv](https://docs.astral.sh/uv/)

```bash
git clone https://github.com/Surya96t/fastf1-mcp.git
cd fastf1-mcp
uv sync --dev        # installs all deps including dev/lint groups
```

**Run tests:**

```bash
uv run pytest
```

**Lint and format:**

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

---

## Branching & Commits

Use short-lived branches off `main`:

| Prefix | Purpose |
|--------|---------|
| `feat/` | New tool, resource, or prompt |
| `fix/` | Bug fix |
| `chore/` | Deps, CI, tooling |
| `docs/` | Documentation only |

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(tools): add get_safety_car_periods tool
fix(telemetry): handle NaN values in distance sampling
docs: update README install instructions
chore(ci): bump astral-sh/setup-uv to v4
```

---

## Pull Request Checklist

Before opening a PR, ensure:

- [ ] `uv run pytest` passes with no failures or warnings
- [ ] `uv run ruff check src/ tests/` returns no errors
- [ ] `uv run ruff format --check src/ tests/` returns no diff
- [ ] New tools are registered in `server.py` and documented in `README.md`
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`
- [ ] No secrets, API keys, or personal paths are committed

---

## Project Layout

```
src/fastf1_mcp/
├── server.py           # MCP server — tool/resource/prompt registration
├── session_manager.py  # Async LRU cache for FastF1 sessions
├── config.py           # Pydantic settings (FASTF1_MCP_* env vars)
├── prompts.py          # MCP prompt templates
├── resources.py        # MCP resource handlers
├── tools/
│   ├── lookups.py      # Ergast API tools
│   ├── session.py      # Session data tools
│   ├── telemetry.py    # Telemetry tools
│   └── utility.py      # Cache + list tools
└── utils/
    ├── converters.py   # DataFrame → JSON helpers
    └── errors.py       # Structured error types
```

---

## Adding a New Tool

1. Implement the function in the appropriate `tools/*.py` module
2. Register it in `server.py` using `@mcp.tool()`
3. Add an entry to the tool table in `README.md`
4. Add at least one test in `tests/test_tools/`
5. Add a `CHANGELOG.md` entry under `[Unreleased]`

---

## Code of Conduct

Be respectful and constructive. Harassment of any kind will not be tolerated.
