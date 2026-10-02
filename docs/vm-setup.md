# VM setup

The agents run on an Ubuntu Server 24.04 VM with Docker. This page covers creating the VM with Hyper-V on Windows
(the tested setup), the one-time setup on the VM, and your first agent.

## Sizing

What the agents use depends on your project. As a reference, five agents on a large .NET solution:

| | Memory | Disk |
| --- | --- | --- |
| An idle agent | 0.2 to 1 GB | its workspace: one clone of your repo, plus build output |
| An agent building and running tests | 2 to 5 GB | |
| A connected Rider backend, while indexing | about 7 GB | about 1 GB per agent, plus about 7 GB shared per Rider version |
| Images and caches | | about 2 GB for the agent image, plus your package cache (4 GB of NuGet packages here) |

32 GB of memory, 20 virtual CPUs and a 250 GB disk ran five agents with one or two Rider backends comfortably.
Builds in the VM were about twice as fast as on the Windows host (no antivirus scanning, a faster file system);
CPU-bound test runs took the same time.

## Hyper-V on Windows

Hyper-V comes with Windows 10/11 Pro, Enterprise and Education. If it isn't on yet, enable it in an admin
PowerShell and restart:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
```

1. Download the Ubuntu Server 24.04 LTS ISO from ubuntu.com.
2. In Hyper-V Manager: **New → Virtual Machine**.
   - **Generation 2.**
   - **Memory:** see Sizing; 16 GB is a workable minimum for a couple of agents.
   - **Network:** *Default Switch*. It gives the VM internet access through NAT, and Windows resolves the VM's
     host name as `<hostname>.mshome.net`, so you don't need its IP address, which changes after restarts.
   - **Disk:** a new virtual hard disk, 250 GB (it grows as used), on a fast drive.
   - **Installation:** the Ubuntu ISO.
3. Before starting it, open the VM's **Settings**:
   - **Security:** turn Secure Boot off, or set its template to *Microsoft UEFI Certificate Authority*.
     Ubuntu doesn't boot with the default Windows template.
   - **Processor:** as many virtual processors as you can spare.
   - **Automatic Start Action:** *Always start this virtual machine automatically*, so it comes up with Windows.
     Or in an admin PowerShell: `Set-VM -Name <vm> -AutomaticStartAction Start`.
4. Start the VM and install Ubuntu Server:
   - Pick a host name you'll type often; this guide uses `agents`, giving `agents.mshome.net`.
   - Create your user. The agents' files on the VM belong to the first user (UID 1000), so use that one.
   - Tick **Install OpenSSH server**. No snaps are needed.
5. After the install, reboot, log in once at the console, and check the network: `ping -c1 ubuntu.com`.
6. Take a checkpoint of the clean install (Hyper-V Manager → Checkpoint). Checkpoints are your undo for the
   whole VM; take one before big changes.

Then set up SSH from Windows ([ssh-setup.md](ssh-setup.md)); everything after this works over SSH.

### Other hosts

Any machine or VM with Ubuntu Server 24.04, a user with sudo and SSH should work the same: VMware, VirtualBox,
UTM on a Mac, Proxmox, a cloud VM or a spare PC. That hasn't been tested. Without Hyper-V's Default Switch, use the
VM's IP address or your network's DNS name in the SSH config. On a cloud VM, keep port 22 restricted to your own
IP address.

## One-time setup on the VM

From your machine, once `ssh agents` works:

```bash
ssh agents "git clone https://github.com/sovist/claude-agents-vm.git ~/claude-agents-vm"
```

Clone your own fork instead if you keep one (docs/project-hooks.md). Then:

```bash
ssh -t agents "sudo ~/claude-agents-vm/vm/setup.sh"
```

It installs Docker Engine and the compose plugin, configures Docker's networks and log limits, adds you to the
docker group, installs `uv` (runs the MCP server), sets the time zone from `config.env` if set, and turns on the
boot autostart. It's safe to run again; finished steps are skipped. Being in the docker group is equivalent to
root on the VM, which is why the VM itself is the security boundary ([security.md](security.md)).

## Configure

On the VM, in `~/claude-agents-vm`:

1. `cp config.example.env config.env` and fill it in: your repository, its git host and token user name, the
   name and email the agents commit with, optionally Jira, your time zone.
2. Describe your stack in `project/` ([project-hooks.md](project-hooks.md)): at least a base image or an
   `image.sh` with your build tools, and a `CLAUDE.md` saying how to build and test.
3. Save the tokens, each at its own prompt (nothing is shown or stored in shell history):

   ```bash
   ssh -t agents ~/claude-agents-vm/save-secret.sh git-token
   ```

   ```bash
   ssh -t agents ~/claude-agents-vm/save-secret.sh claude-oauth-token
   ```

   - `git-token`: for your git host. Prefer a fine-grained token limited to the one repository, with read and
     write access to contents (and pull requests, if agents should open them).
   - `claude-oauth-token`: run `claude setup-token` on your own machine and paste what it prints. It's a
     long-lived token that only allows inference; usage counts against your Claude plan.
   - For Jira and Confluence: `jira-token`, `confluence-token` (Atlassian API tokens with read-only scopes) and
     `atlassian-email`.
4. Allow the hosts your agents need beyond Claude: your git host and package registries, e.g.

   ```bash
   ssh agents "~/claude-agents-vm/allow.sh github.com api.nuget.org '*.nuget.org'"
   ```

   Later, `./allow.sh --refused` lists what the proxy refused, to spot a missing host.

## First agent

```bash
ssh agents "~/claude-agents-vm/agents.sh build"
```

```bash
ssh agents "~/claude-agents-vm/agents.sh start agent-1"
```

The first start clones your repository into `~/agents/agent-1`. Watch it with `agents.sh list` until its state is
`ready`, then attach to it:

```bash
ssh -t agents "~/claude-agents-vm/agents.sh attach agent-1"
```

Detach with Ctrl+B, then D; the agent keeps running. Start the watcher, which sends notifications and runs queue
mode:

```bash
ssh agents "~/claude-agents-vm/agents.sh watcher start"
```

Next: [usage.md](usage.md), to drive the agents from your own Claude Code.
