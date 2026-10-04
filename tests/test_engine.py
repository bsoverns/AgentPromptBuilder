import pytest

import engine
from engine import MODE_DONE, MODE_ME, MODE_SKIP

TEAM = [{"name": "Atlas", "role": "lead"}, {"name": "Forge", "role": "coder"}]


def build(data, project, values, owners=None, missing=None):
    return engine.build_prompt(
        data, data["templates"][0], project, values, owners or {}, TEAM, "claude_code", {}, None, missing
    )


# -- slug / render -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Add Dark Mode!", "add-dark-mode"),
        ("  --Hello__World--  ", "hello-world"),
        ("", ""),
        (None, ""),
    ],
)
def test_slug(text, expected):
    assert engine.slug(text) == expected


def test_slug_truncates_without_trailing_dash():
    assert engine.slug("aaaa bbbb", maxlen=5) == "aaaa"


def test_render_fills_and_reports_missing():
    missing = set()
    assert engine.render("{a} and {b}", {"a": "x"}, missing) == "x and <b>"
    assert missing == {"b"}


def test_render_optional_keys_render_empty():
    missing = set()
    assert engine.render("Done.{issue_note}", {}, missing) == "Done."
    assert missing == set()


def test_render_does_not_re_expand_values():
    assert engine.render("{a}", {"a": "{b}", "b": "nope"}) == "{b}"


# -- computed values -----------------------------------------------------------


@pytest.mark.parametrize(
    ("issue", "ref"),
    [
        ("https://github.com/me/demo/issues/12#issuecomment-345", "#12"),
        ("https://github.com/me/demo/issues/12", "#12"),
        ("https://github.com/me/web3/pull/34", "#34"),
        ("https://github.com/o/r2d2/discussions/9", "#9"),
        ("https://github.com/o/r2d2/issues/5#issuecomment-77", "#5"),
        ("#12", "#12"),
        ("me/demo#12", "#12"),
        ("12", "#12"),
        ("", ""),
    ],
)
def test_issue_parsing(data, project, issue, ref):
    v = engine.computed_values(data, data["templates"][0], project, {"issue": issue})
    assert v["issue_ref"] == ref
    assert bool(v["closes_note"]) == bool(ref)


def test_branch_from_title_without_request_id(data, project):
    v = engine.computed_values(data, data["templates"][0], project, {"title": "Dark mode"})
    assert v["branch"] == "feature/dark-mode"


def test_branch_from_title_with_request_id(data, project):
    v = engine.computed_values(data, data["templates"][0], project, {"title": "Dark mode"}, request_id=7)
    assert v["branch"] == "feature/7-dark-mode"
    assert v["compare_link"] == "https://github.com/me/demo/compare/main...feature/7-dark-mode?expand=1"


def test_explicit_branch_wins(data, project):
    values = {"title": "Dark mode", "branch": "fix/mine"}
    v = engine.computed_values(data, data["templates"][0], project, values, request_id=7)
    assert v["branch"] == "fix/mine"


def test_project_commands_fall_back_to_language_defaults(data, project):
    v = engine.computed_values(data, data["templates"][0], project, {})
    assert v["test_cmd"] == "pytest"
    assert v["base_branch"] == "main"


# -- steps and owners ----------------------------------------------------------


def test_resolve_steps_forms(data):
    template = {
        "steps": [
            "orient",
            {"use": "implement", "title": "Build it"},
            {"use": "review", "id": "review2"},
            {"id": "custom", "title": "Custom", "text": "Do it."},
        ]
    }
    steps = engine.resolve_steps(data, template)
    assert [s["id"] for s in steps] == ["orient", "implement", "review2", "custom"]
    assert steps[1]["title"] == "Build it"
    assert steps[1]["role"] == "coder"
    assert "use" not in steps[1]
    assert steps[3]["text"] == "Do it."
    # Overrides don't leak back into the library.
    assert data["step_library"]["implement"]["title"] == "Implement"


def test_default_owner_matches_role(data):
    step = data["step_library"]["implement"]
    assert engine.default_owner(step, TEAM, {}, {"agentic": True}) == "Forge"


def test_default_owner_missing_role_goes_to_lead(data):
    step = data["step_library"]["review"]
    assert engine.default_owner(step, TEAM, {}, {"agentic": True}) == "Atlas"


def test_default_owner_capability_off_is_manual(data):
    step = data["step_library"]["create_branch"]
    assert engine.default_owner(step, TEAM, {"git_local": False}, {"agentic": True}) == MODE_ME


def test_default_owner_non_agentic_target_is_manual(data):
    step = data["step_library"]["create_branch"]
    assert engine.default_owner(step, TEAM, {"git_local": True}, {"agentic": False}) == MODE_ME


def test_default_owner_respects_default_mode():
    assert engine.default_owner({"default_mode": MODE_SKIP}, TEAM, {}, {}) == MODE_SKIP


def test_resolve_owners_replaces_stale_names(data):
    steps = engine.resolve_steps(data, data["templates"][0])
    own = engine.resolve_owners(steps, {"orient": "Ghost", "implement": MODE_DONE}, TEAM, {}, {})
    assert own["orient"] == "Atlas"
    assert own["implement"] == MODE_DONE
    assert own["review"] == "Atlas"


# -- build_prompt / problems ---------------------------------------------------


def test_build_prompt_done_and_skip(data, project):
    out = build(data, project, {"title": "X"}, owners={"orient": MODE_DONE, "review": MODE_SKIP})
    done, rest = out.split("## Already done", 1)[1].split("## Steps", 1)
    steps = rest.split("## Rules", 1)[0]
    assert "- Get oriented" in done
    assert "Get oriented" not in steps
    assert "Review" not in steps
    assert "1. **Create branch** (Atlas)" in steps
    assert "2. **Implement** (Forge)" in steps


def test_build_prompt_manual_step(data, project):
    out = build(data, project, {"title": "X"}, owners={"implement": MODE_ME})
    assert "**Implement** [ME]" in out
    assert "Steps marked [ME] are mine" in out


def test_project_rules_are_verbatim(data, project):
    project["project_rules"] = "- Use {name} literally\n* Keep {braces}\n\n"
    missing = set()
    out = build(data, project, {"title": "X"}, missing=missing)
    rules = out.split("## Rules", 1)[1]
    assert "- Use {name} literally" in rules
    assert "- Keep {braces}" in rules
    assert "- Work only inside C:/src/demo." in rules
    assert "- Base is main." in rules
    assert not missing & {"name", "braces"}


def test_problems_basic_warnings(data):
    team = [{"name": "A"}, {"name": "A"}]
    issues = engine.problems(data, data["templates"][0], None, {}, {}, team, "claude_code", {})
    assert "No project selected" in issues
    assert "No title" in issues
    assert "Agent names must be filled in and unique" in issues
    assert any(i.startswith("Unfilled:") and "{repo_path}" in i for i in issues)


def test_problems_clean_request(data, project):
    issues = engine.problems(data, data["templates"][0], project, {"title": "X"}, {}, TEAM, "claude_code", {})
    assert issues == []


def test_problems_handoff_mentions_branch(data, project):
    values = {"title": "X", "handoff_notes": "I already branched"}
    issues = engine.problems(data, data["templates"][0], project, values, {}, TEAM, "claude_code", {})
    assert any("Hand-off notes mention the branch" in i for i in issues)


# -- real templates.json -------------------------------------------------------


def test_real_templates_validate(templates):
    assert engine.validate(templates) == []


def test_real_templates_all_build(templates, project):
    for template in templates["templates"]:
        for target in templates["targets"]:
            text = engine.build_prompt(
                templates, template, project, {"title": "Smoke"}, {}, None, target, {}, request_id=1
            )
            assert text.startswith(f"# {template['name']}: Smoke (request #1)")
