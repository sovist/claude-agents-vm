# CLAUDE.md

Guidance for Claude Code when helping someone set up or change claude-agents-vm. Read README.md for what it is.

## Who does what

The person you're helping owns the machines and the accounts. Some steps only they can do; prepare those
(the exact command, the exact setting) and check the result afterwards, but don't try to do them yourself:

- **Hypervisor and VM creation**: needs admin rights on their machine, often a GUI (Hyper-V Manager).
- **`sudo` on the VM**: give them the command, to run as `ssh -t <host> "sudo …"`. You don't have their password.
- **Tokens**: they create them (GitHub, Bitbucket, GitLab, Atlassian, `claude setup-token`) and save each one on the
  VM with `ssh -t <host> <repo>/save-secret.sh <name>`, which prompts without echoing. Never ask for a token in chat,
  never read files in `secrets/`, never print a token, and never put one on a command line.
- **Accounts and keys**: creating repositories, adding deploy keys, approving access.
- **Their own SSH config** (`~/.ssh/config` on their machine): propose the lines; let them paste them.

Everything else you can do over SSH as their user on the VM (in the docker group, no sudo): run the scripts,
read logs, edit files in the repo folder, build images, start agents.

## Setup checklist

Work through it in order. Before each step, check whether it's already done (the check is listed), and skip it if so.

0. **A clone of this repo on their machine.** Check: you're in it (README.md, CLAUDE.md and agents.sh are here).
   Do: ask where to put it, `git clone https://github.com/sovist/claude-agents-vm.git`, and read CLAUDE.md from the
   clone. It holds the docs you'll follow; the VM gets its own clone in step 3. Mention that `/setup` works in a new
   Claude Code session started in that folder.
1. **VM exists and boots.** Check: none possible until SSH works. Guide: docs/vm-setup.md (Hyper-V in detail).
2. **SSH from their machine to the VM.** Check: `ssh -o BatchMode=yes <host> true`. Guide: docs/ssh-setup.md.
3. **Repo on the VM.** Check: `ssh <host> test -x ~/claude-agents-vm/agents.sh`. Do: `git clone` it there (or their fork).
4. **One-time VM setup.** Check: `ssh <host> 'docker info >/dev/null && ~/.local/bin/uv --version'`.
   They run: `ssh -t <host> "sudo ~/claude-agents-vm/vm/setup.sh"`, then log out of any VM session so the docker
   group applies (new SSH connections get it).
5. **config.env.** Check: the file exists with REPO_URL, GIT_HOST, GIT_HOST_USERNAME, AGENT_GIT_NAME, AGENT_GIT_EMAIL.
   Do: copy config.example.env and fill it in with them. The git user name depends on the host (see the comments).
6. **Project hooks.** Ask about their stack (language, build tool, package registries) and write `project/` files
   (docs/project-hooks.md): base image or `image.sh`, `CLAUDE.md` with how to build and test, `allowed-domains.txt`.
7. **Tokens.** Check: `ssh <host> ls ~/claude-agents-vm/secrets` (names only). Needed: `git-token`,
   `claude-oauth-token`; for Jira: `jira-token`, `confluence-token`, `atlassian-email`. They save each one (above).
   Recommend a fine-grained git token limited to the one repository, with contents and pull-request access.
8. **Allowed hosts.** The git host at least (`./allow.sh github.com`), plus the package registries from step 6.
9. **Build and first agent.** `./agents.sh build`, then `./agents.sh start agent-1`; check with `./agents.sh list`
   that it reaches `ready`. The first start clones the repository, which can take minutes.
10. **Their machine.** Register the MCP server (docs/usage.md), start the watcher on the VM
    (`./agents.sh watcher start`), and on Windows copy `windows/` somewhere handy. Then try it: `agents()`,
    `send()` a small read-only request to agent-1, `wait()` for the reply.

Optional afterwards: Rider (docs/rider.md), notifications (`notify.sh.example`), more agents, queue mode.

## Rules when changing things

- **The VM copy is the live one.** Before overwriting a file on the VM, check `git status` / `git diff` there:
  they may have edited it. Prefer committing on the VM to copying files around.
- **Agent workspaces belong to the agents.** Never run `git`, build tools or tests on the VM side inside
  `~/agents/<name>`: the agent controls that repo's config and hooks, and a build runs its code. Use
  `docker exec <name> …` (as `agents.sh` does).
- **Files an agent can write** (`~/agents/.status/<name>/`, its workspace) may be symlinks it planted, since the
  agent has the same UID as the VM user. Code on the VM side reads them without following symlinks
  (`plain_file` in agents.sh, `read_agent_file` in mcp/serve.py). Keep it that way in anything new.
- **Don't restart a working agent.** `agents.sh list` shows the state; `stop-all` skips working agents for this
  reason. A new image only reaches an agent when it restarts; ask before restarting one.
- **Project-specific changes go into `config.env` or `project/`**, never into the other files, so forks keep
  merging cleanly (docs/project-hooks.md).
- Shell scripts and configs use LF line endings; `windows/` files use CRLF (`.gitattributes` enforces it).
- Every script explains itself in its header comment; keep those comments current when behavior changes.
