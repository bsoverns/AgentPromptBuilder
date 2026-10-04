"""
Prompt engine for the Personal Agent Prompt Builder.

There's no UI code in here, so a future harness that calls the Claude,
OpenAI or local-model APIs directly can import this module and build exactly
the same prompts as the desktop app.

Inputs:
    data      templates.json (settings, languages, roles, steps, templates...)
    template  one entry of data["templates"]
    project   a project profile dict (repo_path, github_repo, language, ...)
    values    the request's field values {field key: text}
    owners    {step id: agent name | MODE_ME | MODE_DONE | MODE_SKIP}
              Missing or stale entries fall back to default_owner().
    team      [{"name", "role", "model"}]; the first "lead" runs the show
    target_id key of data["targets"] (claude_code, agentic_cli, chat, local)
    caps      {capability key: bool}
"""

import re
from datetime import datetime

MODE_ME = "Me (manual)"
MODE_DONE = "Already done"
MODE_SKIP = "Skip"
SPECIAL_MODES = (MODE_ME, MODE_DONE, MODE_SKIP)

PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Computed placeholders that are allowed to render as empty text.
OPTIONAL_KEYS = {"issue_note", "closes_note", "framework_note", "test_plan_note", "reviewer_note"}

HANDOFF_KEY = "handoff_notes"
CMD_KEYS = ("build", "test", "lint", "run", "outdated")

# Project values shown under Context, in this order. Commands are shown as code.
PROJECT_CONTEXT = [
    ("Project", "project_name", False),
    ("Repo folder", "repo_path", False),
    ("GitHub repo", "github_repo", False),
    ("Base branch", "base_branch", False),
    ("Language", "language", False),
    ("Other languages", "other_languages", False),
    ("Framework", "framework", False),
    ("Rules file", "rules_file", False),
    ("Build", "build_cmd", True),
    ("Test", "test_cmd", True),
    ("Lint", "lint_cmd", True),
    ("Run", "run_cmd", True),
]

SOLO = [{"name": "Claude", "role": "lead"}]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def slug(text: str | None, maxlen: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return s[:maxlen].rstrip("-")


def issue_number(text: str) -> str:
    """The issue number in a URL or reference ('.../issues/12#issuecomment-345' -> '12')."""
    # Last resort is the LAST number, so digits in an owner or repo name aren't picked up.
    for pattern in (r"/issues/(\d+)", r"/pull/(\d+)", r"#(\d+)", r"(\d+)\D*$"):
        m = re.search(pattern, text or "")
        if m:
            return m.group(1)
    return ""


def render(text: str, values: dict, missing: set | None = None) -> str:
    """Fill {placeholders}. Unfilled ones become <name> and are added to missing."""

    def sub(m):
        key = m.group(1)
        if values.get(key) or key in OPTIONAL_KEYS:
            return values.get(key, "")
        if missing is not None:
            missing.add(key)
        return f"<{key}>"

    return PLACEHOLDER.sub(sub, text)


def clean_team(team: list[dict] | None) -> list[dict]:
    out = [
        {
            "name": (a.get("name") or "").strip(),
            "role": a.get("role") or "lead",
            "model": (a.get("model") or "").strip(),
        }
        for a in team or []
    ]
    return [a for a in out if a["name"]] or [dict(a, model="") for a in SOLO]


def lead_of(team: list[dict]) -> dict:
    return next((a for a in team if a["role"] == "lead"), team[0])


def validate(data: dict) -> list[str]:
    """Return a list of problems in templates.json (empty when it's usable)."""
    errors = []
    library = data.get("step_library", {})
    fields = data.get("fields", {})
    presets = data.get("team_presets", {})
    for t in data.get("templates", []):
        tid = t.get("id", "?")
        for key in t.get("fields", []):
            if key not in fields:
                errors.append(f"{tid}: unknown field '{key}'")
        for item in t.get("steps", []):
            ref = item if isinstance(item, str) else item.get("use")
            if ref and ref not in library:
                errors.append(f"{tid}: unknown library step '{ref}'")
            if not isinstance(item, str) and not ref and not item.get("id"):
                errors.append(f"{tid}: a step has no id")
        if t.get("team") and t["team"] not in presets:
            errors.append(f"{tid}: unknown team preset '{t['team']}'")
    return errors


# ---------------------------------------------------------------------------
# Steps and owners
# ---------------------------------------------------------------------------


def resolve_steps(data: dict, template: dict) -> list[dict]:
    """Expand library references ("orient" or {"use": "orient", ...overrides})."""
    library = data.get("step_library", {})
    steps = []
    for item in template.get("steps", []):
        if isinstance(item, str):
            step = dict(library[item], id=item)
        elif "use" in item:
            step = dict(library[item["use"]])
            step.update({k: v for k, v in item.items() if k != "use"})
            step.setdefault("id", item["use"])
        else:
            step = dict(item)
        steps.append(step)
    return steps


def default_owner(step: dict, team: list[dict] | None, caps: dict, target: dict) -> str:
    if step.get("default_mode"):
        return step["default_mode"]
    cap = step.get("cap")
    if cap and (not caps.get(cap, True) or not target.get("agentic", True)):
        return MODE_ME
    team = clean_team(team)
    role = step.get("role", "lead")
    match = next((a for a in team if a["role"] == role), None)
    return (match or lead_of(team))["name"]


def resolve_owners(
    steps: list[dict], owners: dict | None, team: list[dict] | None, caps: dict, target: dict
) -> dict[str, str]:
    team = clean_team(team)
    valid = {a["name"] for a in team} | set(SPECIAL_MODES)
    out = {}
    for s in steps:
        owner = (owners or {}).get(s["id"])
        out[s["id"]] = owner if owner in valid else default_owner(s, team, caps, target)
    return out


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def project_values(data: dict, project: dict | None) -> dict[str, str]:
    """Project profile values, with blank commands filled from the language defaults."""
    p = {k: ("" if v is None else str(v)).strip() for k, v in (project or {}).items() if k not in ("id",)}
    if project:
        for key, spec in data.get("project_fields", {}).items():
            if not p.get(key) and spec.get("default"):
                p[key] = spec["default"]
    lang = data.get("languages", {}).get(p.get("language", ""), {})
    for k in CMD_KEYS:
        key = f"{k}_cmd"
        if not p.get(key):
            p[key] = lang.get(k, "")
    p["project_name"] = p.pop("name", "")
    return p


def computed_values(
    data: dict,
    template: dict,
    project: dict | None,
    raw_values: dict | None,
    request_id: int | str | None = None,
    team: list[dict] | None = None,
) -> dict[str, str]:
    v = {k: str(val) for k, val in data.get("settings", {}).items()}
    v.update(project_values(data, project))
    v.update({k: (val or "").strip() for k, val in (raw_values or {}).items()})
    # Fields the caller didn't send get their default (or a choice's first option),
    # the same as a fresh form in the UI.
    for key in template.get("fields", []):
        spec = data.get("fields", {}).get(key, {})
        if key not in (raw_values or {}):
            v[key] = spec.get("default") or (spec.get("options") or [""])[0]
    v["request_id"] = str(request_id or "")
    v["today"] = datetime.now().strftime("%Y-%m-%d")
    v["commit_type"] = template.get("commit_type", "feat")

    n = issue_number(v.get("issue", ""))
    v["issue_ref"] = f"#{n}" if n else ""
    v["issue_note"] = f" Reference `#{n}` in the final commit message." if n else ""
    v["closes_note"] = f" Start the PR body with `Closes #{n}`." if n else ""
    v["framework_note"] = f" using {v['framework']}" if v.get("framework") else ""
    reviewer = v.get("github_reviewer", "")
    v["reviewer_note"] = (
        f" Post it as the reviewer account by prefixing the gh command with "
        f'`GH_TOKEN="$(gh auth token --user {reviewer})"` (never print the token).'
        if reviewer
        else ""
    )
    v["test_plan_note"] = (
        " Cover every scenario under 'Test scenarios (from me)'." if v.get("test_plan") else ""
    )

    if not v.get("branch"):
        title = slug(v.get("title", ""))
        if title:
            prefix = template.get("branch_prefix", "feature")
            v["branch"] = f"{prefix}/{request_id}-{title}" if request_id else f"{prefix}/{title}"

    repo, base, branch = v.get("github_repo"), v.get("base_branch"), v.get("branch")
    if repo and base and branch:
        v["compare_link"] = f"https://github.com/{repo}/compare/{base}...{branch}?expand=1"
    elif branch:
        v["compare_link"] = f"the GitHub compare page for `{branch}`"
    v["team_names"] = ", ".join(a["name"] for a in clean_team(team))
    return v


def _field_lines(label: str, value: str, code: bool = False) -> list[str]:
    if "\n" in value:
        return [f"- {label}:"] + [f"    {line}" for line in value.splitlines() if line.strip()]
    return [f"- {label}: `{value}`" if code else f"- {label}: {value}"]


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def build_prompt(
    data: dict,
    template: dict,
    project: dict | None,
    raw_values: dict | None,
    owners: dict | None,
    team: list[dict] | None,
    target_id: str,
    caps: dict,
    request_id: int | str | None = None,
    missing: set | None = None,
) -> str:
    """Build the prompt text. Unfilled placeholders are collected in missing (a set)."""
    if missing is None:
        missing = set()
    fields = data.get("fields", {})
    target = data.get("targets", {}).get(target_id, {})
    terse = target.get("terse", False)
    team = clean_team(team)
    multi = len(team) > 1
    lead = lead_of(team)
    v = computed_values(data, template, project, raw_values, request_id, team)
    out = []

    heading = template["name"]
    if v.get("title"):
        heading += f": {v['title']}"
    if request_id:
        heading += f" (request #{request_id})"
    out += [f"# {heading}", ""]

    if target.get("preamble"):
        out += ["## How to work", render(target["preamble"], v, missing), ""]

    # Context: project profile, then the request's short fields.
    out.append("## Context")
    template_fields = template.get("fields", [])
    if project:
        for label, key, code in PROJECT_CONTEXT:
            if v.get(key) and key not in template_fields:
                out += _field_lines(label, v[key], code)
        notes = data.get("languages", {}).get(v.get("language", ""), {}).get("notes")
        if notes and not terse:
            out.append(f"- {v['language']} conventions: {notes}")
    for key in template_fields:
        spec = fields.get(key, {})
        if key in ("title", HANDOFF_KEY) or spec.get("section") or not v.get(key):
            continue
        out += _field_lines(spec.get("label", key), v[key], code=(key == "branch"))
    out.append("")

    for key in template_fields:
        spec = fields.get(key, {})
        if spec.get("section") and v.get(key):
            out += [f"## {spec['section']}", v[key], ""]

    # Team
    if multi:
        roles = data.get("roles", {})
        out.append("## Team")
        if terse:
            out += [f"- {a['name']}: {a['role']}" for a in team]
        else:
            out += ["| Agent | Role | Model | Brief |", "|---|---|---|---|"]
            out += [
                f"| {a['name']} | {a['role']} | {a['model'] or 'default'} | {roles.get(a['role'], '')} |"
                for a in team
            ]
        out.append("")
        if target.get("subagents"):
            model_note = " (use the listed model where one is given)" if any(a["model"] for a in team) else ""
            out.append(
                f"You are **{lead['name']}**, the lead. Start each other agent as a subagent with the "
                f"Agent tool, under the name above{model_note}, with its brief and only the context "
                "its steps need. Nobody reviews or QAs their own work. Run independent reviews in "
                "parallel and don't show reviewers each other's findings until you consolidate them. "
                "You own the questions to me, the hand-offs and the final report."
            )
        else:
            other = next((a for a in team if a is not lead), lead)
            out.append(
                "You play every agent yourself, in step order. Start each step's output with the "
                f"agent's name in brackets, e.g. **[{other['name']} - {other['role']}]**, and stay in "
                "that role: a reviewer or QA agent checks the work as if seeing it for the first time "
                "and doesn't fix it."
            )
        out.append("")

    # Steps
    steps = [s for s in resolve_steps(data, template) if not s.get("when") or v.get(s["when"])]
    own = resolve_owners(steps, owners, team, caps, target)

    done = [s for s in steps if own[s["id"]] == MODE_DONE]
    if done or v.get(HANDOFF_KEY):
        out.append("## Already done")
        out += [f"- {s['title']}" for s in done]
        if v.get(HANDOFF_KEY):
            out.append(v[HANDOFF_KEY])
        out += ["Don't redo these. Pick up from the first step below.", ""]

    active = [s for s in steps if own[s["id"]] not in (MODE_DONE, MODE_SKIP)]
    if active:
        out.append("## Steps")
        manual = 0
        for n, s in enumerate(active, 1):
            owner = own[s["id"]]
            if owner == MODE_ME:
                manual += 1
                text = s.get("manual") or (
                    "I'll do this myself: "
                    + s["text"]
                    + " Stop before it, give me what I need, and wait for me to confirm."
                )
                out.append(f"{n}. **{s['title']}** [ME]: {render(text, v, missing)}")
            else:
                who = f" ({owner})" if multi else ""
                out.append(f"{n}. **{s['title']}**{who}: {render(s['text'], v, missing)}")
        if manual:
            out += [
                "",
                "Steps marked [ME] are mine. When you reach one, stop, give me what I need "
                "for it (commands, links, text), and wait until I tell you it's done.",
            ]
        out.append("")

    # Rules
    rules = list(data.get("global_rules", [])) + list(template.get("rules", []))
    for key in template_fields:
        extra = fields.get(key, {}).get("rules", {}).get(v.get(key, ""))
        if extra:
            rules.append(extra)
    rules = [render(r, v, missing) for r in rules]
    # The user's own project rules go in verbatim: braces in them aren't placeholders.
    rules += [
        line.strip().lstrip("-*").strip() for line in v.get("project_rules", "").splitlines() if line.strip()
    ]
    if rules:
        out.append("## Rules")
        out += [f"- {r}" for r in rules]
        out.append("")

    agent_col = "agent | " if multi else ""
    out += [
        "## When you stop",
        "Whether you're finished, blocked, or waiting on me, end with a table: "
        f"step | {agent_col}status (done / waiting on me / skipped / failed) | details and links.",
    ]
    return "\n".join(out)


def problems(
    data: dict,
    template: dict,
    project: dict | None,
    raw_values: dict | None,
    owners: dict | None,
    team: list[dict] | None,
    target_id: str,
    caps: dict,
    request_id: int | str | None = None,
) -> list[str]:
    """Things that would make the prompt send the agent off guessing."""
    missing = set()
    build_prompt(data, template, project, raw_values, owners, team, target_id, caps, request_id, missing)
    v = computed_values(data, template, project, raw_values, request_id, team)
    out = []
    if not project:
        out.append("No project selected")
    if not v.get("title"):
        out.append("No title")
    names = [(a.get("name") or "").strip() for a in team or []]
    if any(not n for n in names) or len(set(names)) != len(names):
        out.append("Agent names must be filled in and unique")

    target = data.get("targets", {}).get(target_id, {})
    steps = resolve_steps(data, template)
    own = resolve_owners(steps, owners, team, caps, target)
    git_active = any(
        own.get(sid) not in (MODE_DONE, MODE_SKIP, None) for sid in ("sync_base", "create_branch")
    )
    if git_active and re.search(r"\b(branch(ed)?|pulled)\b", v.get(HANDOFF_KEY, ""), re.I):
        out.append(
            "Hand-off notes mention the branch, but the git setup steps are still assigned "
            "(use 'I already branched')"
        )
    if missing:
        out.append("Unfilled: " + ", ".join(f"{{{k}}}" for k in sorted(missing)))
    return out
