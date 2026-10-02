# Project hooks

Everything specific to your project lives in two places that this repo never changes:

- `config.env`: who and where (repository, git host, commit identity, Jira site, time zone). Start from
  `config.example.env`.
- `project/`: the stack (base image, build tools, environment, extra mounts, notes for the agents, allowed hosts,
  extra commands).

So you can keep your settings in a fork, private or public, and merge updates from upstream without conflicts.
Every file in `project/` is optional.

| File | Used by | Effect |
| --- | --- | --- |
| `project/compose.yaml` | every `docker compose` call the scripts make | Merged over `compose.yaml`. Set the base image with `services.agent.build.args.AGENT_BASE_IMAGE`, and add environment variables, volumes (a package cache) or resource limits. Paths are relative to the repo root. |
| `project/image.sh` | `agents.sh build` | Runs as root at the end of the agent image build, with `USERNAME` set to the agent user and `bash -euo pipefail`. Install packages and tools here. |
| `project/CLAUDE.md` | `agents.sh build` | Appended to the agents' environment notes (`/etc/claude-code/CLAUDE.md`): how to build and test here, conventions, known limitations. |
| `project/allowed-domains.txt` | the proxy | Hosts the agents may reach on top of the base list (`proxy/allowed-domains.txt`, Claude Code's own). `allow.sh <host>` adds to this file. |
| `project/bin/` | agents | On the agents' `PATH`, mounted live at `/opt/project/bin`: no rebuild needed. |
| anything else | agents | Visible read-only at `/opt/project/`, live. Good for data such as a test baseline. |

After changing `compose.yaml`, `image.sh` or `CLAUDE.md`, run `./agents.sh build`, then restart agents to give them
the new image (`./agents.sh stop <name>`, then `./agents.sh start <name> --continue`). `allowed-domains.txt` only
needs `./allow.sh --reload`.

## Example: a .NET project

`project/compose.yaml`:

```yaml
volumes:
  nuget-cache: {}

services:
  agent:
    build:
      args:
        AGENT_BASE_IMAGE: mcr.microsoft.com/dotnet/sdk:10.0-noble
    environment:
      DOTNET_CLI_TELEMETRY_OPTOUT: "1"
      DOTNET_NOLOGO: "1"
    volumes:
      - nuget-cache:/home/agent/.nuget/packages
```

`project/image.sh`, so the shared cache volume starts out owned by the agent user:

```bash
mkdir -p "/home/$USERNAME/.nuget/packages"
chown -R "$USERNAME:$USERNAME" "/home/$USERNAME/.nuget"
```

`project/allowed-domains.txt`:

```
api.nuget.org
*.nuget.org
github.com
# NuGet checks the certificates of signed packages online
ocsp.digicert.com
crl3.digicert.com
crl4.digicert.com
```

To find hosts you missed, `./allow.sh --refused` lists what the proxy refused, most frequent first.

The base image must be Ubuntu-based (Debian package names, an `ubuntu` user with UID 1000 that the build
replaces). Official images built on Ubuntu 24.04 ("noble") fit, such as `mcr.microsoft.com/dotnet/sdk:10.0-noble`.
For Node.js, Python or Go, either pick such an image or install the toolchain in `project/image.sh`.

## Keeping a fork

GitHub doesn't allow a private fork of a public repo, so a private fork is a separate repository with this one
as a second remote:

```bash
git remote add upstream https://github.com/sovist/claude-agents-vm.git
git fetch upstream
git merge upstream/main
```

Commit only `config.env` and `project/` in the fork. Send a change to any other file upstream first, then merge it.
