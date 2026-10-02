# SSH setup

Everything goes over SSH from your machine: your terminal tabs, the MCP server, notifications, and Rider's
connection into an agent's container. This page sets up a key, the host alias `agents` for the VM, and the
`agent-*` aliases that reach the agents' containers through the VM.

## 1. A key on your machine

If you don't have one yet (`~/.ssh/id_ed25519` on macOS and Linux, `%USERPROFILE%\.ssh\id_ed25519` on Windows):

```bash
ssh-keygen -t ed25519
```

Windows 10/11 include OpenSSH (`ssh`, `ssh-keygen`), usable from PowerShell, cmd or Windows Terminal.

The scripts connect without prompting (`BatchMode`), so the key either has no passphrase or is loaded in an SSH
agent. On Windows, the OpenSSH Authentication Agent service is disabled by default; to use a passphrase there,
enable it in an admin PowerShell and add the key:

```powershell
Get-Service ssh-agent | Set-Service -StartupType Automatic -PassThru | Start-Service
```

```powershell
ssh-add $env:USERPROFILE\.ssh\id_ed25519
```

## 2. Your key on the VM

Copy the public key into `~/.ssh/authorized_keys` on the VM; you'll type your VM password once. Replace `you`
with your VM user and `agents.mshome.net` with the VM's name or IP address.

On Windows (PowerShell), which has no `ssh-copy-id`:

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh you@agents.mshome.net "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

On macOS or Linux:

```bash
ssh-copy-id you@agents.mshome.net
```

The same `authorized_keys` decides who may connect into the agents' containers (for Rider): `agent.sh` copies it
into each agent's SSH folder when the agent starts.

## 3. Host aliases

Add to `~/.ssh/config` on your machine (`%USERPROFILE%\.ssh\config` on Windows; create it if missing):

```
Host agents
    HostName agents.mshome.net
    User you

Host agent-*
    User agent
    ProxyCommand ssh agents docker exec -i %h agent-sshd
```

- `agents` is the VM. The scripts, the Windows launchers and the MCP registration use this alias; if you pick
  another, set `AGENTS_VM_HOST` on Windows (see `windows/`) and use it in the MCP registration.
- `agent-*` reaches an agent's own container: `ssh agent-1` runs `agent-sshd` inside the container `agent-1` through
  the VM, so nothing listens on a port. You need it for Rider ([rider.md](rider.md)), and it is handy to look around
  as the agent sees things. Each agent has its own host key, so the first connection to each asks you to confirm it.

Check both:

```bash
ssh agents "hostname && docker --version"
```

```bash
ssh agent-1 "whoami && ls /workspace"
```

The second one needs a running agent-1; it prints `agent` and the top of its clone.

## 4. Pushing your fork from the VM (optional)

If you keep your settings in a fork and commit on the VM, give the VM its own key for that one repository rather
than your personal key. On the VM:

```bash
ssh-keygen -t ed25519 -N '' -C "agents VM deploy key" -f ~/.ssh/id_ed25519_github
```

```bash
printf 'Host github.com\n    IdentityFile ~/.ssh/id_ed25519_github\n    IdentitiesOnly yes\n' >> ~/.ssh/config
```

Add the public key (`cat ~/.ssh/id_ed25519_github.pub`) to the repository on GitHub under Settings → Deploy keys,
with write access. A deploy key works for one repository only. To push to a second repository, such as upstream
and a fork, create a second key and give each a host alias (`Host github-fork` with `HostName github.com`), then use
that alias in the remote URL. Agents can't use these keys: they live in your account on the VM, outside every
container.
