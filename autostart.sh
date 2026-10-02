#!/usr/bin/env bash
# Run by cron at boot (turn on and off with "agents.sh autostart on|off"): waits for Docker, then starts
# every agent with "agents.sh start-all", which resumes each one's last conversation in the same mode,
# and the watcher (events, notifications, queue mode).
# Log: ~/agents/autostart.log
set -uo pipefail
cd "$(dirname "$0")"

mkdir -p "$HOME/agents"
exec >> "$HOME/agents/autostart.log" 2>&1
echo "=== $(date '+%F %T') autostart"

for _ in $(seq 1 60); do
    docker info >/dev/null 2>&1 && break
    sleep 5
done
if ! docker info >/dev/null 2>&1; then
    echo "Docker still isn't ready after 5 minutes; no agents started."
    exit 1
fi

./agents.sh start-all
./agents.sh watcher start
