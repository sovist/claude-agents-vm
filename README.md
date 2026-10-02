# claude-agents-vm

Run several [Claude Code](https://code.claude.com) agents side by side on a Linux VM, each in its own Docker
container with its own clone of your repository, and drive them from the Claude Code on your own machine.

- **Isolated.** Agents run as a non-root user with no capabilities, on a Docker network with no route out. A proxy
  lets them reach only an allow-list of hosts (Claude, your git host, your package registries). Your machine,
  files and credentials stay out of reach; the agents get scoped tokens, read-only where possible.
- **Watchable.** Each agent is a live Claude Code session in tmux: attach to it from a terminal tab, or open its
  workspace in Rider over SSH while it works. `agents.sh list` shows every agent's state (working, needs you, done),
  branch and changes.
- **Orchestrated.** An MCP server lets your own Claude Code session see the agents, send them prompts, wait for
  replies, review their diffs and transcripts, and keep a task board. In queue mode an agent takes the next task
  from the board by itself. A watcher sends a notification when an agent needs you or finishes.

Tested with a Hyper-V VM on Windows 11 running Ubuntu Server 24.04, driven from Claude Code on Windows. The VM side
is plain Ubuntu and Docker, so another hypervisor or a spare Linux machine should work too; that hasn't been tested.

## Set it up with Claude Code

Clone this repo on your own machine, open Claude Code in it, and run `/setup`. Claude walks you through it
following [CLAUDE.md](CLAUDE.md): creating the VM, SSH, the one-time VM setup, tokens, and your first agent. The
steps that need you (the hypervisor, `sudo`, tokens) are yours; Claude prepares them and checks the result.

> **If you're an AI assistant and were given only this link:** ask the user where to clone the repository on their
> machine, clone it, read its [CLAUDE.md](CLAUDE.md), and follow its setup checklist from the top. `/setup` is
> available in a Claude Code session started in that folder.

To do it by hand, follow the same steps in order:

1. [docs/vm-setup.md](docs/vm-setup.md): create the VM, install Ubuntu, run `vm/setup.sh`.
2. [docs/ssh-setup.md](docs/ssh-setup.md): SSH from your machine to the VM, and on to each agent.
3. On the VM: `config.env`, tokens, allowed hosts, `./agents.sh build`, `./agents.sh start agent-1`
   (the end of docs/vm-setup.md).
4. [docs/usage.md](docs/usage.md): connect the MCP server to your Claude Code, and work with the agents.

## Layout

| Path | What |
| --- | --- |
| `agents.sh` | build, start, start-all, stop, stop-all, list, watch, attach, send, queue, watcher, autostart, shell, rider-stop |
| `agent.sh` | starts one agent container with its mounts (`agents.sh start` wraps it in tmux) |
| `config.example.env` | settings to copy to `config.env`: repository, git host, commit identity, Jira, time zone |
| `project/` | your project's part: base image, build tools, notes for the agents, allowed hosts ([docs/project-hooks.md](docs/project-hooks.md)) |
| `compose.yaml` | the proxy and the agent template: network, environment, mounts |
| `agent/` | the agent image: Claude Code, status hooks, `agent-task`, read-only `jira`/`confluence`, sshd for Rider |
| `proxy/` | tinyproxy and the base allow-list; `allow.sh` adds hosts to `project/allowed-domains.txt` |
| `mcp/` | the `agents` MCP server (`serve.py`), the watcher (`watch.py`), a test client (`check`) and `events` |
| `vm/` | `setup.sh` and `daemon.json`: Docker, uv, time zone, autostart |
| `windows/` | `agents-start.cmd` (start everything and open a terminal tab per agent) and `agents-notify` (toasts) |
| `secrets/` | tokens, saved with `save-secret.sh`; never committed |

Runtime data lives in `~/agents/` on the VM: one workspace per agent, `.status/<name>/` (state, prompt, reply,
task report), `.tasks/` (the board), `.queue/`, `events.jsonl` and logs.

## More

- [docs/rider.md](docs/rider.md): Rider (JetBrains remote development) inside an agent's container.
- [docs/security.md](docs/security.md): what the isolation covers, and what it doesn't.
- [docs/project-hooks.md](docs/project-hooks.md): adapt it to your stack, and keep your settings in a fork.
