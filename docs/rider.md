# Rider inside an agent

JetBrains Remote Development runs the IDE's backend next to the code, inside an agent's container, and your
Rider on your machine is a thin client to it. You see the agent's workspace live while it works: its changes,
its branch, its build output, with full code navigation. Tested with Rider 2026.2; other JetBrains IDEs use the
same remote development mechanism and should work the same way, untested.

## Connect

1. Set up the `agent-*` SSH alias first ([ssh-setup.md](ssh-setup.md)), and check `ssh agent-1 whoami` prints `agent`.
2. In Rider's welcome screen: **Remote Development → SSH → New Connection**.
   - Choose **OpenSSH config and authentication agent**, so Rider uses your `~/.ssh/config` and its `ProxyCommand`.
   - Host `agent-1`, user `agent`, port 22 (no port is opened; the connection goes through `docker exec` on the VM).
3. Pick the IDE version: **the same version as your local Rider**. A mismatched backend fails with errors such as
   "Unknown command: host-status".
4. Project: your solution or folder under `/workspace`, the agent's clone.

The first connection downloads the backend into a volume shared by all agents (about 7 GB per version), so later
agents connect quickly. Each agent keeps its own Rider caches and settings in its `<name>-jetbrains` volume. While it
indexes a large solution, a backend needs about 7 GB of memory.

## The backend stops when you're gone

A backend keeps its memory after you close the window. `rider-idle-stop` in each container stops it once no Rider
window has been connected for `RIDER_IDLE_MINUTES` (config.env, default 10); connecting again starts it. The
`RIDER` column of `agents.sh list` shows `connected`, `idle 4m` or `off`. To stop one right away:
`agents.sh rider-stop agent-1`. In Rider, *Stop IDE Backend* is only in the welcome screen's Remote Development list.

## Troubleshooting

- **Empty Explorer after the first connect:** Backend Status Details → **Save and restart**.
- **Rider asks to trust the host key:** expected on each agent's first connection; each agent has its own key.
- **Different JDK, SDK or environment in Rider's terminal than in the agent:** the SSH session gets the container's
  environment from `agent-sshd`, except `HOME`, `PATH` and the Claude token. Rebuild and restart the agent after
  changing `project/compose.yaml`.

## Before you rely on it

The backend runs inside the agent's container, where the agent can modify it, and your Rider client talks to that
backend. It's a trade-off for watching live; see [security.md](security.md). Never connect Rider to an agent's
workspace as your own VM user instead: opening the project runs git and build tooling, which run code the agent
controls, with your account's rights.
