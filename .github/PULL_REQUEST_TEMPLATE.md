## Summary

<!-- One sentence explaining what and why. -->

Fixes #<!-- issue number, or remove this line -->

---

## Type of change

- [ ] Bug fix
- [ ] New tool / resource / prompt
- [ ] Documentation update
- [ ] CI / tooling change
- [ ] Dependency bump

---

## Checklist

- [ ] `uv run pytest` passes with no failures or warnings
- [ ] `uv run ruff check src/ tests/` returns no errors
- [ ] `uv run ruff format --check src/ tests/` returns no diff
- [ ] New tools are registered in `server.py` with `@mcp.tool()`
- [ ] New tools are listed in the `README.md` tool table
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] No secrets, API keys, or personal file paths committed
