# Using the agents

Three ways in, from the most hands-on to the most hands-off: a terminal tab per agent, `agents.sh` on the VM, and
the `agents` MCP server, which lets the Claude Code on your own machine drive the agents.

## Terminal tabs

`agents.sh attach <name>` puts you in the agent's live Claude Code session (tmux): type to it as usual, and
detach with Ctrl+B, then D. Closing the terminal also just detaches.

On Windows, `windows/agents-start.cmd` starts every agent on the VM and opens one Windows Terminal window: a tab
with `agents.sh watch`, a tab with notifications, and a tab per agent. A shortcut to it in your Startup folder
(Win+R, `shell:startup`) runs it at logon; it waits for the VM to answer first. Options: `/now` (skip the
initial 15 s wait), `/nowatch`, `/nonotify`, `/dry`. If your SSH alias isn't `agents` or the repo isn't in
`~/claude-agents-vm` on the VM, set `AGENTS_VM_HOST` or `AGENTS_VM_DIR` as Windows environment variables.

## agents.sh on the VM

| Command | Does |
| --- | --- |
| `build` | builds the images; restart agents to give them a new one |
| `start <name> [--unattended] [--continue] [-- <prompt>]` | starts an agent; a new name gets a fresh clone |
| `start-all`, `stop-all [--force]` | all agents; `stop-all` skips working agents unless forced |
| `list`, `watch [seconds]` | every agent's state, CPU, memory, Rider, branch, changes and last reply line |
| `attach <name>`, `shell <name>` | the agent's session; a shell in its container |
| `send <name> [--force] [<prompt>]` | gives an agent a prompt (or stdin), only when it's ready or done |
| `stop <name>` | stops it; the workspace and the conversation are kept for `start --continue` |
| `queue [<name> on\|off]`, `watcher start\|stop\|status` | queue mode and the watcher (below) |
| `autostart [on\|off]` | start everything when the VM boots (on after `vm/setup.sh`) |
| `rider-stop <name>` | stops the agent's Rider backend |

`--unattended` runs Claude Code with `--dangerously-skip-permissions`: no permission prompts, so the agent works
on its own. The container is its sandbox, but it can still push code and use every allowed host; see
[security.md](security.md). Without it, permission prompts appear in the session, and notifications tell you.

## The MCP server

Register it once in your Claude Code (user scope, so every project sees it). Replace `agents` if your SSH alias
differs, and the path if the repo lives elsewhere on the VM. On macOS and Linux:

```bash
claude mcp add --scope user agents -- ssh -o BatchMode=yes -o ServerAliveInterval=30 agents /home/<user>/claude-agents-vm/mcp/serve
```

On Windows, run the same from Git Bash with `MSYS_NO_PATHCONV=1` in front, so Git Bash doesn't rewrite the Linux
path. PowerShell's `claude` wrapper drops the `--`, which breaks the command there.

`claude mcp get agents` should then say Connected. A new Claude Code session has the tools; ask it in plain
language, for example "what are the agents doing?" or "send agent-2: …, and wait for the reply".

| Tool | Does |
| --- | --- |
| `agents` | every agent: state, since when, last reply line, tmux, Rider, branch, changes, mode, its task |
| `start`, `stop`, `start_all`, `stop_all` | as in agents.sh |
| `send`, `wait`, `reply` | a prompt in; block until the turn ends; the full last reply |
| `screen`, `key` | the agent's terminal; one key to answer a permission prompt or question |
| `diff`, `transcript` | what it changed (git inside its container); what it did, turn by turn |
| `add_task`, `tasks`, `task`, `assign`, `update_task` | the task board |
| `queue`, `events` | queue mode; what the watcher recorded |

### The task board

Tasks are work items that outlive sessions, stored as JSON in `~/agents/.tasks/` on the VM. States: `todo`,
`assigned`, `needs-you`, `done`, then `accepted` or `dropped` after your review.

- `add_task` with a title and notes, or with just a Jira key when Jira is configured: the summary and description
  are filled in from Jira.
- `assign` sends the task to an agent with instructions to report back. The agent runs
  `agent-task done "<what it did, and the branch>"` or `agent-task needs-you "<what it needs>"` at the end, and the
  board picks that up.
- Review a `done` task with `transcript` and `diff`, then `update_task` to `accepted`, or `send` a follow-up; the
  agent's next report updates the same task.

### Queue mode and the watcher

The watcher (`agents.sh watcher start`; also started at boot) checks every 15 seconds. It records events in
`~/agents/events.jsonl`: a task done or needing you, an agent asking for permission, an agent that ended. It sends
notifications for the ones that need you, and it runs queue mode: an agent in queue mode
(`agents.sh queue agent-3 on`, or the `queue` tool) gets the oldest `todo` task whenever it's free. Queued tasks
tell the agent to start from a fresh default branch, push its work on a branch and name it in its report. Keep the
agents you talk to yourself out of queue mode.

Notifications reach you two ways:

- `windows/agents-notify.cmd` shows a Windows toast for each one (`agents-start.cmd` opens it in a tab).
- `notify.sh` on the VM, if you create it from `notify.sh.example`: the watcher runs it for each one, e.g. to post
  to ntfy.sh for your phone.

## Checking things by hand

- `mcp/check` on the VM calls the MCP server like Claude Code does: `mcp/check agents`,
  `mcp/check tasks '{"all": true}'`.
- `mcp/events 20` shows the last events; `~/agents/watch.log` and `~/agents/autostart.log` are the logs.
