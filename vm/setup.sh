#!/usr/bin/env bash
# One-time setup of the agents VM from a plain Ubuntu Server 24.04: everything outside this repo folder that
# the agents need. Run from your own account with sudo, from anywhere, e.g.:
#     sudo ~/claude-agents-vm/vm/setup.sh
# Safe to run again after a change: every step is skipped when it is already done. See docs/vm-setup.md.
#
#   1. packages: git, curl, tmux, jq, acl (setfacl, used by agent.sh)
#   2. Docker Engine and the compose plugin, from Docker's apt repository
#   3. /etc/docker/daemon.json from vm/daemon.json: container networks that don't collide with yours, log caps
#   4. your user in the docker group (a new login is needed before docker works without sudo)
#   5. uv for your user, in ~/.local/bin: runs the MCP server and the watcher without system Python packages
#   6. the time zone, when TZ_NAME is set in config.env, so the task board and the events show your clock
#   7. ~/agents and the boot autostart (agents.sh autostart on)
#
# Not done here: the tokens (save-secret.sh), the images (agents.sh build), the agents' clones
# (agents.sh start makes them), Rider's backend (Rider installs it on first connect), a desktop or xrdp.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run it with sudo: sudo $0" >&2; exit 1; }
user="${SUDO_USER:-}"
if [ -z "$user" ] || [ "$user" = root ]; then
    echo "Run it with sudo from your own account, not as root: the agents belong to that account." >&2
    exit 1
fi
home="$(getent passwd "$user" | cut -d: -f6)"
root="$(cd "$(dirname "$0")/.." && pwd)"
TZ_NAME=
# shellcheck source=/dev/null
[ -f "$root/config.env" ] && TZ_NAME="$(set -a; . "$root/config.env"; printf '%s' "${TZ_NAME:-}")"
as_user() { sudo -u "$user" -H "$@"; }
step() { printf '\n== %s\n' "$*"; }

step "Packages"
apt-get update -q
apt-get install -y -q --no-install-recommends git curl ca-certificates gnupg tmux jq acl

step "Docker Engine"
if command -v docker >/dev/null; then
    echo "already installed: $(docker --version)"
else
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    echo "installed $(docker --version)"
fi
systemctl enable --now docker >/dev/null

step "Docker daemon configuration"
if cmp -s "$root/vm/daemon.json" /etc/docker/daemon.json; then
    echo "already in place"
else
    install -m 0644 "$root/vm/daemon.json" /etc/docker/daemon.json
    # Restarting Docker stops every running container, agents included.
    if [ -n "$(docker ps -q 2>/dev/null)" ]; then
        echo "installed; containers are running, so Docker was NOT restarted. When no agent is working:  sudo systemctl restart docker"
    else
        systemctl restart docker
        echo "installed and Docker restarted"
    fi
fi

step "Docker group"
if id -nG "$user" | grep -qw docker; then
    echo "$user is already in the docker group"
else
    usermod -aG docker "$user"
    echo "$user added to the docker group: log out and in again before using docker"
fi

step "uv for $user"
if [ -x "$home/.local/bin/uv" ]; then
    echo "already installed: $("$home/.local/bin/uv" --version)"
else
    as_user sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null
    echo "installed $("$home/.local/bin/uv" --version)"
fi

step "Time zone"
if [ -z "$TZ_NAME" ]; then
    echo "TZ_NAME isn't set in config.env; keeping $(timedatectl show -p Timezone --value)"
elif [ "$(timedatectl show -p Timezone --value)" = "$TZ_NAME" ]; then
    echo "already $TZ_NAME"
else
    timedatectl set-timezone "$TZ_NAME"
    echo "set to $TZ_NAME (restart the watcher so it uses it: agents.sh watcher stop, then start)"
fi

step "Agents folder and autostart"
as_user mkdir -p "$home/agents"
as_user "$root/agents.sh" autostart on

step "Disk"
# Ubuntu Server's default storage layout gives / only half of the LVM volume group.
vg_free="$(vgs --noheadings --units g --nosuffix -o vg_free 2>/dev/null | awk '{ s += $1 } END { printf "%d", s }')"
if [ "${vg_free:-0}" -ge 5 ]; then
    echo "$vg_free GB of the disk isn't used by / yet (the installer's default layout). To give it to /:"
    echo "    sudo lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv"
else
    echo "/ has $(df -h / | awk 'NR == 2 { print $2 }')"
fi

step "Done"
echo "Next, as $user (after logging in again if the docker group was just added): config.env, save-secret.sh"
echo "for each token, allow.sh for the project's hosts, agents.sh build, agents.sh start agent-1. See docs/vm-setup.md."
