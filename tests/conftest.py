import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def templates() -> dict:
    """templates.json, loaded the way the app loads it (UTF-8 JSON)."""
    with open(ROOT / "templates.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def data() -> dict:
    """A small, self-contained templates structure for focused engine tests."""
    return {
        "settings": {"author": "Me"},
        "capabilities": {"git_local": {"default": True}},
        "targets": {
            "claude_code": {"agentic": True, "subagents": True},
            "chat": {"agentic": False},
        },
        "roles": {"lead": "Runs it", "coder": "Writes it", "reviewer": "Checks it"},
        "languages": {"Python": {"build": "py -m compileall .", "test": "pytest", "lint": "ruff"}},
        "project_fields": {"base_branch": {"default": "main"}},
        "fields": {
            "title": {"label": "Title"},
            "issue": {"label": "Issue"},
            "branch": {"label": "Branch"},
            "handoff_notes": {"label": "Hand-off"},
        },
        "global_rules": ["Work only inside {repo_path}."],
        "step_library": {
            "orient": {"title": "Get oriented", "role": "lead", "text": "Read {repo_path}."},
            "create_branch": {
                "title": "Create branch",
                "role": "lead",
                "cap": "git_local",
                "text": "Create `{branch}`.",
            },
            "implement": {"title": "Implement", "role": "coder", "text": "Make the change."},
            "review": {"title": "Review", "role": "reviewer", "text": "Review it."},
        },
        "team_presets": {"Solo": [{"name": "Claude", "role": "lead"}]},
        "templates": [
            {
                "id": "feat",
                "name": "Feature",
                "branch_prefix": "feature",
                "fields": ["title", "issue", "branch", "handoff_notes"],
                "rules": ["Base is {base_branch}."],
                "steps": ["orient", "create_branch", "implement", "review"],
            }
        ],
    }


@pytest.fixture
def project() -> dict:
    return {
        "id": 1,
        "name": "Demo",
        "repo_path": "C:/src/demo",
        "github_repo": "me/demo",
        "language": "Python",
    }
