# AGENTS.md

Rules for agents (and people) working in this repo.

## What this is

Agent Prompt Builder: a personal Windows desktop tool (tkinter) that builds structured prompts
for coding agents. `AgentPromptBuilder.py` is the GUI, `engine.py` builds prompts (no UI),
`store.py` is the SQLite store, `github_auth.py` handles GitHub tokens via Windows Credential
Manager, and `templates.json` holds the templates, steps, roles and rules.

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

## Commands

- Build: `python -m compileall -q .`
- Test: `python -m pytest`
- Lint: `ruff check . && ruff format --check .`

All three must pass before a commit.

## Conventions

- Type hints on public functions (the GUI file is exempt).
- Runtime is standard library only (Python 3.13+). Dev tools go in `requirements-dev.txt`.
- Keep `engine.py` free of UI code; tests cover it and `store.py` without a GUI.
- Preserve line endings: `.py`, `.md`, `.bat` are LF + UTF-8; `templates.json` is CRLF.
  The repo uses `core.autocrlf=false`.
- Never commit secrets, tokens, `prompts.db`, `config.json` or `Prompts/`.
