"""
GitHub token storage and checks.

Tokens are stored in Windows Credential Manager (encrypted under your Windows
login, visible in Control Panel > Credential Manager > Windows Credentials as
"AgentPromptBuilder:github:<username>"). They are never written to
config.json, prompts.db or a prompt.

Agents don't read these entries. "Log gh in" hands the token to the GitHub
CLI, which keeps its own copy, and prompts then use
`GH_TOKEN="$(gh auth token --user <name>)"` so the token never appears in a
prompt or a transcript.

Standard library only (ctypes for Credential Manager, urllib for the API).
"""

import ctypes
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from ctypes import wintypes

TARGET_PREFIX = "AgentPromptBuilder:github:"

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2  # survives logoff; still only readable by this Windows user
ERROR_NOT_FOUND = 1168


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_PCRED = ctypes.POINTER(_CREDENTIAL)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_advapi32.CredWriteW.argtypes = [_PCRED, wintypes.DWORD]
_advapi32.CredWriteW.restype = wintypes.BOOL
_advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_PCRED)]
_advapi32.CredReadW.restype = wintypes.BOOL
_advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
_advapi32.CredDeleteW.restype = wintypes.BOOL
_advapi32.CredFree.argtypes = [ctypes.c_void_p]
_advapi32.CredFree.restype = None


def _target(username: str) -> str:
    return TARGET_PREFIX + username.strip().lower()


# ---------------------------------------------------------------------------
# Credential Manager
# ---------------------------------------------------------------------------


def save_token(username: str, token: str) -> None:
    blob = token.strip().encode("utf-8")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = _CREDENTIAL()
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = _target(username)
    cred.Comment = "GitHub token saved by Agent Prompt Builder"
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = username.strip()
    if not _advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def load_token(username: str) -> str | None:
    """The stored token, or None if there isn't one."""
    if not username.strip():
        return None
    p = _PCRED()
    if not _advapi32.CredReadW(_target(username), CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
        err = ctypes.get_last_error()
        if err == ERROR_NOT_FOUND:
            return None
        raise ctypes.WinError(err)
    try:
        c = p.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-8")
    finally:
        _advapi32.CredFree(p)


def delete_token(username: str) -> None:
    if not _advapi32.CredDeleteW(_target(username), CRED_TYPE_GENERIC, 0):
        err = ctypes.get_last_error()
        if err != ERROR_NOT_FOUND:
            raise ctypes.WinError(err)


def masked(token: str | None) -> str:
    return f"...{token[-4:]}" if token and len(token) > 8 else "(set)"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def test_token(token: str) -> tuple[bool, str]:
    """Ask the GitHub API who the token belongs to. Returns (ok, message)."""
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token.strip()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AgentPromptBuilder",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            user = json.load(resp)
            scopes = resp.headers.get("X-OAuth-Scopes")
            expires = resp.headers.get("GitHub-Authentication-Token-Expiration")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False, "GitHub rejected the token (401): it's wrong, expired or revoked."
        return False, f"GitHub returned HTTP {exc.code}."
    except urllib.error.URLError as exc:
        return False, f"Couldn't reach GitHub: {exc.reason}"
    except TimeoutError:
        return False, "GitHub didn't answer within 15 seconds."
    except OSError as exc:
        return False, f"Couldn't reach GitHub: {exc}"
    except ValueError:  # includes json.JSONDecodeError
        return False, "GitHub sent a response that isn't valid JSON."

    lines = [f"Valid token for '{user.get('login')}'."]
    if scopes is not None:  # classic tokens report scopes; fine-grained ones don't
        lines.append(f"Classic token scopes: {scopes or '(none)'}")
        missing = [
            s for s in ("repo", "workflow", "read:org") if s not in [x.strip() for x in scopes.split(",")]
        ]
        if missing:
            lines.append("Missing for this tool: " + ", ".join(missing))
    else:
        lines.append(
            "Fine-grained token: check it has Contents, Pull requests, Workflows and "
            "Metadata access to the repos you'll use."
        )
    if expires:
        lines.append(f"Expires: {expires}")
    return True, "\n".join(lines)


def gh_path() -> str | None:
    return shutil.which("gh")


# Keep gh from flashing a console window when the app runs under pythonw (0 off Windows).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_gh(args: list[str], stdin: str | None = None) -> tuple[bool, str]:
    """Run gh with args. Returns (ok, combined output); failures to run come back as (False, why)."""
    try:
        run = subprocess.run(
            [gh_path(), *args],
            input=stdin,
            text=True,
            capture_output=True,
            timeout=60,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return False, f"gh {args[0]} {args[1]} timed out after 60 seconds."
    except OSError as exc:
        return False, f"Couldn't run gh: {exc}"
    return run.returncode == 0, ((run.stdout or "") + (run.stderr or "")).strip()


def gh_login(token: str) -> tuple[bool, str]:
    """Log the GitHub CLI in with this token (gh keeps its own copy). Returns (ok, output)."""
    if not gh_path():
        return False, "The GitHub CLI isn't installed. Run: winget install --id GitHub.cli"
    ok, out = _run_gh(["auth", "login", "--hostname", "github.com", "--with-token"], token.strip())
    return ok, out or "gh is logged in with this token."


def gh_setup_git() -> tuple[bool, str]:
    if not gh_path():
        return False, "The GitHub CLI isn't installed. Run: winget install --id GitHub.cli"
    return _run_gh(["auth", "setup-git"])


def gh_status() -> str:
    """`gh auth status` output (gh masks the tokens itself)."""
    if not gh_path():
        return "The GitHub CLI isn't installed. Run: winget install --id GitHub.cli"
    return _run_gh(["auth", "status"])[1] or "(no output)"
