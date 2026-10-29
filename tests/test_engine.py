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


def test_dated_branch_style(data, project):
    template = dict(data["templates"][0], branch_style="dated")
    v = engine.computed_values(data, template, project, {"title": "add a wild-west theme"}, request_id=7)
    assert v["branch"] == f"{v['today']}_Add_A_Wild_West_Theme"
    v = engine.computed_values(data, template, project, {"title": "X", "branch": "mine"})
    assert v["branch"] == "mine"


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


OPUS_LEAD = [
    {"name": "Atlas", "role": "lead", "model": "opus"},
    {"name": "Runner", "role": "ops", "model": "sonnet"},
]
SONNET_LEAD = [
    {"name": "Atlas", "role": "lead", "model": "sonnet"},
    {"name": "Sage", "role": "architect", "model": "opus"},
]


def test_ops_takes_mechanical_steps_only():
    target = {"agentic": True}
    assert engine.default_owner({"role": "coder", "work": "mechanical"}, OPUS_LEAD, {}, target) == "Runner"
    assert engine.default_owner({"role": "lead", "work": "trivial"}, OPUS_LEAD, {}, target) == "Atlas"
    assert engine.default_owner({"role": "coder"}, OPUS_LEAD, {}, target) == "Atlas"


def test_architect_takes_judgment_steps_from_the_lead():
    target = {"agentic": True}
    assert engine.default_owner({"role": "lead"}, SONNET_LEAD, {}, target) == "Sage"
    assert engine.default_owner({"role": "coder"}, SONNET_LEAD, {}, target) == "Sage"
    assert engine.default_owner({"role": "coder", "work": "mechanical"}, SONNET_LEAD, {}, target) == "Atlas"
    assert engine.default_owner({"role": "lead", "work": "trivial"}, SONNET_LEAD, {}, target) == "Atlas"
    # A matching specialist still wins over the architect.
    team = SONNET_LEAD + [{"name": "Hawk", "role": "reviewer"}]
    assert engine.default_owner({"role": "reviewer"}, team, {}, target) == "Hawk"


def test_routing_ignored_without_router_roles(data):
    step = dict(data["step_library"]["implement"], work="mechanical")
    assert engine.default_owner(step, TEAM, {}, {"agentic": True}) == "Forge"


def test_delegation_section(data, project):
    data["delegation"] = {"ops": ["Only {ops} gets mechanical work."]}
    args = (data, data["templates"][0], project, {"title": "X"}, {}, OPUS_LEAD)
    out = engine.build_prompt(*args, "claude_code", {})
    assert "## Delegation (cost control)\n- Only Runner gets mechanical work." in out
    assert "Agent tool's `model`" in out
    assert "step | agent (model) | status" in out
    assert "## Delegation" not in engine.build_prompt(*args, "chat", {})


def test_problems_models_without_subagents(data, project):
    args = (data, data["templates"][0], project, {"title": "X"}, {}, OPUS_LEAD)
    assert any("models are ignored" in i for i in engine.problems(*args, "chat", {}))
    assert not any("models are ignored" in i for i in engine.problems(*args, "claude_code", {}))


def test_validate_rejects_unknown_work(data):
    data["step_library"]["orient"]["work"] = "easy"
    assert engine.validate(data) == ["step_library: 'orient' has unknown work 'easy'"]


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


def test_problems_reuses_missing_from_build(data):
    """The GUI builds once and hands the missing set to problems(); same result either way."""
    args = (data, data["templates"][0], None, {}, {}, TEAM, "claude_code", {})
    missing = set()
    engine.build_prompt(*args, None, missing)
    assert missing
    assert engine.problems(*args, missing=missing) == engine.problems(*args)


def test_problems_trusts_given_missing(data, project):
    # A supplied set is used as-is: the prompt isn't rebuilt to find it.
    issues = engine.problems(
        data, data["templates"][0], project, {"title": "X"}, {}, TEAM, "claude_code", {}, missing={"zzz"}
    )
    assert issues == ["Unfilled: {zzz}"]


def test_long_lines():
    text = "short\n" + "A" * 50 + "\nok\n" + "B" * 51
    assert engine.long_lines(text, 50) == [4]
    assert engine.long_lines(text, 10) == [2, 4]
    assert engine.long_lines("", 10) == []


def test_problems_handoff_mentions_branch(data, project):
    values = {"title": "X", "handoff_notes": "I already branched"}
    issues = engine.problems(data, data["templates"][0], project, values, {}, TEAM, "claude_code", {})
    assert any("Hand-off notes mention the branch" in i for i in issues)


# -- starting model -------------------------------------------------------------


def _with_start_models(data):
    data["start_models"] = {
        "Opus 5.5": {"lead": "opus", "preset": "Opus team"},
        "Sonnet 5.5": {"lead": "sonnet", "preset": "Sonnet team"},
    }
    data["team_presets"].update({"Opus team": OPUS_LEAD, "Sonnet team": SONNET_LEAD})
    data["fields"]["start_model"] = {
        "type": "choice",
        "options": ["Team decides", "Opus 5.5", "Sonnet 5.5"],
        "default": "Team decides",
    }
    data["templates"][0]["fields"].insert(1, "start_model")
    return data


def _start_problems(data, project, start_model, team, target="claude_code"):
    values = {"title": "X", "start_model": start_model}
    return engine.problems(data, data["templates"][0], project, values, {}, team, target, {})


def test_start_model_team_decides_adds_nothing(data, project):
    data = _with_start_models(data)
    out = build(data, project, {"title": "X"})
    assert "## Delegation" not in out
    assert "Starting model" not in out and "start_model" not in out
    assert _start_problems(data, project, "Team decides", TEAM) == []


def test_start_model_delegation_comes_from_the_team(data, project):
    data = _with_start_models(data)
    data["delegation"] = {"architect": ["{architect} thinks."]}
    values = {"title": "X", "start_model": "Sonnet 5.5"}
    out = engine.build_prompt(data, data["templates"][0], project, values, {}, SONNET_LEAD, "claude_code", {})
    assert "## Delegation (cost control)\n- Sage thinks." in out
    assert _start_problems(data, project, "Sonnet 5.5", SONNET_LEAD) == []


def test_start_model_warns_on_lead_mismatch(data, project):
    data = _with_start_models(data)
    issues = _start_problems(data, project, "Sonnet 5.5", OPUS_LEAD)
    assert any("Starting model is Sonnet 5.5" in i and "'Sonnet team'" in i for i in issues)
    assert any("lead's model is 'not set'" in i for i in _start_problems(data, project, "Opus 5.5", TEAM))
    assert not any("Starting model" in i for i in _start_problems(data, project, "Opus 5.5", TEAM, "chat"))


def test_validate_rejects_unknown_start_preset(data):
    data = _with_start_models(data)
    data["start_models"]["Opus 5.5"]["preset"] = "Ghost"
    assert engine.validate(data) == ["start_models: 'Opus 5.5' has unknown preset 'Ghost'"]


# -- project run mode, branch style and conventions -------------------------------


def _with_run_mode(data):
    data["global_rules"].append({"text": "Ask me first.", "unattended": "Never wait for me."})
    data["step_library"]["ask"] = {"title": "Ask", "role": "lead", "text": "Ask.", "unattended": "Decide."}
    data["step_library"]["merge"] = {
        "title": "Merge",
        "role": "lead",
        "when": "unattended",
        "text": "Merge it.",
    }
    data["step_library"]["wait"] = {"title": "Wait", "role": "lead", "unless": "unattended", "text": "Wait."}
    data["templates"][0]["steps"] += ["ask", "merge", "wait"]
    return data


def test_stop_for_me_keeps_the_usual_text(data, project):
    out = build(_with_run_mode(data), dict(project, run_mode="Stop for me"), {"title": "X"})
    assert "- Ask me first." in out and "**Ask** (Atlas): Ask." in out and "**Wait**" in out
    assert "Merge" not in out and "Never wait" not in out


def test_unattended_swaps_text_and_steps(data, project):
    out = build(_with_run_mode(data), dict(project, run_mode="Unattended (start to merge)"), {"title": "X"})
    assert "- Never wait for me." in out and "**Ask** (Atlas): Decide." in out
    assert "**Merge** (Atlas): Merge it." in out
    assert "**Wait**" not in out and "Ask me first" not in out


def test_unattended_warns_about_manual_steps(data, project):
    data = _with_run_mode(data)
    p = dict(project, run_mode="Unattended (start to merge)")
    issues = engine.problems(
        data, data["templates"][0], p, {"title": "X"}, {"merge": MODE_ME}, TEAM, "claude_code", {}
    )
    assert any("Unattended run" in i and "Merge" in i for i in issues)


def test_project_dated_branch_style(data, project):
    p = dict(project, branch_style="Dated (YYYY-MM-DD_Title)")
    v = engine.computed_values(data, data["templates"][0], p, {"title": "fix it"}, request_id=3)
    assert v["branch"].endswith("_Fix_It") and "/" not in v["branch"]


def test_project_conventions_replace_language_notes(data, project):
    data["languages"]["Python"]["notes"] = "Use a venv."
    assert "- Python conventions: Use a venv." in build(data, project, {"title": "X"})
    out = build(data, dict(project, conventions="Stdlib only."), {"title": "X"})
    assert "- Python conventions: Stdlib only." in out and "venv" not in out


def test_problems_empty_request(data, project):
    data["fields"]["guidance"] = {"label": "What needs to happen", "section": "What needs to happen"}
    data["templates"][0]["fields"].append("guidance")
    issues = engine.problems(data, data["templates"][0], project, {"title": "X"}, {}, TEAM, "claude_code", {})
    assert "Nothing says what to do (fill in 'What needs to happen')" in issues
    values = {"title": "X", "guidance": "Do it."}
    assert engine.problems(data, data["templates"][0], project, values, {}, TEAM, "claude_code", {}) == []


def test_validate_rejects_rule_without_text(data):
    data["global_rules"].append({"unattended": "No base text."})
    assert engine.validate(data) == ["global_rules: a rule must be text or a dict with 'text'"]


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
            assert "## Delegation" not in text
            assert "Small change" not in text


def test_real_templates_unattended_never_waits(templates, project):
    p = dict(project, run_mode="Unattended (start to merge)")
    for template in templates["templates"]:
        text = engine.build_prompt(templates, template, p, {"title": "Smoke"}, {}, None, "claude_code", {}, 1)
        assert "ask me everything in ONE message before writing code" not in text
        assert "merge a PR unless I say so" not in text
        if "merge" in template["steps"]:
            assert "**Merge**" in text and "gh pr merge" in text


# -- change size flags, whole-word branch names, Claude Office template ------------


def test_change_size_sets_small_and_large(data, project):
    for size, small, large in (("Small", "yes", ""), ("Normal", "", ""), ("Large", "", "yes")):
        v = engine.computed_values(data, data["templates"][0], project, {"title": "X", "change_size": size})
        assert (v["small"], v["large"]) == (small, large)


def test_dated_branch_cuts_at_a_whole_word():
    words = engine.title_words("Keep the work boxes out of the way of the action")
    assert words == "Keep_The_Work_Boxes_Out_Of_The_Way_Of"
    assert len(engine.title_words("x" * 60)) == 40


def _office(templates, project, size, run_mode):
    template = next(t for t in templates["templates"] if t["id"] == "claude_office_change")
    p = dict(project, run_mode=run_mode)
    team = templates["team_presets"][template["team"]]
    return engine.build_prompt(
        templates, template, p, {"title": "T", "change_size": size}, {}, team, "claude_code", {}, 1
    )


def test_office_template_scales_with_change_size(templates, project):
    small = _office(templates, project, "Small", "Stop for me")
    assert "Check just what changed" in small and "One round" in small and "Check every theme" not in small
    normal = _office(templates, project, "Normal", "Stop for me")
    assert "in the themes the change touches" in normal and "review once more" in normal
    assert "Check every theme" not in normal
    assert "**Check every theme**" in _office(templates, project, "Large", "Stop for me")


def test_office_template_unattended_skips_the_exe_and_merges(templates, project):
    attended = _office(templates, project, "Normal", "Stop for me")
    assert "**Rebuild and stop for my test**" in attended and "**Merge**" not in attended
    out = _office(templates, project, "Normal", "Unattended (start to merge)")
    assert "**Rebuild and stop for my test**" not in out and "After my OK" not in out
    assert "**Merge**" in out and "**Report**" in out and "**Ask questions**" in out
    assert "Wait for my OK" not in out and "I merge it" not in out
