# Agent Prompt Builder (Personal)

A desktop tool that builds structured, repeatable prompts for my own projects: any language, a named agent team, GitHub included. Every request is saved, so I can recall it, update it, roll back to an earlier version, or copy it to another project.

```
python AgentPromptBuilder.py        (or double-click "Run AgentPromptBuilder.bat")
```

Only the Python standard library is needed (tkinter and sqlite3 ship with the Windows installer).

## Quick start
1. **Settings...**: enter your name. **GitHub...**: enter your GitHub username, and a token if you use one (see [Linking your personal GitHub](#linking-your-personal-github-one-time-about-5-minutes)).
2. **Project > New...**: pick the repo folder (for a new project, the folder to create), language, framework and base branch. Leave the build / test / lint commands blank to use the language defaults.
3. Pick a **Task type** and a **Target**, fill in the details, and check the **Agent team** and **Steps**.
4. **Copy to clipboard** and paste into Claude Code. Copying also saves the request.

## How it's organised

| Piece | What it does |
|---|---|
| **Project** | A saved profile: repo folder, GitHub `owner/name`, base branch, main + other languages, framework, commands, rules file (`CLAUDE.md`), and project rules that go into every prompt. |
| **Request** | One piece of work. The dropdown lists the project's requests (`#id title / task type / status`). **New request** starts a blank one, and the first **Save** or **Copy** creates the record. |
| **Status** | Draft, Ready, In progress, Waiting on me, In review, QA, Done or Abandoned. Changing it updates the record immediately. |
| **Task type** | What kind of work it is (see the table below). Switching task type keeps every value the new type also uses. |
| **Target** | Who runs the prompt. **Claude Code** gets subagent instructions; **Codex / agentic CLI** plays the roles in sequence; **Chat / API** and **Local model** can't run anything, so steps that need tools become *Me (manual)* and the agent gives you the commands. |
| **Capabilities** | What the agent can do right now. A step tied to a capability that's switched off defaults to **Me (manual)**. |
| **Agent team** | Named agents with a role (lead, coder, reviewer, QA, security, docs) and an optional model. Presets: Solo, Coder + Reviewer, Full team, Bug hunt, Review pair. With more than one agent, the prompt gets a Team table and each step names its agent. |
| **Steps** | Each step goes to an agent, **Me (manual)**, **Already done** or **Skip**. Steps default to the agent whose role matches the step. **I already branched** marks the git setup steps done. |
| **Warnings** | The red line lists anything that would make the agent guess: no project, no title, duplicate agent names, `{placeholders}` that are still empty, or hand-off notes that say you branched while the branch steps are still assigned. |

## Saving, recalling and reusing

| Button | What it does |
|---|---|
| **Save** (Ctrl+S) | Adds a new **version** to the current request: the whole form (task type, target, values, team, step owners) plus the exact prompt text. Nothing is overwritten. |
| **Save as new request** | Saves the form as a separate request in the same project. |
| **Copy to project...** | Starts a new request in another project from this one, with that project's repo, language and commands. Optionally clears issue, branch, PR, test scenarios and hand-off notes. Step owners go back to their defaults. |
| **History...** | Every version of the request. **Restore into form** loads one as unsaved changes; Save makes it the newest version. |
| **Library...** | Every request in every project. Search by title, project, task type, status or anything in the prompt text. Open, Copy to project or Delete. Done and Abandoned are hidden unless ticked. |
| **Export .md** | Writes the prompt to `Prompts\`. |

**Hand edits.** As soon as you type in the preview it's **locked**, and form changes stop overwriting it. Save stores the edited text and flags the version; reopening it asks whether to restore the edited text or rebuild from the form. Untick **Keep my edits** to rebuild.

All data is in `prompts.db` (SQLite) beside the script. Back that file up. `config.json` only remembers the last project, request, target, capabilities, window size and your settings.

## Task types (templates.json)

| Task type | Flow |
|---|---|
| New software - create a project | requirements check -> scaffold the folder, git, README, LICENSE, `CLAUDE.md` -> architecture -> build, test, review, QA -> `gh repo create` -> optional CI and `.claude/agents` files |
| Feature - add new | branch -> plan -> implement + tests -> review -> QA -> PR -> CI |
| Feature - change existing | find every usage first -> change -> update the tests that encoded the old behavior -> review, QA, PR |
| Feature - replace old with new (keep old alive) | map the old feature -> build the new one behind a switch (default: old) -> parity tests -> deprecate without removing -> document switching, rollback and removal |
| Bug - fix a known bug | failing test first -> root cause -> smallest fix -> regression test -> review, QA, PR |
| Bug hunt (multiple reviewers) | independent reviews -> consolidate (confirmed = two reviewers or reproduced) -> verify -> ranked report -> fixes only after you pick |
| QA - test plan and acceptance | make the criteria testable -> test plan table -> automate -> run -> verdict; QA doesn't fix code |
| Dependencies - update / replace obsolete packages | baseline -> inventory (outdated, deprecated, CVEs, replacements) -> your OK -> update in groups -> migrate code -> compare with the baseline -> PR |
| PR review - my code or another agent's | read, build and test the PR -> reviewers -> one merged review posted as COMMENT -> verdict |
| Refactor / cleanup | baseline tests -> small steps, green after each -> review confirms no behavior change |
| Spike / research | compare the options, optional throwaway prototype, recommendation and the next request to create |
| Release | version -> changelog -> bump -> PR -> tag + `gh release` after the merge |
| CI / build setup | GitHub Actions build + lint + test workflow, suggested branch protection |
| Pick up where I left off | check the current state -> do the next thing |

You can edit `templates.json` without touching the code:
- **Steps** come from `step_library` and are reused by name (`"review"`), or overridden in place (`{"use": "review", "text": "..."}`).
- **`fields`** with a `section` get their own heading in the prompt. Fields with `clear_on_copy` are cleared by Copy to project. `choice` fields can add a rule for the selected option.
- **`languages`** hold the default build / test / lint / outdated commands and conventions.
- **`roles`**, **`team_presets`**, **`targets`**, **`capabilities`** and **`global_rules`** are all plain JSON.
- **Placeholders**: any field or project key, plus `branch`, `request_id`, `compare_link`, `commit_type`, `issue_ref`, `team_names`, `today`, `project_name`, `build_cmd`, `test_cmd`, `lint_cmd`, `run_cmd` and `outdated_cmd`.

`engine.py` builds the prompt and has no UI, so a future API harness can import it and produce the same prompts. `store.py` is the only code that touches the database, so the local board system can replace it later by implementing the same methods.

## Linking your personal GitHub (one-time, about 5 minutes)

1. Install the GitHub CLI: `winget install --id GitHub.cli`. Then close and reopen the terminal.
2. Log in with `gh auth login`. Choose **GitHub.com -> HTTPS -> Yes (authenticate Git) -> Login with a web browser**, then enter the one-time code it shows.
3. Let git use the same login: `gh auth setup-git`.
4. Check it worked: `gh auth status` should show your account and scopes `repo`, `read:org`, `workflow`. If `workflow` is missing, run `gh auth refresh -s workflow`. Without it the agent can't push changes to `.github/workflows`.
5. Test it: `gh repo list --limit 5`.

### Using a token instead (the **GitHub...** button)
If you'd rather use a personal access token, or need one for a second account, open **GitHub...** in the app:
1. **Create a token on GitHub...** opens the token page. A fine-grained token needs **Contents**, **Pull requests**, **Workflows** and **Metadata** access to your repos (plus **Administration** if agents should create repos). A classic token needs `repo`, `workflow` and `read:org`.
2. Enter the username, paste the token and click **Save token**. It goes into **Windows Credential Manager** (encrypted under your Windows login, listed as `AgentPromptBuilder:github:<username>`). It's never written to `config.json`, `prompts.db` or a prompt. Saving also runs **Test**, which shows who the token belongs to, its scopes and its expiry.
3. Click **Log gh in with it** to hand the token to the GitHub CLI (gh keeps its own copy). For the main account this also sets git to use gh. **gh auth status** shows what gh is logged in as.

Agents never read the stored token. They use the gh login, and the reviewer account is used as `GH_TOKEN="$(gh auth token --user <reviewer>)" gh ...`, so the token never appears in a prompt or transcript. Don't put a token in a `GH_TOKEN` environment variable as well: gh would use it in place of the stored logins.

That covers what the templates use: `git push`, `gh repo create`, `gh pr create`, `gh pr review` / `gh api .../reviews`, `gh pr checks`, `gh run watch` and `gh release create`. Claude Code runs under your Windows user, so it picks up this login automatically.

### Agents approving PRs
GitHub doesn't let a PR's author approve their own PR or request changes on it. While everything runs under your account, reviewer agents post **comment** reviews, and *Approve / request changes* stays a **Me (manual)** step (the `gh: approve` capability is off). For real approvals later:
- **Second "bot" account** (GitHub allows one free machine account per person). Add it as a collaborator on your repos. In **GitHub...**, fill in **Reviewer account**, save its token, click **Log gh in with it**, then switch gh back to yourself with `gh auth switch --user <you>`. Tick **gh: approve**. Review steps then post and approve as that account through `GH_TOKEN="$(gh auth token --user <reviewer>)"`, which needs gh 2.40 or later.
- **GitHub App**: cleaner for a harness you build later, but more setup.

If you want branch protection to require a review, you need one of those two first. Otherwise you'd block your own merges.

## Ideas for later
- Connect the local board system (multi-tenant) as a second `Store` backend, or sync requests to it.
- An API harness that runs the team with `engine.build_prompt` and the Claude / OpenAI / local APIs directly.
- A "lessons learned" loop: when a session goes wrong, add a rule to `global_rules` or a step so every later prompt includes it.

## Development

Setup, build, test and lint commands and the project conventions are in [AGENTS.md](AGENTS.md).
In short: `pip install -r requirements-dev.txt`, then `python -m pytest` and `ruff check . && ruff format --check .`.

## License

MIT. See [LICENSE](LICENSE).
