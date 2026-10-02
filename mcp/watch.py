# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=2,<3"]
# ///
"""
The agents watcher: one loop on the VM, started with "agents.sh watcher start" (and at boot by autostart.sh).
Every 15 seconds it
  - records events in ~/agents/events.jsonl: a task reported done or needs-you, an agent asking for you
    (a permission prompt or a question), queue assignments, an agent whose Claude Code ended by itself,
    a task an agent finished a turn on without reporting;
  - notifies you of the ones that need you: through notify.sh in the repo folder when that exists and is
    executable (see notify.sh.example), and through the event stream that agents-notify follows on Windows;
  - runs queue mode: an agent in queue mode (agents.sh queue <name> on) that is ready or done with no task
    in progress gets the oldest unassigned todo task, with the unattended instructions (serve.assign).
Failures are logged (~/agents/watch.log) and the loop goes on. The board logic is imported from serve.py.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import traceback

import serve
from serve import (AGENTS_DIR, EVENTS_FILE, WATCH_PID, _all_tasks, _current_task, _sync_reports, agent_names,
                   agent_state, board_lock, clean, queue_agents, running_containers)

INTERVAL = 15
NOTIFY = serve.REPO_ROOT / "notify.sh"
STATE_FILE = AGENTS_DIR / ".watch-state.json"


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def emit(kind: str, text: str, agent: str | None = None, task: str | None = None, notify: bool = False) -> None:
    event = {"at": int(time.time()), "time": time.strftime("%Y-%m-%d %H:%M"), "kind": kind,
             "agent": agent, "task": task, "text": clean(text, 500).strip(), "notify": notify}
    with EVENTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    log(f"{kind} {agent or ''} {task or ''}: {event['text']}")
    if notify and os.access(NOTIFY, os.X_OK):
        title = " ".join(x for x in (task, kind.replace("-", " "), f"({agent})" if agent else "") if x)
        try:
            subprocess.run([str(NOTIFY), title, event["text"]], timeout=30, capture_output=True)
        except (OSError, subprocess.SubprocessError) as e:
            log(f"notify.sh failed: {e}")


def load_state() -> dict:
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {"tasks": dict(s.get("tasks", {})), "agents": dict(s.get("agents", {})),
                "unreported": list(s.get("unreported", []))}
    except (OSError, ValueError):
        return {"tasks": {}, "agents": {}, "unreported": []}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def last_note(task: dict) -> str:
    return (task["history"][-1].get("note") or "") if task.get("history") else ""


def tick(known: dict, first: bool) -> None:
    with board_lock():
        tasks = _sync_reports(_all_tasks())

    # Task transitions.
    for t in tasks:
        prev, cur = known["tasks"].get(t["id"]), t["state"]
        if prev == cur:
            continue
        known["tasks"][t["id"]] = cur
        if first:
            continue
        label = f"{t['title']}: {last_note(t)}" if last_note(t) else t["title"]
        if cur == "done":
            emit("task-done", label, t.get("agent"), t["id"], notify=True)
        elif cur == "needs-you":
            emit("task-needs-you", label, t.get("agent"), t["id"], notify=True)
        elif cur == "assigned":
            emit("task-assigned", f"{t['title']} -> {t.get('agent')}", t.get("agent"), t["id"])
        elif cur == "todo" and prev is None:
            emit("task-added", t["title"], None, t["id"])
        else:
            emit(f"task-{cur}", t["title"], t.get("agent"), t["id"])

    # Agent transitions.
    running = running_containers()
    for name in agent_names():
        state, ts, detail = agent_state(name)
        if name not in running and state not in ("ended", "-"):
            state = "stopped"
        signature = f"{state}@{ts}"
        if known["agents"].get(name) == signature:
            continue
        known["agents"][name] = signature
        if first:
            continue
        if state == "needs-you" and "waiting for your input" not in detail:
            emit("agent-needs-you", detail or "a question or a permission prompt", name, None, notify=True)
        elif state == "ended" and name in running:
            # Claude Code ended while the container is still up: it exited or crashed; nobody stopped the agent.
            emit("agent-ended", detail or "Claude Code exited", name, None, notify=True)
        elif state == "done":
            # A finished turn on a task the agent has not reported on: it may have skipped agent-task.
            current = _current_task(name, tasks)
            if current and current["state"] == "assigned" and ts and ts > current.get("assigned_at", 0) + 10:
                key = f"{current['id']}@{ts}"
                if key not in known["unreported"]:
                    known["unreported"] = (known["unreported"] + [key])[-50:]
                    emit("task-unreported", f"{current['title']}: {name} finished a turn without reporting; see its reply",
                         name, current["id"], notify=True)

    # Queue mode.
    for name in queue_agents():
        if name not in running:
            continue
        state, _, _ = agent_state(name)
        if state not in ("ready", "done"):
            continue
        current = _current_task(name, tasks)
        if current is not None and current["state"] in ("assigned", "needs-you"):
            continue
        todo = next((t for t in tasks if t["state"] == "todo" and not t.get("agent")), None)
        if todo is None:
            continue
        try:
            serve.assign(todo["id"], name, send_now=True, unattended=True)
        except Exception as e:  # the agent changed state meanwhile, or docker/tmux trouble: next time
            log(f"queue: could not give {todo['id']} to {name}: {e}")
            continue
        known["tasks"][todo["id"]] = "assigned"
        emit("queue-assigned", f"{todo['title']} -> {name}", name, todo["id"])
        with board_lock():
            tasks = _all_tasks()


def main() -> None:
    AGENTS_DIR.mkdir(exist_ok=True)
    WATCH_PID.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    known = load_state()
    # The first run ever records what is there without reporting it; later starts report what changed meanwhile.
    first = not known["tasks"] and not known["agents"]
    log(f"watcher started (pid {os.getpid()}), every {INTERVAL} s")
    try:
        while True:
            try:
                tick(known, first)
                first = False
                save_state(known)
            except Exception:
                log(traceback.format_exc())
            time.sleep(INTERVAL)
    finally:
        try:
            if WATCH_PID.read_text().strip() == str(os.getpid()):
                WATCH_PID.unlink()
        except OSError:
            pass
        log("watcher stopped")


if __name__ == "__main__":
    main()
