# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=2,<3"]
# ///
"""
agents MCP server: lets Claude Code on the developer's machine orchestrate the agents on this VM.

Tools: agents, start, stop, start_all, stop_all (over agents.sh); send, reply, wait, screen, key (prompts in,
replies out, dialogs answered); diff, transcript (what an agent did); add_task, tasks, task, assign,
update_task (the task board in ~/agents/.tasks; add_task can fill a task from Jira); queue, events (queue
mode and the event stream, both run by the watcher in watch.py).

Run over stdio by ./serve, as alex. Everything an agent writes (its state, reply and task report under
~/agents/.status/<name>, .git/HEAD in its workspace) is opened without following symlinks and reduced to
printable text before it is returned: the agent runs as UID 1000, the same UID as alex, so a symlink it
plants in a mounted folder would otherwise point this process at any of alex's files. Agent text is still
agent text: a reply or a task note is what the agent chose to say, not a fact.
"""
from __future__ import annotations

import base64
import collections
import contextlib
import fcntl
import json
import os
import re
import stat
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

HOME = Path.home()
REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_SH = REPO_ROOT / "agents.sh"
AGENTS_DIR = HOME / "agents"
STATUS_DIR = AGENTS_DIR / ".status"
TASKS_DIR = AGENTS_DIR / ".tasks"
QUEUE_DIR = AGENTS_DIR / ".queue"          # one empty file per agent in queue mode (out of the agents' reach)
EVENTS_FILE = AGENTS_DIR / "events.jsonl"  # written by the watcher (watch.py)
WATCH_PID = AGENTS_DIR / ".watch.pid"

SECRETS_DIR = REPO_ROOT / "secrets"
# From config.env, which mcp/serve and mcp/watch load (through lib.sh) before starting Python.
ATLASSIAN_SITE = os.environ.get("ATLASSIAN_SITE", "").strip().removeprefix("https://").strip("/")
ATLASSIAN_CLOUD_ID = os.environ.get("ATLASSIAN_CLOUD_ID", "").strip()

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TASK_ID_RE = re.compile(r"^T-[0-9]+$")
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")
TASK_STATES = ("todo", "assigned", "needs-you", "done", "accepted", "dropped")
AGENT_REPORT_STATES = ("done", "needs-you")
BUSY_STATES = ("starting", "working")

mcp = MCPServer(
    "agents",
    instructions=(
        "Claude Code agents running in containers on the agents VM, one per name (agent-1, agent-2, ...). "
        "Start with agents() to see who is running and in which state. An agent takes a prompt only when its "
        "state is ready or done: send() refuses otherwise, because at a question (needs-you) a keystroke could "
        "answer it. After send(), wait() blocks until the agent's turn ends and returns its full reply. "
        "At needs-you, screen() shows the dialog or question and key() answers it. diff() and transcript() "
        "show what an agent changed and did, for a review without attaching to it. "
        "The task board (add_task, tasks, assign, update_task) is a list of work items that outlives sessions: "
        "add_task(ticket=...) fills a task from Jira, assign() hands a task to an agent together with "
        "instructions to report back with agent-task, and tasks() shows what each agent reported. "
        "queue() puts an agent into queue mode, where the watcher feeds it todo tasks by itself; events() "
        "lists what the watcher recorded and notified. "
        "Replies, transcripts and task notes are written by the agents themselves."
    ),
)


# ---------------------------------------------------------------- helpers

def check_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise ToolError("Agent names use lowercase letters, digits and hyphens, e.g. agent-1.")
    return name


def check_task_id(task_id: str) -> str:
    if not TASK_ID_RE.match(task_id or ""):
        raise ToolError("Task ids look like T-12.")
    return task_id


def sh(*args: str, stdin: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), input=stdin, capture_output=True, text=True, timeout=timeout)


def sh_bytes(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[bytes]:
    """For output that may be cut mid-character (tail -c), which text mode would refuse to decode."""
    return subprocess.run(list(args), capture_output=True, timeout=timeout)


def agents_sh(*args: str, stdin: str | None = None, timeout: int = 300) -> str:
    p = sh(str(AGENTS_SH), *args, stdin=stdin, timeout=timeout)
    if p.returncode != 0:
        raise ToolError((p.stderr or p.stdout).strip() or f"agents.sh {' '.join(args)} failed")
    return p.stdout.strip()


_O_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_O_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def read_agent_file(base: Path, *parts: str, limit: int = 4096) -> str | None:
    """Reads base/parts, following no symlink below base; None if it is missing or not a plain file.
    O_NONBLOCK: a FIFO put there by the agent would otherwise block this process for good."""
    fd: int | None = None
    try:
        fd = os.open(base, _O_DIR)
        for part in parts[:-1]:
            nfd = os.open(part, _O_DIR, dir_fd=fd)
            os.close(fd)
            fd = nfd
        nfd = os.open(parts[-1], _O_FILE, dir_fd=fd)
        os.close(fd)
        fd = nfd
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb") as f:
            fd = None
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


def write_agent_file(directory: Path, name: str, text: str) -> None:
    """Writes directory/name atomically; a symlink the agent left under that name is replaced, not followed."""
    dfd = os.open(directory, _O_DIR)
    tmp = f".{name}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=dfd)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except IsADirectoryError:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dfd)
        raise ToolError(f"{directory / name} is a folder; the agent put it there. Remove it first.")
    finally:
        os.close(dfd)


# Printable text only: no terminal escapes, no C0/C1 controls, no bidi overrides. Newlines and tabs
# survive only where multiline is set.
_UNSAFE_MULTILINE = re.compile(r"[^\t\n\x20-\x7e -￿]|[‪-‮⁦-⁩]")
_UNSAFE_LINE = re.compile(r"[^\x20-\x7e -￿]|[‪-‮⁦-⁩]")


def clean(text: str | None, limit: int, multiline: bool = False) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n")
    text = (_UNSAFE_MULTILINE if multiline else _UNSAFE_LINE).sub("" if multiline else " ", text)
    return text[:limit]


def ago(ts: int | None) -> str:
    if ts is None:
        return "-"
    s = max(0, int(time.time()) - ts)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{s % 3600 // 60:02d}m"
    return f"{s // 86400}d"


def when(ts: int | None) -> str | None:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else None


def natural(name: str) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def agent_names() -> list[str]:
    if not AGENTS_DIR.is_dir():
        return []
    names = [p.name for p in AGENTS_DIR.iterdir() if p.is_dir() and not p.is_symlink() and NAME_RE.match(p.name)]
    return sorted(names, key=natural)


def parse_status_line(raw: str | None) -> tuple[str, int | None, str]:
    """<state> TAB <unix time> TAB <detail>, as agent-status writes it."""
    if not raw:
        return "-", None, ""
    fields = raw.split("\n", 1)[0].split("\t")
    state = re.sub(r"[^a-z-]", "", fields[0])[:10] or "-"
    ts = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else None
    detail = clean(fields[2] if len(fields) > 2 else "", 200).strip()
    return state, ts, detail


def agent_state(name: str) -> tuple[str, int | None, str]:
    return parse_status_line(read_agent_file(STATUS_DIR, name, "state", limit=1024))


def running_containers() -> set[str]:
    p = sh("docker", "ps", "--format", "{{.Names}}", "--filter", "label=com.docker.compose.service=agent")
    return set(p.stdout.split()) if p.returncode == 0 else set()


def tmux_sessions() -> dict[str, bool]:
    """session name -> attached"""
    p = sh("tmux", "list-sessions", "-F", "#{session_name}\t#{session_attached}")
    out: dict[str, bool] = {}
    if p.returncode == 0:
        for line in p.stdout.splitlines():
            name, _, attached = line.partition("\t")
            out[name] = attached.strip() not in ("", "0")
    return out


def container_stats() -> dict[str, tuple[str, str]]:
    p = sh("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}", timeout=60)
    out: dict[str, tuple[str, str]] = {}
    if p.returncode == 0:
        for line in p.stdout.splitlines():
            name, cpu, mem = (line.split("\t") + ["", ""])[:3]
            mem = mem.split("/")[0].strip()
            out[name] = (cpu.strip(), mem)
    return out


def reply_info(name: str) -> dict:
    state, ts, detail = agent_state(name)
    text = read_agent_file(STATUS_DIR, name, "reply", limit=65536)
    return {
        "agent": name,
        "state": state,
        "since": ago(ts),
        "detail": detail,
        "reply": clean(text, 65536, multiline=True).strip(),
    }


# ---------------------------------------------------------------- agents

@mcp.tool()
def agents(stats: bool = False) -> list[dict]:
    """Every agent with its state. Fields: name; running (container up); state: ready (waiting for a prompt),
    working, needs-you (a question or permission prompt is open), done (turn finished; detail is the last
    line of the reply), ended (Claude Code exited), stopped, or "-" (never started); since (how long in that
    state); detail; tmux (attached means someone is watching it); rider (Rider backend: connected, idle, off);
    branch; changes (modified files); unattended (started with --dangerously-skip-permissions); queue (in
    queue mode, see queue()); task (its current board task and that task's state). stats=true adds cpu and
    mem per container, which takes about two seconds."""
    running = running_containers()
    sessions = tmux_sessions()
    usage = container_stats() if stats and running else {}
    with board_lock():
        board = _sync_reports(_all_tasks())
    in_queue = set(queue_agents())
    rows = []
    for name in agent_names():
        state, ts, detail = agent_state(name)
        is_running = name in running
        if not is_running and state not in ("ended", "-"):
            state, detail = "stopped", ""
        rider = "-"
        if is_running:
            r_state, r_ts, _ = parse_status_line(read_agent_file(STATUS_DIR, name, "rider", limit=256))
            rider = r_state if r_state != "idle" else f"idle {ago(r_ts)}"
        branch = "-"
        head = read_agent_file(AGENTS_DIR, name, ".git", "HEAD", limit=512)
        if head:
            branch = clean(head.split("\n", 1)[0].removeprefix("ref: refs/heads/"), 80).strip() or "-"
        changes: int | str | None = None
        if is_running:
            p = sh("docker", "exec", name, "sh", "-c", "git status --porcelain 2>/dev/null | wc -l", timeout=60)
            changes = int(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip().isdigit() else "?"
        row = {
            "name": name,
            "running": is_running,
            "state": state,
            "since": ago(ts),
            "detail": detail,
            "tmux": ("attached" if sessions[name] else "detached") if name in sessions else "-",
            "rider": rider,
            "branch": branch,
            "changes": changes,
            "unattended": (STATUS_DIR / name / "unattended").exists(),
            "queue": name in in_queue,
            "task": f"{current['id']} ({current['state']})" if (current := _current_task(name, board)) else None,
        }
        if stats:
            cpu, mem = usage.get(name, ("-", "-"))
            row["cpu"], row["mem"] = cpu, mem
        rows.append(row)
    return rows


@mcp.tool()
def start(agent: str, unattended: bool = False, resume: bool = False, prompt: str | None = None) -> str:
    """Starts an agent (Claude Code in its own container and tmux session). A new name gets a fresh clone of
    the configured repository first, which can take minutes; the agent is ready when agents() shows state ready. unattended
    runs it with --dangerously-skip-permissions (no permission prompts); resume continues its last
    conversation; prompt is the first prompt."""
    check_name(agent)
    args = ["start", agent]
    if unattended:
        args.append("--unattended")
    if resume:
        args.append("--continue")
    if prompt and prompt.strip():
        args += ["--", prompt.strip()]
    return agents_sh(*args)


@mcp.tool()
def stop(agent: str) -> str:
    """Stops an agent: ends Claude Code and its container. The workspace and the conversation are kept, so
    start(resume=true) continues where it left off. Stopping an agent that is working cuts off its turn."""
    return agents_sh("stop", check_name(agent))


@mcp.tool()
def start_all(unattended: bool = False) -> str:
    """Starts every agent that has a workspace and isn't running, each resuming its last conversation in the
    mode it last ran in (unattended=true forces that mode for all)."""
    return agents_sh("start-all", *(["--unattended"] if unattended else []))


@mcp.tool()
def stop_all(force: bool = False) -> str:
    """Stops every running agent. One that is working is skipped unless force=true."""
    return agents_sh("stop-all", *(["--force"] if force else []))


# ---------------------------------------------------------------- prompts and replies

@mcp.tool()
def send(agent: str, prompt: str, force: bool = False) -> str:
    """Gives a running agent a prompt, like typing it into its session. The prompt may be long and
    multi-line; it is written to a file the agent is told to read. Only an agent in state ready or done
    takes it; force=true sends it anyway (while working, Claude Code queues it for the next turn; at a
    question it may be taken as the answer). Follow with wait() for the reply."""
    check_name(agent)
    if not prompt or not prompt.strip():
        raise ToolError("The prompt is empty.")
    p = sh(str(AGENTS_SH), "send", agent, *(["--force"] if force else []), stdin=prompt, timeout=60)
    if p.returncode == 1:
        raise ToolError(p.stderr.strip() or "send failed")
    return (p.stdout + p.stderr).strip()


@mcp.tool()
def reply(agent: str) -> dict:
    """The agent's current state and its last full reply (the text of its final message in the last turn
    that ended). While it is working, the reply is still the previous one."""
    return reply_info(check_name(agent))


@mcp.tool()
def wait(agent: str, seconds: int = 120) -> dict:
    """Waits until the agent's turn ends (state done, needs-you or ended), up to the given seconds (1 to 600),
    and returns its state and full reply, as reply() does, plus waited. If it is still working when the time
    is up, state is working; call wait() again."""
    check_name(agent)
    seconds = max(1, min(600, int(seconds)))
    started = time.time()
    while True:
        state, _, _ = agent_state(agent)
        if state not in BUSY_STATES or time.time() - started >= seconds:
            info = reply_info(agent)
            info["waited"] = f"{int(time.time() - started)}s"
            if info["state"] in BUSY_STATES:
                info["reply"] = ""
                info["note"] = "still working; the reply above is not ready yet, call wait() again"
            return info
        time.sleep(1)


def screen_text(agent: str, lines: int) -> str:
    lines = max(5, min(500, int(lines)))
    # "=name:" is the session named exactly that, its current window; a bare "=name" only matches a window.
    p = sh("tmux", "capture-pane", "-p", "-J", "-t", f"={agent}:", "-S", f"-{lines}")
    if p.returncode != 0:
        raise ToolError(f"{agent} has no session; is it running?")
    text = "\n".join(line.rstrip() for line in clean(p.stdout, 200000, multiline=True).splitlines())
    return text.strip("\n")


@mcp.tool()
def screen(agent: str, lines: int = 50) -> str:
    """The last lines of the agent's terminal (its tmux pane), what you would see with agents.sh attach:
    useful when it is needs-you, to read the question or the permission prompt it is showing."""
    return screen_text(check_name(agent), lines)


@mcp.tool()
def key(agent: str, key: str) -> str:
    """Presses one key in the agent's terminal, to answer what it is showing: a permission dialog (1 to 4,
    or y/n), a question with choices (enter takes the highlighted one), or escape to dismiss a dialog or
    interrupt the turn. Allowed: enter, escape, 1, 2, 3, 4, y, n. The key goes to whatever is on screen,
    so look at screen() first; at an empty prompt a digit or letter is simply typed into the input box.
    Returns the screen a second later."""
    check_name(agent)
    k = (key or "").strip().lower()
    named = {"enter": ["Enter"], "return": ["Enter"], "escape": ["Escape"], "esc": ["Escape"]}
    if k in named:
        args = named[k]
    elif k in ("1", "2", "3", "4", "y", "n"):
        args = ["-l", k]
    else:
        raise ToolError("Allowed keys: enter, escape, 1, 2, 3, 4, y, n.")
    p = sh("tmux", "send-keys", "-t", f"={agent}:", *args)
    if p.returncode != 0:
        raise ToolError(f"{agent} has no session; is it running?")
    time.sleep(1)
    return screen_text(agent, 25)


# ---------------------------------------------------------------- what an agent did

def git_in(agent: str, *args: str) -> str:
    """Runs git in the agent's workspace inside a container, never on the VM side, because the agent controls
    that repo's config and hooks: in its running container, or else in a throwaway one on the same image."""
    if agent in running_containers():
        cmd = ["docker", "exec", agent, "git", "-C", "/workspace", *args]
    else:
        cmd = ["docker", "run", "--rm", "--network", "none", "-v", f"{AGENTS_DIR / agent}:/workspace",
               "--entrypoint", "git", "agents/agent", "-C", "/workspace", *args]
    p = sh(*cmd, timeout=120)
    if p.returncode != 0:
        raise ToolError(clean(p.stderr.strip() or p.stdout.strip(), 2000) or f"git {' '.join(args)} failed in {agent}")
    return p.stdout


@mcp.tool()
def diff(agent: str, full: bool = False, path: str | None = None) -> str:
    """What the agent has changed in its workspace, from git run inside its container: the branch, git status
    --short, and git diff --stat against HEAD (staged and unstaged; untracked files show in the status only).
    full=true adds the diff itself, for everything or for one path (a file or folder, relative to the repo
    root). Cut at 200000 characters. Works for a stopped agent too."""
    check_name(agent)
    if path is not None:
        path = path.strip()
        if not path or path.startswith(("-", "/")) or ".." in path.split("/"):
            raise ToolError("path is a file or folder relative to the repo root.")
    if agent not in running_containers() and not (AGENTS_DIR / agent).is_dir():
        raise ToolError(f"{agent} has no workspace.")
    branch = git_in(agent, "rev-parse", "--abbrev-ref", "HEAD").strip()
    status = git_in(agent, "status", "--short").rstrip()
    stat_ = git_in(agent, "diff", "HEAD", "--stat").rstrip()
    out = f"branch: {branch}\n\n{status or '(nothing changed)'}\n\n{stat_}".rstrip()
    if full:
        out += "\n\n" + git_in(agent, "diff", "HEAD", *(["--", path] if path else []))
    out = clean(out, 200001, multiline=True)
    if len(out) > 200000:
        out = out[:200000] + "\n[... cut at 200000 characters]"
    return out


def transcript_lines(agent: str) -> list[str]:
    """The tail of the agent's newest Claude Code transcript (~/.claude/projects/-workspace/<session>.jsonl in
    its claude volume), read inside a container. Newest with a conversation in it: Claude Code also writes
    small side files there (the session's title) that would otherwise win by modification time."""
    find = ('for f in $(ls -t {d}/*.jsonl 2>/dev/null); do '
            'grep -q \'"type":"user"\' "$f" && {{ tail -c 4000000 "$f"; break; }}; done')
    if agent in running_containers():
        p = sh_bytes("docker", "exec", agent, "sh", "-c", find.format(d="/home/agent/.claude/projects/-workspace"), timeout=60)
    else:
        p = sh_bytes("docker", "run", "--rm", "--network", "none", "-v", f"{agent}-claude:/c:ro", "--entrypoint", "sh",
                     "agents/agent", "-c", find.format(d="/c/projects/-workspace"), timeout=120)
    if p.returncode != 0 or not p.stdout.strip():
        raise ToolError(f"{agent} has no transcript yet.")
    lines = p.stdout.decode("utf-8", "replace").split("\n")
    # tail -c may have started in the middle of a line.
    return lines[1:] if len(p.stdout) >= 4000000 else lines


def tool_summary(block: dict) -> str:
    name = block.get("name") or "?"
    inp = block.get("input")
    if not isinstance(inp, dict) or not inp:
        return name
    for k in ("command", "file_path", "pattern", "query", "description", "prompt", "url"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return f"{name}: {v.strip().splitlines()[0][:200]}"
    return f"{name}: {json.dumps(inp, ensure_ascii=False)[:150]}"


def local_time(ts: str | None) -> str:
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return "?"


def parse_transcript(lines: list[str], turns: int) -> list[tuple[str, str, str]]:
    """(kind, text, time) items: prompt, agent (its text), tool (one line per tool call), error (a failed
    tool call); the last `turns` prompts and what followed them."""
    items: list[tuple[str, str, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("isSidechain") or e.get("isMeta"):
            continue
        kind, ts = e.get("type"), local_time(e.get("timestamp"))
        content = (e.get("message") or {}).get("content")
        if kind == "user":
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    t = (b.get("text") or "").strip()
                    # Lines starting with "<" are Claude Code's own records (<command-name>, <local-command-stdout>).
                    if t and not t.startswith("<"):
                        items.append(("prompt", t, ts))
                elif b.get("type") == "tool_result" and b.get("is_error"):
                    c = b.get("content")
                    if isinstance(c, list):
                        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                    items.append(("error", str(c or "").strip(), ts))
        elif kind == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and (b.get("text") or "").strip():
                    items.append(("agent", b["text"].strip(), ts))
                elif b.get("type") == "tool_use":
                    items.append(("tool", tool_summary(b), ts))
    starts = [i for i, it in enumerate(items) if it[0] == "prompt"]
    if len(starts) > turns:
        items = items[starts[-turns]:]
    return items


@mcp.tool()
def transcript(agent: str, turns: int = 3) -> str:
    """The agent's last conversation turns from its Claude Code transcript: each prompt it got, the tools it
    ran (one line each, failed ones marked !), and what it replied. turns is how many prompts back to go
    (1 to 20). For reviewing what an agent did without attaching to it; works for a stopped agent too.
    Cut at 60000 characters, keeping the latest part."""
    check_name(agent)
    turns = max(1, min(20, int(turns)))
    out: list[str] = []
    for kind, text, ts in parse_transcript(transcript_lines(agent), turns):
        if kind == "prompt":
            out.append(f"\n=== {ts} prompt\n{text[:3000]}")
        elif kind == "tool":
            out.append(f"  > {text}")
        elif kind == "error":
            out.append(f"  ! {text[:300]}")
        else:
            out.append(f"--- {ts} agent\n{text[:6000]}")
    text = clean("\n".join(out).strip(), 10_000_000, multiline=True)
    if len(text) > 60000:
        text = "[... earlier part cut]\n" + text[-60000:]
    return text or f"{agent}'s transcript has no turns yet."


# ---------------------------------------------------------------- task board

@contextlib.contextmanager
def board_lock():
    TASKS_DIR.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(TASKS_DIR / ".lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def _load_task(task_id: str) -> dict:
    path = _task_path(check_task_id(task_id))
    if not path.is_file():
        raise ToolError(f"There is no task {task_id}.")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_task(task: dict) -> None:
    path = _task_path(task["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(task, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _all_tasks() -> list[dict]:
    if not TASKS_DIR.is_dir():
        return []
    tasks = [json.loads(p.read_text(encoding="utf-8")) for p in TASKS_DIR.glob("T-*.json")]
    return sorted(tasks, key=lambda t: int(t["id"][2:]))


def _next_id() -> str:
    used = [int(p.stem[2:]) for p in TASKS_DIR.glob("T-*.json") if p.stem[2:].isdigit()]
    return f"T-{max(used, default=0) + 1}"


def _now() -> int:
    return int(time.time())


def _history(task: dict, by: str, state: str | None, note: str | None, at: int | None = None) -> None:
    task["history"].append({"at": at or _now(), "by": by, "state": state, "note": note or ""})
    task["updated_at"] = at or _now()


def _current_task(agent: str, tasks: list[dict]) -> dict | None:
    """The agent's current board task: its latest assignment among the open ones. A done task stays current
    until the agent gets a new assignment, so a report after a follow-up prompt lands on it."""
    mine = [t for t in tasks if t.get("agent") == agent and t["state"] in ("assigned", "needs-you", "done")]
    return max(mine, key=lambda t: t.get("assigned_at", 0)) if mine else None


def _sync_reports(tasks: list[dict]) -> list[dict]:
    """Applies what agents reported with agent-task (<state> TAB <unix time> TAB <note> in
    .status/<agent>/task), each to its agent's current task only. A report counts once, and only if it is
    newer than the assignment, so a report about an earlier task of the same agent is ignored."""
    for agent in {t["agent"] for t in tasks if t.get("agent")}:
        if not NAME_RE.match(agent):
            continue
        task = _current_task(agent, tasks)
        if task is None:
            continue
        state, ts, note = parse_status_line(read_agent_file(STATUS_DIR, agent, "task", limit=2048))
        if state not in AGENT_REPORT_STATES or ts is None:
            continue
        if ts < task.get("assigned_at", 0) or ts <= task.get("reported_at", 0):
            continue
        task["state"] = state
        task["reported_at"] = ts
        _history(task, agent, state, clean(note, 500), at=ts)
        _save_task(task)
    return tasks


def queue_agents() -> list[str]:
    if not QUEUE_DIR.is_dir():
        return []
    return sorted((p.name for p in QUEUE_DIR.iterdir() if NAME_RE.match(p.name)), key=natural)


def watcher_pid() -> int | None:
    """The watcher's pid when it is running: the pid file must name a live process running watch.py."""
    try:
        pid = int(WATCH_PID.read_text().strip())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return None
    return pid if b"watch.py" in cmdline else None


def _present(task: dict, history: bool = False) -> dict:
    out = {
        "id": task["id"],
        "state": task["state"],
        "ticket": task.get("ticket"),
        "title": task["title"],
        "agent": task.get("agent"),
        "notes": task.get("notes") or "",
        "created": when(task.get("created_at")),
        "updated": when(task.get("updated_at")),
        "last": (task["history"][-1] | {"at": when(task["history"][-1]["at"])}) if task.get("history") else None,
    }
    if history:
        out["history"] = [h | {"at": when(h["at"])} for h in task["history"]]
    return out


UNATTENDED_INSTRUCTIONS = (
    "This task comes from the queue: nobody looks at your work before you move on to the next task, so what "
    "you push is the deliverable. If the task changes code: begin by making sure the workspace is clean and on "
    "an up-to-date default branch (the one origin/HEAD points to; if the previous task left anything "
    "uncommitted, commit it on its own branch first), create this task's branch following the project's branch "
    "naming rules, and when the work is complete commit it there following the project's commit conventions, "
    "push the branch (never the default or a release branch, never force-push), name the branch in your report, "
    "and check out the default branch again so the workspace is ready for the next task. If the task changes "
    "nothing, say so in the report and create no branch.\n\n"
)


def _task_prompt(task: dict, unattended: bool = False) -> str:
    ticket = f" ({task['ticket']})" if task.get("ticket") else ""
    notes = (task.get("notes") or "").strip()
    return (
        f"Task {task['id']}{ticket}: {task['title']}\n\n"
        + (f"{notes}\n\n" if notes else "")
        + (UNATTENDED_INSTRUCTIONS if unattended else "")
        + "When you're finished, report with:  agent-task done \"<one line: what you did, and the PR or branch>\"\n"
        + "If you're blocked or need a decision from the developer, report with:  agent-task needs-you \"<what you need>\"\n"
        + "Report once, at the end of your work; the developer reads it from the task board.\n"
    )


def jira_issue(key: str) -> dict:
    """Summary, description, status and type of a Jira issue, through the read-only token in secrets/ (the
    same token the agents' jira command uses; scoped tokens only work through the api.atlassian.com gateway)."""
    if not ATLASSIAN_SITE or not ATLASSIAN_CLOUD_ID:
        raise ToolError("Jira isn't configured: set ATLASSIAN_SITE and ATLASSIAN_CLOUD_ID in config.env on the VM, "
                        "or give the task a title.")
    try:
        email = (SECRETS_DIR / "atlassian-email").read_text().strip()
        token = (SECRETS_DIR / "jira-token").read_text().strip()
    except OSError:
        raise ToolError("The Jira token or the Atlassian email is missing in secrets/ on the VM (save-secret.sh).")
    url = (f"https://api.atlassian.com/ex/jira/{ATLASSIAN_CLOUD_ID}/rest/api/2/issue/{key}"
           "?fields=summary,description,status,issuetype")
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    req = urllib.request.Request(url, headers={"Accept": "application/json", "Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ToolError(f"Jira has no issue {key}.")
        raise ToolError(f"Jira answered {e.code} for {key}; the token may have expired (they last about 90 days).")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ToolError(f"Jira isn't reachable: {e}")
    f = data.get("fields") or {}
    return {
        "summary": (f.get("summary") or key).strip(),
        "description": f.get("description") or "",
        "status": (f.get("status") or {}).get("name") or "?",
        "type": (f.get("issuetype") or {}).get("name") or "Issue",
    }


@mcp.tool()
def add_task(title: str | None = None, ticket: str | None = None, notes: str | None = None) -> dict:
    """Adds a task to the board (state todo). ticket is the Jira key, e.g. KEY-123. With a ticket and no
    title, the title and notes come from Jira: the issue's summary, and a link plus its description, with
    your notes appended. notes is what the agent should know or do; it becomes part of the prompt when the
    task is assigned (the agents can read the issue themselves with their jira command as well)."""
    title = (title or "").strip()
    ticket = (ticket or "").strip().upper() or None
    notes = (notes or "").strip()
    if ticket and not TICKET_RE.match(ticket):
        raise ToolError("ticket is a Jira key like KEY-123.")
    if not title:
        if not ticket:
            raise ToolError("Give a title, or a ticket to take the title from Jira.")
        issue = jira_issue(ticket)
        title = issue["summary"]
        description = issue["description"].strip()
        notes = (f"https://{ATLASSIAN_SITE}/browse/{ticket}  ({issue['type']}, {issue['status']})\n\n"
                 + (f"{description}\n\n" if description else "")
                 + (f"Notes from the developer:\n{notes}\n" if notes else "")).strip()
    with board_lock():
        now = _now()
        task = {
            "id": _next_id(),
            "title": title[:200],
            "ticket": ticket,
            "notes": notes[:20000],
            "state": "todo",
            "agent": None,
            "created_at": now,
            "updated_at": now,
            "assigned_at": 0,
            "reported_at": 0,
            "history": [],
        }
        _history(task, "board", "todo", None, at=now)
        _save_task(task)
    return _present(task)


@mcp.tool()
def tasks(state: str | None = None, all: bool = False) -> list[dict]:
    """The task board. By default the open tasks (todo, assigned, needs-you, done); all=true includes
    accepted and dropped ones; state filters to one state. States: todo, assigned (handed to an agent),
    needs-you (the agent asked for a decision; see last.note), done (the agent reported it finished; review
    it, then update_task to accepted), accepted, dropped."""
    if state is not None and state not in TASK_STATES:
        raise ToolError(f"state must be one of {', '.join(TASK_STATES)}.")
    with board_lock():
        rows = _sync_reports(_all_tasks())
    if state is not None:
        rows = [t for t in rows if t["state"] == state]
    elif not all:
        rows = [t for t in rows if t["state"] not in ("accepted", "dropped")]
    return [_present(t) for t in rows]


@mcp.tool()
def task(task_id: str) -> dict:
    """One task with its full history."""
    with board_lock():
        t = _sync_reports([_load_task(task_id)])[0]
    return _present(t, history=True)


@mcp.tool()
def assign(task_id: str, agent: str, send_now: bool = True, unattended: bool = False) -> dict:
    """Hands a task to an agent: sends it the task (title, ticket, notes) with instructions to report back
    through agent-task, and sets the task to assigned. The agent must be ready or done; otherwise nothing
    changes. send_now=false only records the assignment, for an agent you will prompt yourself.
    unattended=true adds the instructions queue mode uses: start from a clean, current default branch, commit
    on the task's branch, push it, name it in the report, and leave the workspace on the default branch for the
    next task."""
    check_name(agent)
    with board_lock():
        t = _load_task(task_id)
        if t["state"] in ("accepted", "dropped"):
            raise ToolError(f"{task_id} is {t['state']}; set it back to todo with update_task first.")
        t["agent"] = agent
        if send_now:
            p = sh(str(AGENTS_SH), "send", agent, stdin=_task_prompt(t, unattended), timeout=60)
            if p.returncode == 1:
                raise ToolError(p.stderr.strip() or "send failed")
            sent = (p.stdout + p.stderr).strip()
        else:
            sent = "not sent"
        t["state"] = "assigned"
        t["assigned_at"] = _now()
        _history(t, "queue" if unattended else "board", "assigned", f"{agent}: {sent}")
        _save_task(t)
    return _present(t)


@mcp.tool()
def queue(agent: str | None = None, on: bool | None = None) -> dict:
    """Queue mode. Without arguments: whether the watcher runs, which agents are in queue mode, and the
    unassigned todo tasks. With agent and on: puts that agent into queue mode (on=true) or takes it out. In
    queue mode the watcher (agents.sh watcher start on the VM; started at boot) gives the agent the oldest
    unassigned todo task whenever it is ready or done with no task in progress, with the unattended
    instructions (see assign). A task it reports done stays done for your review while it moves on to the
    next one. Keep the agents you talk to yourself out of queue mode."""
    if agent is not None:
        check_name(agent)
        if on is None:
            raise ToolError("Give on=true or on=false.")
        QUEUE_DIR.mkdir(mode=0o700, exist_ok=True)
        marker = QUEUE_DIR / agent
        if on:
            marker.touch()
        else:
            marker.unlink(missing_ok=True)
    with board_lock():
        tasks = _sync_reports(_all_tasks())
    pid = watcher_pid()
    return {
        "watcher": f"running (pid {pid})" if pid else "stopped; start it on the VM with: agents.sh watcher start",
        "queue_agents": queue_agents(),
        "todo": [f"{t['id']} {t['title']}" for t in tasks if t["state"] == "todo" and not t.get("agent")],
    }


@mcp.tool()
def events(limit: int = 20) -> list[dict]:
    """The latest events the watcher recorded, oldest first: a task reported done or needs-you, an agent
    asking for you (a permission prompt or a question), queue assignments, an agent whose Claude Code ended,
    a task an agent finished without reporting on. notify=true marks the ones that were sent as
    notifications (notify.sh on the VM, agents-notify on Windows)."""
    limit = max(1, min(200, int(limit)))
    if not EVENTS_FILE.is_file():
        return []
    out: list[dict] = []
    with EVENTS_FILE.open(encoding="utf-8", errors="replace") as f:
        for line in collections.deque(f, maxlen=limit):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


@mcp.tool()
def update_task(task_id: str, state: str | None = None, note: str | None = None,
                title: str | None = None, notes: str | None = None, ticket: str | None = None) -> dict:
    """Changes a task: its state (todo, assigned, needs-you, done, accepted, dropped), a note for its history,
    or its title, notes or ticket. Typical: accepted after reviewing a done task, dropped for one that is no
    longer needed, todo to put a task back on the list."""
    if state is not None and state not in TASK_STATES:
        raise ToolError(f"state must be one of {', '.join(TASK_STATES)}.")
    with board_lock():
        t = _sync_reports([_load_task(task_id)])[0]
        if title is not None and title.strip():
            t["title"] = title.strip()[:200]
        if notes is not None:
            t["notes"] = notes.strip()[:20000]
        if ticket is not None:
            t["ticket"] = ticket.strip().upper()[:20] or None
        if state is not None:
            t["state"] = state
            if state == "todo":
                t["agent"] = None
        if state is not None or note:
            _history(t, "board", state, (note or "").strip()[:2000])
        else:
            t["updated_at"] = _now()
        _save_task(t)
    return _present(t, history=True)


if __name__ == "__main__":
    mcp.run(transport="stdio")
