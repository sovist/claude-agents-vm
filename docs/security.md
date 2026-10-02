# Security model

Agents run code: whatever the model decides, plus your repository's build scripts, tests and packages. With
`--unattended` they do it without asking. The design assumes an agent may misbehave, through a bad decision, a
prompt injection in something it read, or a malicious package, and limits what that can reach.

## The boundaries

| Layer | What it does |
| --- | --- |
| **The VM** | The outer boundary. Agents never run on your machine; your files, browser sessions and credentials aren't on the VM. |
| **The container** | A non-root user, all Linux capabilities dropped, `no-new-privileges`, no sudo. The agent sees its own workspace, its Claude state and the tokens in `secrets/`, read-only. |
| **The network** | Agents sit on a Docker network marked `internal`: no route out. The only way out is the proxy, which allows HTTPS (port 443) to the hosts on the allow-list and refuses everything else. |
| **Tokens** | Scoped by you: a git token limited to one repository; read-only Atlassian tokens; a Claude token that only allows inference. |
| **Commit identity** | Set by environment variables, so an agent can't commit under another name by changing git config. |

## What it doesn't protect against

- **Anything the allowed hosts allow.** The proxy filters by host name, not content. An agent can push to any branch
  your git token permits, open pull requests, or post data to any allowed host, including through features of that
  host you didn't think about (a gist, an issue comment). Keep the allow-list short, the tokens narrow, and protect
  your important branches on the git host.
- **DNS.** Whether Docker's embedded DNS answers external names from the internal network hasn't been verified
  here; treat DNS lookups as a possible side channel.
- **Your review.** An agent's branch is untrusted code until you've read it. Its replies, transcripts and task
  reports are its own words, not verified facts.

## Accepted risks

- **The docker group is root on the VM.** Your VM user can control Docker, which is equivalent to root there. That's
  why the VM, not the user account, is the boundary: don't keep anything on the VM you couldn't lose.
- **Shared UID on bind mounts.** The agent user has UID 1000, like your VM user, so files it writes in its
  workspace and status folder belong to you on the VM side. An agent could plant a symlink there pointing at your
  files. The VM-side scripts read agent-written files without following symlinks; anything new must do the same.
  Never run git, builds or tests on the VM side inside an agent's workspace: the repo's hooks and build files are
  the agent's to change.
- **Rider's backend runs inside the agent's container**, so the agent can modify the backend your Rider client
  talks to. It's the price of watching live; skip Rider if that's not acceptable to you.
- **The Claude token is visible to the agent** (it's in the container's environment). It only allows inference, but
  usage counts against your plan, and it lasts until you revoke it.
- **Shared volumes.** Each agent has its own workspace and volumes, but the Rider backend volume (`jetbrains-dist`)
  is shared and writable by every agent, and so is any cache you share in `project/compose.yaml` (a package cache):
  one agent could tamper with what another runs. And all agents share one Docker host: a container escape would
  reach the others and the VM.

## Practices

- Prefer normal mode while you're learning how the agents behave; use `--unattended` for well-scoped tasks.
- Look at `./allow.sh --refused` now and then: what the agents tried to reach tells you what they're up to.
- Keep a VM checkpoint from before big changes; rotate tokens if you suspect anything.
