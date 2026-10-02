#!/usr/bin/env bash
# Starts an interactive agent container with its own workspace and Claude Code state.
# Usage: ./agent.sh <name> [command...]      e.g. ./agent.sh agent-1
#   workspace:  ~/agents/<name>  ->  /workspace
#   claude:     docker volume <name>-claude  ->  /home/agent/.claude
#   status:     ~/agents/.status/<name>  ->  /run/agent-status (written by Claude Code hooks)
#   ssh:        ~/agents/.ssh/<name> (host key, your authorized_keys)  ->  /run/agent-ssh, read-only
#   rider:      docker volume <name>-jetbrains  ->  /home/agent/.jetbrains, plus the shared
#               jetbrains-dist volume for Rider's backend binaries
#   secrets:    secrets/  ->  /run/secrets, read-only
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh

name="${1:?Usage: $0 <agent-name> [command...]}"
shift
workspace="$HOME/agents/$name"
status_dir="$HOME/agents/.status/$name"
mkdir -p "$workspace" "$status_dir"
printf 'starting\t%s\t\n' "$(date +%s)" > "$status_dir/state"

# SSH into the container for Rider (see agent-sshd in the image): a host key per agent, generated once,
# and the keys allowed to log in, which are the same as for your own account on the VM.
ssh_dir="$HOME/agents/.ssh/$name"
mkdir -p "$ssh_dir"
chmod 700 "$HOME/agents/.ssh" "$ssh_dir"
[ -f "$ssh_dir/ssh_host_ed25519_key" ] || ssh-keygen -q -t ed25519 -N '' -C "$name" -f "$ssh_dir/ssh_host_ed25519_key"
install -m 600 "$HOME/.ssh/authorized_keys" "$ssh_dir/authorized_keys"
# Explicit modes, no inherited ACLs: a default ACL on a parent folder overrides the umask ssh-keygen
# relies on, and sshd refuses a host key that others can read.
setfacl -R -b "$ssh_dir" 2>/dev/null || true
chmod 600 "$ssh_dir/ssh_host_ed25519_key"

compose up -d proxy

# secrets/ holds git-token, claude-oauth-token and, when Jira is used, jira-token, confluence-token and
# atlassian-email; the whole folder is mounted read-only at /run/secrets.
secret_args=(-v "$PWD/secrets:/run/secrets:ro")
if [ -f secrets/claude-oauth-token ]; then
    # Passed by name, so the token never appears in the process list.
    export CLAUDE_CODE_OAUTH_TOKEN="$(tr -d '\r\n' < secrets/claude-oauth-token)"
    secret_args+=(-e CLAUDE_CODE_OAUTH_TOKEN)
fi

exec "${compose_cmd[@]}" run --rm --name "$name" \
    -v "$workspace:/workspace" \
    -v "$name-claude:/home/agent/.claude" \
    -v "$status_dir:/run/agent-status" \
    -v "$ssh_dir:/run/agent-ssh:ro" \
    -v "$name-jetbrains:/home/agent/.jetbrains" \
    -v "jetbrains-dist:/home/agent/.jetbrains/cache/RemoteDev/dist" \
    -e AGENT_RIDER_WATCHDOG=1 \
    "${secret_args[@]}" \
    agent "$@"
