"""
Agent Prompt Builder (Personal)
---------------------------------------------------------------------------
A desktop UI for building structured prompts for my own projects, in any
language, for Claude Code (primarily), other agentic CLIs, plain chat / API
models and local models.

    Project   a saved profile: repo folder, GitHub repo, language, build /
              test / lint commands, project rules. Pick it once; every task
              type uses it.
    Request   one piece of work (a feature, a bug, a review...). Every Save
              stores a new version of the form and the exact prompt text in
              prompts.db, so any request can be recalled, updated, copied to
              another project, or rolled back to an earlier version.
    Team      named agents (lead, coder, reviewer, QA...). Every step is
              assigned to an agent, to me, already done, or skipped.

Templates, steps, roles, languages and rules live in templates.json and can
be edited without touching the code. The prompt logic is in engine.py (no UI)
so a future API harness can reuse it.

Requires only the standard library (tkinter + sqlite3 ship with Python).

    python AgentPromptBuilder.py
"""

import contextlib
import json
import os
import tempfile
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import engine
import github_auth
from engine import MODE_DONE, MODE_ME, MODE_SKIP, SPECIAL_MODES
from store import Store

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_FILE = APP_DIR / "templates.json"
CONFIG_FILE = APP_DIR / "config.json"
DB_FILE = APP_DIR / "prompts.db"
PROMPTS_DIR = APP_DIR / "Prompts"

STATUSES = ("Draft", "Ready", "In progress", "Waiting on me", "In review", "QA", "Done", "Abandoned")
CLOSED_STATUSES = ("Done", "Abandoned")
NEW_REQUEST_LABEL = "(new request - not saved yet)"
MUTED = "#666"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def load_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return fallback


def load_config(path):
    """config.json as a dict; a missing, corrupt or wrongly shaped file gives a fresh {}."""
    try:
        config = load_json(path, {})
    except ValueError:  # corrupt config.json: start fresh rather than refuse to open
        return {}
    if not isinstance(config, dict):
        return {}
    if not isinstance(config.get("settings", {}), dict):
        config["settings"] = {}
    return config


def save_json(path, obj):
    """Write atomically: a crash mid-write leaves the old file, not a truncated one."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def browse(var, kind, start_dir=None):
    start = var.get() or start_dir or str(APP_DIR)
    if kind == "dir":
        path = filedialog.askdirectory(initialdir=start if os.path.isdir(start) else start_dir)
    else:
        path = filedialog.askopenfilename(initialdir=os.path.dirname(start) or None)
    if path:
        var.set(os.path.normpath(path))


# ---------------------------------------------------------------------------
# Reusable widgets
# ---------------------------------------------------------------------------


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is self:
                self.canvas.yview_scroll(int(-event.delta / 120), "units")
                return
            if isinstance(widget, (tk.Text, ttk.Treeview)):
                return
            widget = widget.master


class FieldForm:
    """A label / widget grid built from field specs (entry, text, dir, file, choice)."""

    def __init__(self, parent, specs, initial, data, on_change=None, start_dir=None):
        self.widgets = {}
        parent.columnconfigure(1, weight=1)
        for row, (key, spec) in enumerate(specs):
            value = initial.get(key, spec.get("default", ""))
            value = "" if value is None else str(value)
            kind = spec.get("type", "entry")
            ttk.Label(parent, text=spec.get("label", key)).grid(
                row=row, column=0, sticky="nw", padx=(0, 6), pady=3
            )
            if kind == "text":
                w = tk.Text(
                    parent, height=spec.get("height", 3), wrap="word", font=("Segoe UI", 9), undo=True
                )
                w.insert("1.0", value)
                if on_change:
                    w.bind("<KeyRelease>", lambda e: on_change())
                w.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
            else:
                if kind == "choice":
                    options = spec.get("options") or list(data.get(spec.get("options_from", ""), {}))
                    var = tk.StringVar(value=value if value in options else (options[0] if options else ""))
                    ttk.Combobox(parent, textvariable=var, values=options, state="readonly").grid(
                        row=row, column=1, sticky="ew", pady=3
                    )
                else:
                    var = tk.StringVar(value=value)
                    ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=3)
                    if kind in ("dir", "file"):
                        ttk.Button(
                            parent, text="...", width=3, command=lambda v=var, k=kind: browse(v, k, start_dir)
                        ).grid(row=row, column=2, padx=(4, 0))
                if on_change:
                    var.trace_add("write", lambda *a: on_change())
                w = var
            if spec.get("hint"):
                ttk.Label(
                    parent,
                    text=spec["hint"],
                    foreground="#888",
                    font=("Segoe UI", 8),
                    wraplength=230,
                    justify="left",
                ).grid(row=row, column=3, sticky="nw", padx=6, pady=4)
            self.widgets[key] = w

    def get(self, key):
        w = self.widgets[key]
        return w.get("1.0", "end-1c") if isinstance(w, tk.Text) else w.get()

    def set(self, key, value):
        w = self.widgets[key]
        if isinstance(w, tk.Text):
            w.delete("1.0", "end")
            w.insert("1.0", value)
        else:
            w.set(value)

    def values(self):
        return {key: self.get(key) for key in self.widgets}


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


class Dialog(tk.Toplevel):
    def __init__(self, app, title, geometry):
        super().__init__(app)
        self.app = app
        self.title(title)
        self.geometry(geometry)
        self.transient(app)
        self.result = None
        self.body = ttk.Frame(self, padding=10)
        self.body.pack(fill="both", expand=True)

    def run(self):
        self.grab_set()
        self.wait_window()
        return self.result


class ProjectDialog(Dialog):
    """Create or edit a project profile. result = ("saved", id) or ("deleted", id)."""

    def __init__(self, app, project=None):
        super().__init__(app, "Edit project" if project else "New project", "820x600")
        self.project = project
        data = app.data
        specs = list(data["project_fields"].items())
        form_box = ttk.Frame(self.body)
        form_box.pack(fill="x")
        self.form = FieldForm(
            form_box,
            specs,
            dict(project or {}),
            data,
            on_change=self._changed,
            start_dir=app.settings.get("projects_root"),
        )
        self.lang_hint = ttk.Label(self.body, foreground=MUTED, wraplength=780, justify="left")
        self.lang_hint.pack(fill="x", pady=(10, 0))

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", side="bottom", pady=(10, 0))
        ttk.Button(bar, text="Save", command=self._save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=6)
        if project:
            ttk.Button(bar, text="Delete project...", command=self._delete).pack(side="left")
        self._last_path = self.form.get("repo_path")
        self._changed()

    def _changed(self):
        path = self.form.get("repo_path").strip()
        if path and path != self._last_path:
            base = os.path.basename(path.rstrip("\\/"))
            if not self.form.get("name").strip():
                self.form.set("name", base)
            user = self.app.settings.get("github_user", "").strip()
            if user and not self.form.get("github_repo").strip():
                self.form.set("github_repo", f"{user}/{base}")
        self._last_path = path
        lang = self.form.get("language")
        d = self.app.data["languages"].get(lang, {})
        parts = [f"{k} `{d[k]}`" for k in ("build", "test", "lint", "outdated") if d.get(k)]
        self.lang_hint.configure(
            text=(f"{lang} defaults: " + " | ".join(parts) + "\nLeave a command blank to use its default.")
            if parts
            else ""
        )

    def _save(self):
        values = self.form.values()
        name = values["name"].strip()
        if not name:
            messagebox.showwarning("Project", "Give the project a name.", parent=self)
            return
        other = self.app.store.find_project(name)
        if other and (not self.project or other["id"] != self.project["id"]):
            messagebox.showwarning("Project", f"There's already a project called '{name}'.", parent=self)
            return
        pid = self.app.store.save_project(values, self.project["id"] if self.project else None)
        self.result = ("saved", pid)
        self.destroy()

    def _delete(self):
        pid = self.project["id"]
        count = self.app.store.request_count(pid)
        if messagebox.askyesno(
            "Delete project",
            f"Delete '{self.project['name']}' and its {count} saved request(s)?\nThis can't be undone.",
            icon="warning",
            parent=self,
        ):
            self.app.store.delete_project(pid)
            self.result = ("deleted", pid)
            self.destroy()


class SettingsDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, "Settings", "640x220")
        specs = list(app.data.get("settings_fields", {}).items())
        box = ttk.Frame(self.body)
        box.pack(fill="x")
        self.form = FieldForm(box, specs, app.settings, app.data)
        bar = ttk.Frame(self.body)
        bar.pack(fill="x", side="bottom")
        ttk.Button(bar, text="Save", command=self._save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=6)

    def _save(self):
        self.result = self.form.values()
        self.destroy()


class GitHubDialog(Dialog):
    """GitHub usernames + tokens. Tokens live in Windows Credential Manager only."""

    ACCOUNTS = (
        (
            "github_user",
            "Main account (you)",
            "Pushes branches, opens PRs and posts comment reviews. Not needed if you've already run "
            "`gh auth login`; storing a token here is for a personal access token you want to keep, "
            "test or re-apply.",
        ),
        (
            "github_reviewer",
            "Reviewer account (optional)",
            "A second GitHub account that reviewer agents use to approve or request changes. GitHub "
            "doesn't allow either on your own PRs, so this has to be a different account with access "
            "to the repos. When it's set, review steps run gh as this account.",
        ),
    )
    NEW_TOKEN_URL = "https://github.com/settings/personal-access-tokens/new"

    def __init__(self, app):
        super().__init__(app, "GitHub accounts", "900x700")
        self.rows = {}
        # Created first (packed below the account boxes) because _show_state may call _say.
        out = ttk.LabelFrame(self.body, text="Result", padding=6)
        self.output = tk.Text(out, height=10, wrap="word", font=("Consolas", 9))
        self.output.pack(fill="both", expand=True)
        for key, title, note in self.ACCOUNTS:
            box = ttk.LabelFrame(self.body, text=title, padding=8)
            box.pack(fill="x", pady=4)
            box.columnconfigure(1, weight=1)
            ttk.Label(box, text=note, foreground=MUTED, wraplength=840, justify="left").grid(
                row=0, column=0, columnspan=3, sticky="w", pady=(0, 4)
            )
            ttk.Label(box, text="Username").grid(row=1, column=0, sticky="w", pady=3)
            user = tk.StringVar(value=app.settings.get(key, ""))
            ttk.Entry(box, textvariable=user, width=32).grid(row=1, column=1, sticky="w", pady=3)
            ttk.Label(box, text="Token").grid(row=2, column=0, sticky="w", pady=3)
            token = tk.StringVar()
            ttk.Entry(box, textvariable=token, show="•").grid(row=2, column=1, sticky="ew", pady=3)
            ttk.Label(
                box, text="Paste a new token here, then Save", foreground="#888", font=("Segoe UI", 8)
            ).grid(row=2, column=2, sticky="w", padx=6)
            state = ttk.Label(box)
            state.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 2))
            row = {"key": key, "title": title, "user": user, "token": token, "state": state}
            btns = ttk.Frame(box)
            btns.grid(row=4, column=0, columnspan=3, sticky="w", pady=(2, 0))
            ttk.Button(btns, text="Save token", command=lambda r=row: self._save(r)).pack(side="left")
            ttk.Button(btns, text="Test", command=lambda r=row: self._test(r)).pack(side="left", padx=4)
            ttk.Button(btns, text="Log gh in with it", command=lambda r=row: self._gh_login(r)).pack(
                side="left"
            )
            ttk.Button(btns, text="Remove token", command=lambda r=row: self._remove(r)).pack(
                side="left", padx=4
            )
            user.trace_add("write", lambda *a, r=row: self._show_state(r))
            self.rows[key] = row
            self._show_state(row)

        out.pack(fill="both", expand=True, pady=4)

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Button(bar, text="gh auth status", command=self._status).pack(side="left")
        ttk.Button(
            bar, text="Create a token on GitHub...", command=lambda: webbrowser.open(self.NEW_TOKEN_URL)
        ).pack(side="left", padx=6)
        ttk.Button(bar, text="Close", command=self._close).pack(side="right")
        ttk.Label(
            self.body,
            foreground=MUTED,
            wraplength=860,
            justify="left",
            text="Tokens are stored in Windows Credential Manager (encrypted under your Windows login) "
            "as 'AgentPromptBuilder:github:<username>'. They're never written to config.json, "
            "prompts.db or a prompt. Agents use the gh login, not these entries.",
        ).pack(fill="x", pady=(6, 0))
        self.protocol("WM_DELETE_WINDOW", self._close)

    # -- helpers --

    def _say(self, text):
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.update_idletasks()

    def _stored(self, row):
        try:
            return github_auth.load_token(row["user"].get())
        except OSError as exc:
            self._say(f"Couldn't read Credential Manager: {exc}")
            return None

    def _show_state(self, row):
        tok = self._stored(row) if row["user"].get().strip() else None
        if not row["user"].get().strip():
            row["state"].configure(text="No username yet.", foreground=MUTED)
        elif tok:
            row["state"].configure(
                text=f"Token stored in Credential Manager ({github_auth.masked(tok)}).", foreground="#1b7f3b"
            )
        else:
            row["state"].configure(text="No token stored for this username.", foreground=MUTED)

    def _token_for(self, row):
        """The token typed in the box, otherwise the stored one."""
        return row["token"].get().strip() or self._stored(row)

    def _remember_users(self):
        self.app.update_settings({key: r["user"].get().strip() for key, r in self.rows.items()})

    # -- actions --

    def _save(self, row):
        user, token = row["user"].get().strip(), row["token"].get().strip()
        if not user or not token:
            messagebox.showwarning("GitHub", "Enter the username and paste the token first.", parent=self)
            return
        try:
            github_auth.save_token(user, token)
        except OSError as exc:
            self._say(f"Couldn't save to Credential Manager: {exc}")
            return
        row["token"].set("")
        self._remember_users()
        self._show_state(row)
        self._say("Saved. Checking it with GitHub...")
        self._test(row, token)

    def _test(self, row, token=None):
        token = token or self._token_for(row)
        if not token:
            self._say("No token to test: paste one or save one first.")
            return
        self._say("Checking with GitHub...")
        ok, msg = github_auth.test_token(token)
        user = row["user"].get().strip()
        if ok and user and f"'{user.lower()}'" not in msg.lower():
            msg += f"\n\nWARNING: the token doesn't belong to '{user}'."
        self._say(msg)

    def _gh_login(self, row):
        token = self._token_for(row)
        if not token:
            self._say("No token: paste one or save one first.")
            return
        self._say("Logging the GitHub CLI in...")
        ok, out = github_auth.gh_login(token)
        lines = [out]
        if ok and row["key"] == "github_user":
            ok2, out2 = github_auth.gh_setup_git()
            lines.append("git is set to use gh for GitHub." if ok2 else f"gh auth setup-git failed: {out2}")
        if ok and row["key"] == "github_reviewer":
            lines.append(
                "gh is now switched to the reviewer account. Switch back with:\n"
                f"    gh auth switch --user {self.app.settings.get('github_user') or '<your username>'}\n"
                "Review steps use the reviewer through GH_TOKEN, so the active account can stay yours."
            )
        if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
            lines.append(
                "Note: GH_TOKEN / GITHUB_TOKEN is set in the environment, and gh uses it instead "
                "of the stored login."
            )
        self._say("\n\n".join(lines))
        self._remember_users()

    def _remove(self, row):
        user = row["user"].get().strip()
        if not user or not self._stored(row):
            self._say("Nothing stored for this username.")
            return
        if messagebox.askyesno(
            "Remove token",
            f"Remove the stored token for '{user}' from Credential "
            "Manager?\n(This doesn't log gh out or revoke it on GitHub.)",
            parent=self,
        ):
            try:
                github_auth.delete_token(user)
                self._say(
                    f"Removed. To log gh out too: gh auth logout --user {user}\n"
                    "To revoke the token: GitHub > Settings > Developer settings > Personal access tokens."
                )
            except OSError as exc:
                self._say(f"Couldn't remove it: {exc}")
            self._show_state(row)

    def _status(self):
        self._say("Running gh auth status...")
        self._say(github_auth.gh_status())

    def _close(self):
        self._remember_users()
        self.destroy()


class CopyToProjectDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, "Copy request to project", "520x150")
        names = [p["name"] for p in app.projects]
        current = app.project["name"] if app.project else ""
        others = [n for n in names if n != current]
        ttk.Label(self.body, text="Copy this request to:").grid(row=0, column=0, sticky="w")
        self.project = tk.StringVar(value=(others or names)[0])
        ttk.Combobox(self.body, textvariable=self.project, values=names, state="readonly", width=40).grid(
            row=0, column=1, sticky="w", padx=6
        )
        self.clear = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self.body,
            variable=self.clear,
            text="Clear request-specific fields (issue, branch, PR, test scenarios, hand-off notes)",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=8)
        bar = ttk.Frame(self.body)
        bar.grid(row=2, column=0, columnspan=2, sticky="e")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bar, text="Copy", command=self._ok).pack(side="right", padx=6)

    def _ok(self):
        self.result = (self.project.get(), self.clear.get())
        self.destroy()


class HistoryDialog(Dialog):
    """Every saved version of the current request. Restore loads one into the form."""

    def __init__(self, app, request):
        super().__init__(app, f"History: #{request['id']} {request['title']}", "1100x620")
        self.versions = app.store.list_versions(request["id"])
        paned = ttk.PanedWindow(self.body, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        cols = ("ver", "saved", "note", "edited")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for col, text, width in (
            ("ver", "Ver", 45),
            ("saved", "Saved", 140),
            ("note", "Note", 160),
            ("edited", "Hand edits", 80),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)
        for n, ver in reversed(list(enumerate(self.versions, 1))):
            self.tree.insert(
                "",
                "end",
                iid=str(n - 1),
                values=(f"v{n}", ver["created"], ver["note"], "yes" if ver["edited"] else ""),
            )

        right = ttk.Frame(paned)
        paned.add(right, weight=2)
        self.text = tk.Text(right, wrap="word", font=("Consolas", 9))
        self.text.pack(fill="both", expand=True)

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="Restore into form", command=self._restore).pack(side="left")
        ttk.Button(bar, text="Copy text", command=self._copy).pack(side="left", padx=6)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        ttk.Label(
            bar,
            foreground=MUTED,
            text="Restore loads that version as unsaved changes; Save makes it the newest version.",
        ).pack(side="left", padx=12)

        self.tree.bind("<<TreeviewSelect>>", self._show)
        self.tree.bind("<Double-1>", lambda e: self._restore())
        if self.versions:
            last = str(len(self.versions) - 1)
            self.tree.selection_set(last)
            self.tree.focus(last)

    def _selected(self):
        sel = self.tree.selection()
        return self.versions[int(sel[0])] if sel else None

    def _show(self, _event=None):
        ver = self._selected()
        self.text.delete("1.0", "end")
        if ver:
            self.text.insert("1.0", ver["prompt"])

    def _copy(self):
        ver = self._selected()
        if ver:
            self.clipboard_clear()
            self.clipboard_append(ver["prompt"])

    def _restore(self):
        ver = self._selected()
        if ver:
            self.result = ver
            self.destroy()


class LibraryDialog(Dialog):
    """All saved requests across projects. result = ("open" | "copy", request row)."""

    def __init__(self, app):
        super().__init__(app, "Saved requests", "1200x560")
        top = ttk.Frame(self.body)
        top.pack(fill="x")
        ttk.Label(top, text="Search (title, project, task type, status, prompt text):").pack(side="left")
        self.search = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.search, width=40)
        entry.pack(side="left", padx=6, fill="x", expand=True)
        ttk.Label(top, text="Project:").pack(side="left", padx=(8, 0))
        self.project = tk.StringVar(value="All")
        ttk.Combobox(
            top,
            textvariable=self.project,
            state="readonly",
            width=24,
            values=["All"] + [p["name"] for p in app.projects],
        ).pack(side="left", padx=4)
        self.show_closed = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Show Done / Abandoned", variable=self.show_closed).pack(
            side="left", padx=6
        )

        cols = ("id", "project", "title", "task", "status", "updated", "versions")
        self.tree = ttk.Treeview(self.body, columns=cols, show="headings", selectmode="browse")
        for col, text, width in (
            ("id", "#", 50),
            ("project", "Project", 150),
            ("title", "Title", 330),
            ("task", "Task type", 270),
            ("status", "Status", 100),
            ("updated", "Updated", 140),
            ("versions", "Versions", 70),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, pady=6)

        bar = ttk.Frame(self.body)
        bar.pack(fill="x")
        ttk.Button(bar, text="Open", command=lambda: self._finish("open")).pack(side="left")
        ttk.Button(bar, text="Copy to project...", command=lambda: self._finish("copy")).pack(
            side="left", padx=6
        )
        ttk.Button(bar, text="Delete", command=self._delete).pack(side="right")
        ttk.Label(
            bar,
            foreground=MUTED,
            text="Open loads the latest version. Copy to project starts a new request "
            "in another project from this one.",
        ).pack(side="left", padx=12)

        self.records = app.store.list_requests()
        self.by_iid = {}
        for var in (self.search, self.project, self.show_closed):
            var.trace_add("write", self._fill)
        self.tree.bind("<Double-1>", lambda e: self._finish("open"))
        entry.focus_set()
        self._fill()

    def _task_name(self, rec):
        t = self.app.templates.get(rec["template_id"])
        return t["name"] if t else f"(missing task type: {rec['template_id']})"

    def _fill(self, *_):
        self.tree.delete(*self.tree.get_children())
        self.by_iid.clear()
        terms = self.search.get().lower().split()
        project = self.project.get()
        for rec in self.records:
            if project != "All" and rec["project_name"] != project:
                continue
            if not self.show_closed.get() and rec["status"] in CLOSED_STATUSES:
                continue
            task = self._task_name(rec)
            hay = " ".join(
                [
                    str(rec["id"]),
                    rec["title"],
                    rec["project_name"] or "",
                    task,
                    rec["status"],
                    rec["latest_prompt"] or "",
                ]
            ).lower()
            if all(t in hay for t in terms):
                iid = self.tree.insert(
                    "",
                    "end",
                    values=(
                        rec["id"],
                        rec["project_name"] or "(none)",
                        rec["title"],
                        task,
                        rec["status"],
                        rec["updated"],
                        rec["version_count"],
                    ),
                )
                self.by_iid[iid] = rec

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Saved requests", "Select a request first.", parent=self)
            return None
        return self.by_iid[sel[0]]

    def _finish(self, action):
        rec = self._selected()
        if not rec:
            return
        if rec["template_id"] not in self.app.templates:
            messagebox.showerror(
                "Saved requests",
                f"Task type '{rec['template_id']}' isn't in templates.json any more. "
                "Add it back to open this request.",
                parent=self,
            )
            return
        self.result = (action, rec)
        self.destroy()

    def _delete(self):
        rec = self._selected()
        if rec and messagebox.askyesno(
            "Delete",
            f"Delete request #{rec['id']} '{rec['title']}' and all "
            f"{rec['version_count']} saved version(s)? This can't be undone.",
            icon="warning",
            parent=self,
        ):
            self.app.store.delete_request(rec["id"])
            self.records.remove(rec)
            self.app.on_request_deleted(rec["id"])
            self._fill()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Agent Prompt Builder (Personal)")
        self.minsize(1100, 650)

        try:
            self.data = load_json(TEMPLATES_FILE, None)
        except json.JSONDecodeError as exc:
            messagebox.showerror("templates.json", f"templates.json is not valid JSON:\n{exc}")
            raise SystemExit(1) from None
        if not self.data:
            messagebox.showerror("templates.json", f"Missing {TEMPLATES_FILE}")
            raise SystemExit(1)
        errors = engine.validate(self.data)
        if errors:
            messagebox.showerror("templates.json", "templates.json has problems:\n" + "\n".join(errors))
            raise SystemExit(1)

        self.config_data = load_config(CONFIG_FILE)
        self.geometry(self.config_data.get("geometry", "1500x900"))
        self.settings = {**self.data.get("settings", {}), **self.config_data.get("settings", {})}
        self.data["settings"] = self.settings  # engine reads settings from data
        self.store = Store(DB_FILE)
        self.templates = {t["id"]: t for t in self.data["templates"]}
        self.targets = self.data["targets"]
        saved_caps = self.config_data.get("capabilities", {})
        self.cap_vars = {
            key: tk.BooleanVar(value=saved_caps.get(key, spec.get("default", True)))
            for key, spec in self.data.get("capabilities", {}).items()
        }

        self.projects = []
        self.project = None  # current project dict
        self.request = None  # current request row (None = new, unsaved)
        self.current = None  # current template
        self.form = None
        self.steps_by_id = {}
        self.owner_vars = {}
        self.owner_combos = {}
        self.team_rows = []
        self.request_map = {}
        self._refresh_job = None
        self._team_job = None
        self._generated = ""
        self._saved_key = None
        self._loading = False

        self._build_layout()
        self.reload_projects()

        last_project = self.store.get_project(self.config_data.get("last_project") or 0)
        if not last_project and self.projects:
            last_project = self.projects[0]
        self.set_project(last_project)
        last_request = self.store.get_request(self.config_data.get("last_request") or 0)
        if (
            last_request
            and last_project
            and last_request["project_id"] == last_project["id"]
            and last_request["template_id"] in self.templates
        ):
            self.open_request(last_request["id"], confirm=False)
        else:
            tid = self.config_data.get("last_template")
            self.start_blank(tid if tid in self.templates else self.data["templates"][0]["id"])

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Control-s>", lambda e: self.save())

    # -- layout -------------------------------------------------------------

    def _build_layout(self):
        row1 = ttk.Frame(self, padding=(10, 8, 10, 2))
        row1.pack(fill="x")
        ttk.Label(row1, text="Project:").pack(side="left")
        self.project_combo = ttk.Combobox(row1, state="readonly", width=28)
        self.project_combo.pack(side="left", padx=(4, 2))
        self.project_combo.bind("<<ComboboxSelected>>", self.on_project_selected)
        ttk.Button(row1, text="New...", width=7, command=self.new_project).pack(side="left", padx=2)
        ttk.Button(row1, text="Edit...", width=7, command=self.edit_project).pack(side="left", padx=2)
        ttk.Separator(row1, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(row1, text="Request:").pack(side="left")
        self.request_combo = ttk.Combobox(row1, state="readonly", width=70)
        self.request_combo.pack(side="left", padx=4)
        self.request_combo.bind("<<ComboboxSelected>>", self.on_request_selected)
        ttk.Button(row1, text="New request", command=self.new_request).pack(side="left", padx=2)
        ttk.Button(row1, text="Library...", command=self.open_library).pack(side="left", padx=2)
        ttk.Button(row1, text="Settings...", command=self.open_settings).pack(side="right")
        ttk.Button(row1, text="GitHub...", command=self.open_github).pack(side="right", padx=4)

        row2 = ttk.Frame(self, padding=(10, 2))
        row2.pack(fill="x")
        ttk.Label(row2, text="Task type:").pack(side="left")
        self.template_combo = ttk.Combobox(
            row2, state="readonly", width=50, values=[t["name"] for t in self.data["templates"]]
        )
        self.template_combo.pack(side="left", padx=4)
        self.template_combo.bind("<<ComboboxSelected>>", self.on_template_selected)
        ttk.Label(row2, text="Target:").pack(side="left", padx=(12, 0))
        self.target_combo = ttk.Combobox(
            row2, state="readonly", width=40, values=[t["label"] for t in self.targets.values()]
        )
        self.target_combo.pack(side="left", padx=4)
        self.target_combo.bind("<<ComboboxSelected>>", self.on_target_selected)
        ttk.Label(row2, text="Status:").pack(side="left", padx=(12, 0))
        self.status_var = tk.StringVar(value="Draft")
        status_combo = ttk.Combobox(
            row2, textvariable=self.status_var, values=STATUSES, state="readonly", width=14
        )
        status_combo.pack(side="left", padx=4)
        status_combo.bind("<<ComboboxSelected>>", self.on_status_selected)

        self.desc_label = ttk.Label(self, foreground=MUTED, padding=(12, 2))
        self.desc_label.pack(fill="x")
        self.warning = ttk.Label(self, foreground="#b00020", padding=(12, 0), justify="left")
        self.warning.pack(fill="x")
        self.warning.bind("<Configure>", lambda e: self.warning.configure(wraplength=max(e.width - 24, 200)))

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=6)
        self.left = ScrollFrame(paned)
        paned.add(self.left, weight=1)

        right = ttk.Frame(paned)
        paned.add(right, weight=1)
        bar1 = ttk.Frame(right)
        bar1.pack(fill="x")
        ttk.Label(bar1, text="Prompt preview", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.lock_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bar1,
            text="Keep my edits (form changes won't overwrite the preview)",
            variable=self.lock_var,
            command=self.on_lock_toggled,
        ).pack(side="left", padx=12)
        bar2 = ttk.Frame(right)
        bar2.pack(fill="x", pady=4)
        ttk.Button(bar2, text="Copy to clipboard", command=self.copy_prompt).pack(side="left")
        ttk.Button(bar2, text="Save", command=self.save).pack(side="left", padx=4)
        ttk.Button(bar2, text="Save as new request", command=self.save_as_new).pack(side="left")
        ttk.Button(bar2, text="Copy to project...", command=self.copy_to_project).pack(side="left", padx=4)
        ttk.Button(bar2, text="History...", command=self.open_history).pack(side="left")
        ttk.Button(bar2, text="Export .md", command=self.export_md).pack(side="left", padx=4)
        text_box = ttk.Frame(right)
        text_box.pack(fill="both", expand=True)
        self.preview = tk.Text(text_box, wrap="word", font=("Consolas", 10), undo=True)
        sb = ttk.Scrollbar(text_box, command=self.preview.yview)
        self.preview.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.preview.pack(fill="both", expand=True)
        self.preview.bind("<KeyRelease>", self.on_preview_edited)

        self.status = ttk.Label(self, padding=(10, 2), foreground=MUTED)
        self.status.pack(fill="x")

    def _build_form(self, values, owners, team):
        for child in self.left.inner.winfo_children():
            child.destroy()
        inner = self.left.inner

        caps = ttk.LabelFrame(inner, text="Capabilities (what the agent can do right now)", padding=8)
        caps.pack(fill="x", padx=4, pady=4)
        for i, (key, spec) in enumerate(self.data.get("capabilities", {}).items()):
            ttk.Checkbutton(
                caps,
                text=spec["label"],
                variable=self.cap_vars[key],
                command=lambda k=key: self.on_capability_changed(k),
            ).grid(row=i // 2, column=i % 2, sticky="w", padx=4, pady=1)

        details = ttk.LabelFrame(inner, text="Details", padding=8)
        details.pack(fill="x", padx=4, pady=4)
        fields = self.data["fields"]
        specs = [(k, fields[k]) for k in self.current["fields"]]
        self.form = FieldForm(
            details,
            specs,
            values,
            self.data,
            on_change=self.schedule_refresh,
            start_dir=self.settings.get("projects_root"),
        )

        tf = ttk.LabelFrame(inner, text="Agent team", padding=8)
        tf.pack(fill="x", padx=4, pady=4)
        top = ttk.Frame(tf)
        top.pack(fill="x")
        ttk.Label(top, text="Preset:").pack(side="left")
        self.preset_var = tk.StringVar()
        preset = ttk.Combobox(
            top,
            textvariable=self.preset_var,
            state="readonly",
            width=20,
            values=list(self.data.get("team_presets", {})),
        )
        preset.pack(side="left", padx=4)
        preset.bind("<<ComboboxSelected>>", lambda e: self.apply_preset(self.preset_var.get()))
        ttk.Button(top, text="+ Add agent", command=lambda: self.add_agent()).pack(side="left", padx=6)
        ttk.Label(
            top, foreground=MUTED, text="Name | role | model (optional, e.g. opus, sonnet, haiku)"
        ).pack(side="left", padx=6)
        self.team_box = ttk.Frame(tf)
        self.team_box.pack(fill="x", pady=(6, 0))

        sf = ttk.LabelFrame(inner, text="Steps: who does what", padding=8)
        sf.pack(fill="x", padx=4, pady=4)
        tools = ttk.Frame(sf)
        tools.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ttk.Button(tools, text="Reset owners", command=self.reset_owners).pack(side="left")
        ttk.Button(tools, text="I already branched", command=self.mark_git_done).pack(side="left", padx=6)
        sf.columnconfigure(2, weight=1)

        # Team rows first so the step owner lists know the names.
        self.team_rows = []
        for a in team:
            self.add_agent(a, notify=False)

        steps = engine.resolve_steps(self.data, self.current)
        self.steps_by_id = {s["id"]: s for s in steps}
        self.owner_vars.clear()
        self.owner_combos.clear()
        names = [a["name"] for a in self.team()]
        own = engine.resolve_owners(steps, owners, self.team(), self.capabilities(), self.target())
        cap_labels = self.data.get("capabilities", {})
        for row, s in enumerate(steps, 1):
            ttk.Label(sf, text=f"{row}.").grid(row=row, column=0, sticky="w")
            var = tk.StringVar(value=own[s["id"]])
            var.trace_add("write", lambda *a: self.schedule_refresh())
            combo = ttk.Combobox(
                sf, textvariable=var, values=names + list(SPECIAL_MODES), state="readonly", width=15
            )
            combo.grid(row=row, column=1, padx=4, pady=1)
            text = f"{s['title']}   ({s.get('role', 'lead')})"
            if s.get("cap"):
                text += f"   [{cap_labels.get(s['cap'], {}).get('label', s['cap'])}]"
            if s.get("when"):
                label = self.data["fields"].get(s["when"], {}).get("label", s["when"])
                text += f"   only if '{label}' is filled"
            ttk.Label(sf, text=text).grid(row=row, column=2, sticky="w")
            self.owner_vars[s["id"]] = var
            self.owner_combos[s["id"]] = combo

    # -- team ---------------------------------------------------------------

    def add_agent(self, agent=None, notify=True):
        agent = agent or {"name": "", "role": "coder", "model": ""}
        row = ttk.Frame(self.team_box)
        row.pack(fill="x", pady=1)
        entry = {
            "frame": row,
            "name": tk.StringVar(value=agent.get("name", "")),
            "role": tk.StringVar(value=agent.get("role", "lead")),
            "model": tk.StringVar(value=agent.get("model", "")),
        }
        ttk.Entry(row, textvariable=entry["name"], width=16).pack(side="left")
        ttk.Combobox(
            row,
            textvariable=entry["role"],
            values=list(self.data.get("roles", {})),
            state="readonly",
            width=11,
        ).pack(side="left", padx=4)
        ttk.Entry(row, textvariable=entry["model"], width=14).pack(side="left")
        ttk.Button(row, text="Remove", width=8, command=lambda: self.remove_agent(entry)).pack(
            side="left", padx=4
        )
        brief = ttk.Label(row, foreground="#888", font=("Segoe UI", 8))
        brief.pack(side="left", padx=4)
        entry["brief"] = brief
        for key in ("name", "role", "model"):
            entry[key].trace_add("write", lambda *a: self.schedule_team_change())
        self.team_rows.append(entry)
        self._update_brief(entry)
        if notify:
            self.schedule_team_change()

    def _update_brief(self, entry):
        brief = self.data.get("roles", {}).get(entry["role"].get(), "")
        entry["brief"].configure(text=(brief[:70] + "...") if len(brief) > 70 else brief)

    def remove_agent(self, entry):
        if len(self.team_rows) <= 1:
            messagebox.showinfo("Agent team", "The team needs at least one agent.")
            return
        entry["frame"].destroy()
        self.team_rows.remove(entry)
        self.schedule_team_change()

    def apply_preset(self, name):
        preset = self.data.get("team_presets", {}).get(name)
        if not preset:
            return
        for entry in self.team_rows:
            entry["frame"].destroy()
        self.team_rows = []
        for a in preset:
            self.add_agent(dict(a), notify=False)
        self.reset_owners()
        self.schedule_team_change()

    def team(self):
        return [
            {"name": e["name"].get().strip(), "role": e["role"].get(), "model": e["model"].get().strip()}
            for e in self.team_rows
        ]

    def schedule_team_change(self):
        if self._team_job:
            self.after_cancel(self._team_job)
        self._team_job = self.after(200, self.on_team_changed)

    def on_team_changed(self):
        self._team_job = None
        for entry in self.team_rows:
            self._update_brief(entry)
        team = self.team()
        options = [a["name"] for a in team if a["name"]] + list(SPECIAL_MODES)
        own = engine.resolve_owners(
            list(self.steps_by_id.values()),
            {sid: v.get() for sid, v in self.owner_vars.items()},
            team,
            self.capabilities(),
            self.target(),
        )
        for sid, combo in self.owner_combos.items():
            combo["values"] = options
            if self.owner_vars[sid].get() != own[sid]:
                self.owner_vars[sid].set(own[sid])
        self.schedule_refresh()

    # -- loading ------------------------------------------------------------

    def default_team(self, template):
        preset = self.data.get("team_presets", {}).get(template.get("team", ""), engine.SOLO)
        return [dict(a) for a in preset]

    def load_form(self, template_id, values, owners, team, target_id):
        self._loading = True
        self.current = self.templates[template_id]
        self.template_combo.set(self.current["name"])
        if target_id not in self.targets:
            target_id = next(iter(self.targets))
        self.target_combo.set(self.targets[target_id]["label"])
        self.desc_label.configure(text=self.current.get("description", ""))
        self.lock_var.set(False)
        self._build_form(values, owners, team)
        self.left.canvas.yview_moveto(0)
        self._loading = False
        self.refresh()

    def start_blank(self, template_id, values=None, team=None):
        """A new, unsaved request. It becomes a record on the first Save or Copy."""
        self.request = None
        template = self.templates[template_id]
        self.status_var.set("Draft")
        self.load_form(
            template_id,
            values or {},
            {},
            team or self.default_team(template),
            self.config_data.get("target", "claude_code"),
        )
        self._saved_key = self._dirty_key()
        self.reload_requests()

    def open_request(self, rid, confirm=True):
        if confirm and not self.confirm_leave():
            return False
        req = self.store.get_request(rid)
        if not req:
            return False
        ver = self.store.latest_version(rid)
        state = (
            ver["state"] if ver else {"template_id": req["template_id"], "values": {"title": req["title"]}}
        )
        if state["template_id"] not in self.templates:
            messagebox.showerror(
                "Open request", f"Task type '{state['template_id']}' isn't in templates.json any more."
            )
            return False
        if not self.project or self.project["id"] != req["project_id"]:
            self.set_project(self.store.get_project(req["project_id"]))
        self.request = req
        self.status_var.set(req["status"])
        self.apply_state(state, ver)
        self._saved_key = self._dirty_key()
        self.reload_requests()
        self.refresh()
        return True

    def apply_state(self, state, ver=None):
        template = self.templates[state["template_id"]]
        self.load_form(
            state["template_id"],
            state.get("values", {}),
            state.get("owners", {}),
            state.get("team") or self.default_team(template),
            state.get("target", self.config_data.get("target", "claude_code")),
        )
        if (
            ver
            and ver.get("edited")
            and messagebox.askyesno(
                "Hand-edited prompt",
                "This version was saved with hand edits in the preview.\n\n"
                "Yes: restore the edited text (the preview stays locked).\n"
                "No: rebuild the prompt from the form.",
            )
        ):
            self._set_preview(ver["prompt"])
            self.lock_var.set(True)

    # -- state --------------------------------------------------------------

    def form_state(self):
        return {
            "template_id": self.current["id"],
            "target": self.target_id(),
            "values": self.form.values(),
            "owners": self.owners(),
            "team": self.team(),
        }

    def _dirty_key(self):
        st = self.form_state()
        if self.lock_var.get():
            st["preview"] = self.prompt_text()
        return json.dumps(st, sort_keys=True)

    def is_dirty(self):
        if self.current is None or self.form is None:
            return False
        if self.request is None:
            return bool(self.form.values().get("title", "").strip())
        return self._dirty_key() != self._saved_key

    def confirm_leave(self):
        """Ask to save unsaved changes. False means the user canceled."""
        if not self.is_dirty():
            return True
        label = f"#{self.request['id']} {self.request['title']}" if self.request else "this new request"
        answer = messagebox.askyesnocancel("Unsaved changes", f"Save changes to {label} first?")
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    def capabilities(self):
        return {k: v.get() for k, v in self.cap_vars.items()}

    def owners(self):
        return {k: v.get() for k, v in self.owner_vars.items()}

    def target_id(self):
        label = self.target_combo.get()
        return next((k for k, t in self.targets.items() if t["label"] == label), next(iter(self.targets)))

    def target(self):
        return self.targets[self.target_id()]

    def build_args(self):
        return (
            self.data,
            self.current,
            self.project,
            self.form.values(),
            self.owners(),
            self.team(),
            self.target_id(),
            self.capabilities(),
            self.request["id"] if self.request else None,
        )

    # -- projects -----------------------------------------------------------

    def reload_projects(self):
        self.projects = self.store.list_projects()
        self.project_combo["values"] = [p["name"] for p in self.projects]

    def set_project(self, project):
        self.project = project
        self.project_combo.set(project["name"] if project else "")
        self.reload_requests()

    def switch_project(self, project):
        """Saved request open: start a new one in the other project.
        Unsaved draft: keep it and point it at the other project."""
        if self.request is not None:
            if not self.confirm_leave():
                self.project_combo.set(self.project["name"] if self.project else "")
                return
            self.set_project(project)
            self.start_blank(self.current["id"])
        else:
            self.set_project(project)
            self.refresh()

    def on_project_selected(self, _event=None):
        project = next((p for p in self.projects if p["name"] == self.project_combo.get()), None)
        if project and (not self.project or project["id"] != self.project["id"]):
            self.switch_project(project)

    def new_project(self):
        result = ProjectDialog(self).run()
        if result:
            self.reload_projects()
            self.switch_project(self.store.get_project(result[1]))

    def edit_project(self):
        if not self.project:
            self.new_project()
            return
        result = ProjectDialog(self, self.project).run()
        if not result:
            return
        self.reload_projects()
        if result[0] == "saved":
            self.project = self.store.get_project(result[1])
            self.project_combo.set(self.project["name"])
            self.refresh()
        else:
            self.request = None
            self.set_project(self.projects[0] if self.projects else None)
            self.start_blank(self.current["id"])

    # -- requests -----------------------------------------------------------

    def reload_requests(self):
        self.request_map = {}
        labels = []
        if self.project:
            for r in self.store.list_requests(self.project["id"]):
                t = self.templates.get(r["template_id"], {"name": r["template_id"]})
                label = f"#{r['id']}  {r['title']}   |  {t['name']}  |  {r['status']}"
                self.request_map[label] = r["id"]
                labels.append(label)
        self.request_combo["values"] = labels
        current = next(
            (label for label, rid in self.request_map.items() if self.request and rid == self.request["id"]),
            None,
        )
        self.request_combo.set(current or NEW_REQUEST_LABEL)

    def on_request_selected(self, _event=None):
        rid = self.request_map.get(self.request_combo.get())
        if rid and (not self.request or rid != self.request["id"]) and not self.open_request(rid):
            self.reload_requests()

    def new_request(self):
        if not self.project:
            if messagebox.askyesno("New request", "Requests belong to a project. Create a project now?"):
                self.new_project()
            if not self.project:
                return
        if not self.confirm_leave():
            return
        self.start_blank(self.current["id"])
        self.status.configure(
            text="New request. Fill in the title and details; Save or Copy creates the record."
        )

    def on_request_deleted(self, rid):
        if self.request and self.request["id"] == rid:
            self.request = None
            self._saved_key = None
        self.reload_requests()

    def on_status_selected(self, _event=None):
        if self.request:
            self.store.update_request(self.request["id"], status=self.status_var.get())
            self.request = self.store.get_request(self.request["id"])
            self.reload_requests()

    # -- events -------------------------------------------------------------

    def on_template_selected(self, _event=None):
        tid = next(t["id"] for t in self.data["templates"] if t["name"] == self.template_combo.get())
        if tid == self.current["id"]:
            return
        if self.lock_var.get() and not messagebox.askyesno(
            "Change task type",
            "Changing the task type rebuilds the prompt and drops your preview edits. Continue?",
        ):
            self.template_combo.set(self.current["name"])
            return
        # Carry over every value the new task type also has; owners go back to defaults.
        self.load_form(tid, self.form.values(), {}, self.team(), self.target_id())

    def on_target_selected(self, _event=None):
        self.reset_owners(only_caps=True)
        self.schedule_refresh()

    def on_capability_changed(self, cap):
        caps, team, target = self.capabilities(), self.team(), self.target()
        for sid, s in self.steps_by_id.items():
            var = self.owner_vars[sid]
            if s.get("cap") == cap and var.get() not in (MODE_DONE, MODE_SKIP):
                var.set(engine.default_owner(s, team, caps, target))
        self.schedule_refresh()

    def reset_owners(self, only_caps=False):
        caps, team, target = self.capabilities(), self.team(), self.target()
        for sid, s in self.steps_by_id.items():
            if only_caps and (not s.get("cap") or self.owner_vars[sid].get() in (MODE_DONE, MODE_SKIP)):
                continue
            self.owner_vars[sid].set(engine.default_owner(s, team, caps, target))

    def mark_git_done(self):
        found = [sid for sid in ("sync_base", "create_branch") if sid in self.owner_vars]
        for sid in found:
            self.owner_vars[sid].set(MODE_DONE)
        self.status.configure(
            text="Marked 'Update base branch' and 'Create branch' as Already done."
            if found
            else "This task type has no git setup steps."
        )

    def on_preview_edited(self, _event=None):
        if not self.lock_var.get() and self.prompt_text() != self._generated:
            self.lock_var.set(True)
            self.status.configure(
                text="Preview edited, so it's locked: form changes won't overwrite it. "
                "Untick 'Keep my edits' to rebuild it from the form."
            )
        self.refresh_status()

    def on_lock_toggled(self):
        if (
            not self.lock_var.get()
            and self.prompt_text() != self._generated
            and not messagebox.askyesno(
                "Rebuild prompt", "Discard your preview edits and rebuild the prompt from the form?"
            )
        ):
            self.lock_var.set(True)
            return
        self.refresh()

    # -- preview ------------------------------------------------------------

    def schedule_refresh(self):
        if self._loading:
            return
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(250, self.refresh)

    def _set_preview(self, text):
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.edit_reset()

    def refresh(self):
        self._refresh_job = None
        if self.current is None or self.form is None:
            return
        prompt = engine.build_prompt(*self.build_args())
        self._generated = prompt
        if not self.lock_var.get():
            self._set_preview(prompt)
        issues = engine.problems(*self.build_args())
        self.warning.configure(text=("Check before copying:  " + "   |   ".join(issues)) if issues else "")
        self.refresh_status()

    def refresh_status(self):
        if self.current is None or self.form is None:
            return
        owners = self.owners()
        manual = sum(1 for m in owners.values() if m == MODE_ME)
        req = f"request #{self.request['id']}" if self.request else "new request (not saved)"
        dirty = self.is_dirty()
        parts = [
            self.project["name"] if self.project else "no project",
            req,
            f"{len(self.team())} agent(s)",
            f"manual steps: {manual}",
            f"{len(self.prompt_text()):,} characters",
        ]
        if self.lock_var.get():
            parts.append("preview locked")
        if dirty:
            parts.append("UNSAVED CHANGES")
        self.status.configure(text="    ".join(parts))
        self.title("Agent Prompt Builder (Personal)" + (" *" if dirty else ""))

    def prompt_text(self):
        return self.preview.get("1.0", "end-1c")

    # -- output -------------------------------------------------------------

    def save(self, note="", quiet=False):
        if not self.project:
            messagebox.showwarning("Save", "Pick or create a project first (Project > New...).")
            return False
        title = self.form.values().get("title", "").strip()
        if not title:
            messagebox.showwarning("Save", "Give the request a title first.")
            return False
        if self.request is None:
            rid = self.store.create_request(
                self.project["id"], title, self.current["id"], self.status_var.get() or "Draft"
            )
            self.request = self.store.get_request(rid)
            self.refresh()  # the request id is now part of the branch name and heading
        else:
            self.store.update_request(
                self.request["id"], title=title, template_id=self.current["id"], status=self.status_var.get()
            )
        prompt = self.prompt_text()
        edited = self.lock_var.get() and prompt != self._generated
        self.store.add_version(self.request["id"], self.form_state(), prompt, edited, note)
        self.request = self.store.get_request(self.request["id"])
        self._saved_key = self._dirty_key()
        self.reload_requests()
        count = len(self.store.list_versions(self.request["id"]))
        if not quiet:
            self.status.configure(text=f"Saved request #{self.request['id']} (version {count}).")
        self.remember()
        self.refresh_status()
        return True

    def save_as_new(self):
        previous = self.request
        old = previous["id"] if previous else None
        self.request = None
        if self.save(note=f"copied from #{old}" if old else ""):
            self.status.configure(text=f"Saved as new request #{self.request['id']}.")
        else:
            self.request = previous

    def copy_to_project(self, source=None):
        """Start a new request in another project from the current one (or a library record)."""
        if not self.projects:
            messagebox.showinfo("Copy to project", "Create a project first.")
            return
        if source is None and not self.form.values().get("title", "").strip():
            messagebox.showinfo("Copy to project", "Give the request a title first.")
            return
        result = CopyToProjectDialog(self).run()
        if not result:
            return
        name, clear = result
        if source is None:
            state = self.form_state()
            old = self.request["id"] if self.request else None
        else:
            ver = self.store.latest_version(source["id"])
            state = (
                ver["state"]
                if ver
                else {"template_id": source["template_id"], "values": {"title": source["title"]}}
            )
            old = source["id"]
        if not self.confirm_leave():
            return
        values = dict(state.get("values", {}))
        if clear:
            for key in values:
                if self.data["fields"].get(key, {}).get("clear_on_copy"):
                    values[key] = ""
        template = self.templates[state["template_id"]]
        self.set_project(next(p for p in self.projects if p["name"] == name))
        self.request = None
        self.status_var.set("Draft")
        self.load_form(
            template["id"],
            values,
            {},
            state.get("team") or self.default_team(template),
            state.get("target", self.target_id()),
        )
        if self.save(note=f"copied from #{old}" if old else "copied", quiet=True):
            self.status.configure(
                text=f"Copied to {name} as request #{self.request['id']}. "
                "Steps are back to their default owners."
            )

    def open_history(self):
        if not self.request:
            messagebox.showinfo("History", "Save the request first; each Save adds a version.")
            return
        ver = HistoryDialog(self, self.request).run()
        if ver:
            if ver["state"]["template_id"] not in self.templates:
                messagebox.showerror(
                    "History", f"Task type '{ver['state']['template_id']}' isn't in templates.json any more."
                )
                return
            self.apply_state(ver["state"], ver)
            self.refresh()
            self.status.configure(text=f"Restored the version from {ver['created']}. Save to keep it.")

    def open_library(self):
        result = LibraryDialog(self).run()
        if not result:
            return
        action, rec = result
        if action == "copy":
            self.copy_to_project(source=rec)
            return
        if not self.confirm_leave():
            return
        project = self.store.get_project(rec["project_id"])
        self.request = None
        self.set_project(project)
        self.open_request(rec["id"], confirm=False)

    def open_settings(self):
        values = SettingsDialog(self).run()
        if values:
            self.update_settings(values)

    def open_github(self):
        GitHubDialog(self).run()

    def update_settings(self, values):
        self.config_data.setdefault("settings", {}).update(values)
        self.settings.update(values)
        save_json(CONFIG_FILE, self.config_data)
        self.refresh()

    def copy_prompt(self):
        issues = engine.problems(*self.build_args())
        if issues and not messagebox.askyesno(
            "Prompt is missing something",
            "\n".join(issues) + "\n\nThe agent will have to guess or ask about these. Copy anyway?",
        ):
            return
        # Save first: saving a new request assigns its id, which changes the branch name and
        # heading, so the clipboard must get the text as saved.
        note = ""
        if (
            self.project
            and self.form.values().get("title", "").strip()
            and (self.request is None or self.is_dirty())
        ):
            if not self.save(note="copied", quiet=True):
                note = " Not saved."
        elif not self.project:
            note = " Not saved: no project selected."
        elif not self.form.values().get("title", "").strip():
            note = " Not saved: no title."
        self.clipboard_clear()
        self.clipboard_append(self.prompt_text())
        count = len(self.store.list_versions(self.request["id"])) if self.request else 0
        if count and not note and not self.is_dirty():
            msg = f"Copied request #{self.request['id']} version {count} to the clipboard."
        else:
            msg = "Copied the unsaved prompt to the clipboard." + note
        self.status.configure(text=msg)

    def export_md(self):
        PROMPTS_DIR.mkdir(exist_ok=True)
        rid = self.request["id"] if self.request else "new"
        title = engine.slug(self.form.values().get("title", "")) or self.current["id"]
        path = PROMPTS_DIR / f"{rid}_{title}_{datetime.now():%Y%m%d_%H%M}.md"
        path.write_text(self.prompt_text(), encoding="utf-8")
        self.status.configure(text=f"Exported {path}")
        os.startfile(PROMPTS_DIR)

    # -- shutdown -----------------------------------------------------------

    def remember(self):
        self.config_data.update(
            {
                "last_project": self.project["id"] if self.project else None,
                "last_request": self.request["id"] if self.request else None,
                "last_template": self.current["id"] if self.current else None,
                "target": self.target_id(),
                "capabilities": self.capabilities(),
                "geometry": self.geometry(),
            }
        )
        save_json(CONFIG_FILE, self.config_data)

    def on_close(self):
        if not self.confirm_leave():
            return
        self.remember()
        self.store.close()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
