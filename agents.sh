#!/usr/bin/env bash
# Starts, lists, attaches to and stops agents. Each agent is Claude Code in its own container,
# running inside a tmux session of the same name, so it keeps going after you disconnect.
#
#   ./agents.sh build                builds the proxy and agent images (after a change to agent/, proxy/ or project/)
#   ./agents.sh start <name> [--unattended] [--continue] [-- <first prompt>]
#   ./agents.sh start-all [--unattended]   every agent with a workspace that isn't running; "start all" too
#   ./agents.sh stop-all [--force]   every running agent; one that is working is skipped without --force
#   ./agents.sh autostart [on|off]   start all agents when the VM boots; without on/off, shows the setting
#   ./agents.sh list                 state (working, needs-you, done...), branch and changes per agent
#   ./agents.sh watch [seconds]      the same list, refreshed every 2 seconds by default (Ctrl+C to quit)
#   ./agents.sh attach <name>        detach again with Ctrl+B, then D
#   ./agents.sh send <name> [--force] [<prompt>]   gives a running agent a prompt (from stdin without one); only when it's ready or done
#   ./agents.sh queue [<name> on|off]   queue mode: the watcher feeds the agent todo tasks from the board; alone, shows who is in it
#   ./agents.sh watcher start|stop|status   the watcher: events, notifications and queue mode (also started at boot)
#   ./agents.sh shell <name>         a bash shell in the running agent's container
#   ./agents.sh stop <name>          ends Claude Code and the container; the workspace is kept
#   ./agents.sh rider-stop <name>    stops the agent's Rider backend (also done after 10 idle minutes)
#
# A new agent gets a fresh clone of REPO_URL (config.env) in ~/agents/<name>. --unattended starts Claude Code with
# --dangerously-skip-permissions (no permission prompts; the container is the sandbox). --continue resumes the agent's last conversation.
# start-all resumes each agent's last conversation, and starts it unattended if it last was.
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh

agents_dir="$HOME/agents"

usage() { sed -n '2,22s/^# \{0,1\}//p' "$0" >&2; exit 1; }
agent_names() {
    local ws name
    for ws in $(printf '%s\n' "$agents_dir"/*/ | sort -V); do
        name="$(basename "$ws")"
        [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]] && echo "$name"
    done
}

check_name() {
    [[ "${1:-}" =~ ^[a-z0-9][a-z0-9-]*$ ]] || { echo "Agent names use lowercase letters, digits and hyphens, e.g. agent-1." >&2; exit 1; }
}
container_running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
session_exists() { tmux has-session -t "=$1" 2>/dev/null; }
# For files an agent writes (its status files, .git/HEAD): a plain file, not a symlink. The agent has alex's
# UID, so a symlink it leaves in a mounted folder would make this script read any of alex's files.
plain_file() { [ -f "$1" ] && [ ! -L "$1" ]; }
# The watcher's pid when it runs (mcp/watch.py writes the pid file; the process must still be it).
watcher_pid() {
    local p
    p="$(cat "$agents_dir/.watch.pid" 2>/dev/null || true)"
    # An if, not a && chain: the function must succeed when the watcher is down, or set -e ends the script.
    if [[ "$p" =~ ^[0-9]+$ ]] && grep -q watch.py "/proc/$p/cmdline" 2>/dev/null; then echo "$p"; fi
}
queue_names() { { ls "$agents_dir/.queue" 2>/dev/null || true; } | sort -V | tr '\n' ' '; }

# Seconds since a unix time, as 45s / 12m / 3h05m / 2d.
ago() {
    local s=$(( $(date +%s) - $1 ))
    if   [ "$s" -lt 60 ];    then echo "${s}s"
    elif [ "$s" -lt 3600 ];  then echo "$(( s / 60 ))m"
    elif [ "$s" -lt 86400 ]; then printf '%dh%02dm\n' $(( s / 3600 )) $(( s % 3600 / 60 ))
    else echo "$(( s / 86400 ))d"; fi
}

cmd="${1:-}"
[ $# -gt 0 ] && shift
# "start all" is the same as start-all, so "all" can't be an agent name.
if [ "$cmd" = start ] && [ "${1:-}" = all ]; then
    cmd=start-all
    shift
fi

case "$cmd" in
build)
    need_config AGENT_GIT_NAME AGENT_GIT_EMAIL
    compose build proxy
    compose --profile agent build agent
    # Running agents keep the image they started with; a new image takes effect when an agent restarts.
    echo "Built. Running agents use the new image after a restart (stop, then start --continue)."
    ;;
start-all)
    extra=()
    while [ $# -gt 0 ]; do
        case "$1" in
            --unattended) extra+=(--unattended); shift ;;
            *) usage ;;
        esac
    done
    found=
    for name in $(agent_names); do
        found=1
        if session_exists "$name" || container_running "$name"; then
            echo "$name: already running"
            continue
        fi
        # Resume the last conversation when there is one; "claude --continue" without one just exits.
        resume=()
        if docker run --rm -v "$name-claude:/c:ro" --entrypoint sh agents/agent \
                -c 'ls /c/projects/-workspace/*.jsonl' >/dev/null 2>&1; then
            resume=(--continue)
        fi
        # Same mode as last time, unless --unattended was given for all.
        mode=("${extra[@]}")
        [ ${#mode[@]} -eq 0 ] && [ -f "$agents_dir/.status/$name/unattended" ] && mode=(--unattended)
        "$PWD/agents.sh" start "$name" "${mode[@]}" "${resume[@]}" >/dev/null
        echo "$name: started$([ ${#mode[@]} -gt 0 ] && echo " unattended"), $([ ${#resume[@]} -gt 0 ] && echo "resuming its last conversation" || echo "new conversation")"
    done
    [ -n "$found" ] || echo "(no agents yet; start one with: $0 start agent-1)"
    ;;
stop-all)
    force=
    while [ $# -gt 0 ]; do
        case "$1" in
            --force) force=1; shift ;;
            *) usage ;;
        esac
    done
    any=
    for name in $(agent_names); do
        session_exists "$name" || container_running "$name" || continue
        any=1
        state=
        plain_file "$agents_dir/.status/$name/state" && state="$(cut -f1 "$agents_dir/.status/$name/state")"
        if [ "$state" = working ] && [ -z "$force" ]; then
            echo "$name: working, skipped (stopping now would cut off its turn; --force stops it anyway)"
            continue
        fi
        "$PWD/agents.sh" stop "$name" >/dev/null
        echo "$name: stopped"
    done
    [ -n "$any" ] || echo "(no agents running)"
    ;;
autostart)
    # A cron @reboot entry for this user, marked so it can be found and removed again.
    marker='# agents.sh autostart'
    entry="@reboot $PWD/autostart.sh  $marker"
    # "|| true": crontab -l fails when there's no crontab yet, which under pipefail would end the script.
    others="$( { crontab -l 2>/dev/null || true; } | { grep -vF "$marker" || true; })"
    case "${1:-}" in
        on)
            printf '%s\n%s\n' "$others" "$entry" | { grep -v '^$' || true; } | crontab -
            echo "Autostart is on: at boot, autostart.sh waits for Docker, then runs start-all (log: $agents_dir/autostart.log)."
            ;;
        off)
            printf '%s\n' "$others" | { grep -v '^$' || true; } | crontab -
            echo "Autostart is off."
            ;;
        "")
            if crontab -l 2>/dev/null | grep -qF "$marker"; then
                echo "Autostart is on. Turn it off with: $0 autostart off"
            else
                echo "Autostart is off. Turn it on with: $0 autostart on"
            fi
            ;;
        *) usage ;;
    esac
    ;;
start)
    name="${1:-}"; check_name "$name"; shift
    need_config REPO_URL AGENT_GIT_NAME AGENT_GIT_EMAIL
    unattended=
    resume=
    prompt=
    while [ $# -gt 0 ]; do
        case "$1" in
            --unattended) unattended=1; shift ;;
            --continue) resume=1; shift ;;
            --) shift; prompt="$*"; break ;;
            *) usage ;;
        esac
    done
    if session_exists "$name" || container_running "$name"; then
        echo "$name is already running. Attach with: $0 attach $name" >&2
        exit 1
    fi
    # Remembered for start-all (and so the boot autostart): start this agent the same way next time.
    mkdir -p "$agents_dir/.status/$name"
    if [ -n "$unattended" ]; then
        : > "$agents_dir/.status/$name/unattended"
    else
        rm -f "$agents_dir/.status/$name/unattended"
    fi
    claude_args=()
    [ -n "$unattended" ] && claude_args+=(--dangerously-skip-permissions)
    [ -n "$resume" ] && claude_args+=(--continue)
    [ -n "$prompt" ] && claude_args+=("$prompt")
    # The inner script runs in the container: clone on first use, then Claude Code in the workspace.
    # A conversation that was moved to a Claude Code background session can't be continued from a new container
    # (it looks registered elsewhere), and claude exits at once; then resume a copy of it with --fork-session.
    inner='if [ ! -d .git ]; then echo "Cloning $0 into the workspace..."; git clone --quiet "$0" . || exit 1; fi
started=$(date +%s); claude "$@"; rc=$?
case " $* " in *" --continue "*)
    if [ $(( $(date +%s) - started )) -lt 15 ]; then
        echo "Resuming a copy of the last conversation (--fork-session)..."; exec claude "$@" --fork-session
    fi ;;
esac
exit $rc'
    tmux new-session -d -s "$name" -x 220 -y 50 \
        "$PWD/agent.sh" "$name" bash -c "$inner" "$REPO_URL" "${claude_args[@]}"
    echo "Started $name$([ -n "$unattended" ] && echo " (unattended)"). Attach with: $0 attach $name"
    ;;
list)
    # The whole VM first, then one line per agent. CPU is per container, where 100% is one core.
    printf 'VM: load %s on %s cores, RAM %s\n' "$(cut -d' ' -f1 /proc/loadavg)" "$(nproc)" \
        "$(free -m | awk 'NR == 2 { printf "%.1f of %.1f GB used", $3 / 1024, $2 / 1024 }')"
    qn="$(queue_names)"
    printf 'Watcher: %s; queue mode: %s\n\n' "$([ -n "$(watcher_pid)" ] && echo running || echo stopped)" "${qn:-(no agents)}"
    stats=
    if [ -n "$(docker ps -q --filter label=com.docker.compose.service=agent)" ]; then
        stats="$(docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>/dev/null || true)"
    fi
    printf '%-14s %-10s %-6s %-5s %-6s %-9s %-10s %-36s %-7s %s\n' AGENT STATE SINCE CPU MEM TMUX RIDER BRANCH CHANGES DETAIL
    found=
    # Natural order (agent-2 before agent-10). Agent names can't contain spaces or newlines.
    for ws in $(printf '%s\n' "$agents_dir"/*/ | sort -V); do
        [ -d "$ws" ] || continue
        name="$(basename "$ws")"
        [[ "$name" =~ ^[a-z0-9][a-z0-9-]*$ ]] || continue
        found=1
        # Written by the agent's Claude Code hooks (agent-status in the image). The agent controls
        # this file, so keep only printable characters: no terminal escape codes reach the screen.
        agent_state=-; since=-; detail=
        if plain_file "$agents_dir/.status/$name/state"; then
            IFS=$'\t' read -r agent_state ts detail < "$agents_dir/.status/$name/state" || true
            agent_state="$(printf '%s' "$agent_state" | tr -cd '[:alnum:]-' | cut -c1-10)"
            detail="$(printf '%s' "$detail" | tr -cd '[:print:]' | cut -c1-70)"
            [[ "${ts:-}" =~ ^[0-9]+$ ]] && since="$(ago "$ts")"
        fi
        state=stopped; changes=-; cpu=-; mem=-; rider=-
        if container_running "$name"; then
            state=running
            # Written by rider-idle-stop in the container (agent-controlled, so sanitised like the state).
            if plain_file "$agents_dir/.status/$name/rider"; then
                IFS=$'\t' read -r rider rts < "$agents_dir/.status/$name/rider" || true
                rider="$(printf '%s' "$rider" | tr -cd '[:alpha:]' | cut -c1-9)"
                [ "$rider" = idle ] && [[ "${rts:-}" =~ ^[0-9]+$ ]] && rider="idle $(ago "$rts")"
            fi
            # docker stats gives e.g. "845.23%" and "1.234GiB / 30.69GiB": shorten to 845% and 1.2G.
            line="$(awk -F'\t' -v n="$name" '$1 == n' <<<"$stats")"
            if [ -n "$line" ]; then
                cpu="$(cut -f2 <<<"$line" | awk '{ printf "%.0f%%", $1 + 0 }')"
                mem="$(cut -f3 <<<"$line" | awk '{ v = $1 + 0; u = $1; gsub(/[0-9.]/, "", u);
                    if (u == "GiB") printf "%.1fG", v; else if (u == "MiB") printf "%.0fM", v; else printf "%s", $1 }')"
            fi
            # git runs inside the container: never run git on the host side in an agent's repo,
            # because the agent controls that repo's config and hooks.
            changes="$(docker exec "$name" sh -c 'git status --porcelain 2>/dev/null | wc -l' 2>/dev/null || echo '?')"
        fi
        tmux_state=-
        # "|| true": with no tmux server running, list-sessions fails, and under pipefail that
        # would end the whole script at the first agent.
        attached="$(tmux list-sessions -F '#{session_name} #{session_attached}' 2>/dev/null | awk -v n="$name" '$1 == n { print $2 }' || true)"
        if [ -n "$attached" ]; then
            tmux_state=detached
            [ "$attached" != 0 ] && tmux_state=attached
        fi
        # Read the branch from .git/HEAD directly, for the same reason.
        branch=-
        [ ! -L "$ws/.git" ] && plain_file "$ws/.git/HEAD" && branch="$(sed 's#^ref: refs/heads/##' "$ws/.git/HEAD" | tr -cd '[:print:]' | cut -c1-34)"
        # A state left over from a container that has since stopped isn't current.
        if [ "$state" = stopped ] && [ "$agent_state" != ended ] && [ "$agent_state" != - ]; then
            agent_state=stopped; detail=
        fi
        printf '%-14s %-10s %-6s %-5s %-6s %-9s %-10s %-36s %-7s %s\n' \
            "$name" "$agent_state" "$since" "$cpu" "$mem" "$tmux_state" "$rider" "$branch" "$changes" "$(cut -c1-50 <<<"$detail")"
    done
    [ -n "$found" ] || echo "(no agents yet; start one with: $0 start agent-1)"
    ;;
watch)
    interval="${1:-2}"
    [[ "$interval" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "Usage: $0 watch [seconds], e.g. $0 watch 10" >&2; exit 1; }
    # --precise: refresh every <interval> seconds from start to start, so the ~2 s that
    # docker stats needs for its CPU sample doesn't add to the interval.
    exec watch -t -p -n "$interval" "$PWD/agents.sh" list
    ;;
attach)
    name="${1:-}"; check_name "$name"
    session_exists "$name" || { echo "$name isn't running. Start it with: $0 start $name" >&2; exit 1; }
    if [ -n "${TMUX:-}" ]; then exec tmux switch-client -t "=$name"; else exec tmux attach -t "=$name"; fi
    ;;
send)
    name="${1:-}"; check_name "$name"; shift
    force=
    if [ "${1:-}" = --force ]; then force=1; shift; fi
    if [ $# -gt 0 ]; then prompt="$*"; else prompt="$(cat)"; fi
    [ -n "$(tr -d '[:space:]' <<<"$prompt")" ] || { echo "Nothing to send: give the prompt as arguments or on stdin." >&2; exit 1; }
    container_running "$name" && session_exists "$name" || { echo "$name isn't running." >&2; exit 1; }
    status_dir="$agents_dir/.status/$name"
    state=
    plain_file "$status_dir/state" && state="$(cut -f1 "$status_dir/state" | tr -cd '[:alnum:]-')"
    case "$state" in
        ready|done) ;;
        *)  if [ -z "$force" ]; then
                echo "$name is ${state:-in an unknown state}, not waiting for a prompt. --force sends it anyway: while it's working, Claude Code queues the prompt for its next turn; at a question, the prompt may be taken as the answer." >&2
                exit 1
            fi ;;
    esac
    # The prompt goes into a file, and one fixed line is typed into the session: a prompt of any length or
    # shape works, and nothing in it can set off a slash command, an @-completion or a keyboard shortcut.
    # mktemp + mv -T, because the agent writes in this folder too and could have left a symlink under that name.
    tmp="$(mktemp "$status_dir/.prompt.XXXXXX")"
    printf '%s\n' "$prompt" > "$tmp"
    mv -fT "$tmp" "$status_dir/prompt.md"
    tmux send-keys -t "=$name:" -l "Read /run/agent-status/prompt.md and do what it says."
    sleep 0.3
    tmux send-keys -t "=$name:" Enter
    for _ in $(seq 1 10); do
        sleep 0.5
        if plain_file "$status_dir/state" && [ "$(cut -f1 "$status_dir/state")" = working ]; then
            echo "Sent to $name; it's working on it."
            exit 0
        fi
    done
    echo "Sent to $name, but it hasn't started on it after 5 s. Look with: $0 attach $name" >&2
    exit 2
    ;;
queue)
    name="${1:-}"
    if [ -z "$name" ]; then
        qn="$(queue_names)"
        echo "Queue mode: ${qn:-(no agents)}"
        "$PWD/agents.sh" watcher status
        exit 0
    fi
    check_name "$name"
    case "${2:-}" in
        on)
            mkdir -p "$agents_dir/.queue"
            : > "$agents_dir/.queue/$name"
            echo "$name is in queue mode: whenever it is ready or done with no task in progress, the watcher gives it the oldest todo task."
            [ -n "$(watcher_pid)" ] || echo "The watcher isn't running; start it with: $0 watcher start"
            ;;
        off)
            rm -f "$agents_dir/.queue/$name"
            echo "$name is out of queue mode."
            ;;
        *) usage ;;
    esac
    ;;
watcher)
    case "${1:-}" in
        start)
            p="$(watcher_pid)"
            if [ -n "$p" ]; then echo "The watcher is already running (pid $p)."; exit 0; fi
            mkdir -p "$agents_dir"
            setsid "$PWD/mcp/watch" >> "$agents_dir/watch.log" 2>&1 < /dev/null &
            # The first start on a new VM downloads the watcher's Python packages first, which takes a while.
            for _ in $(seq 1 60); do
                p="$(watcher_pid)"
                [ -n "$p" ] && break
                sleep 1
            done
            if [ -n "$p" ]; then
                echo "Watcher started (pid $p): events in $agents_dir/events.jsonl, log in $agents_dir/watch.log."
            else
                echo "The watcher hasn't started after a minute; see $agents_dir/watch.log" >&2
                exit 1
            fi
            ;;
        stop)
            p="$(watcher_pid)"
            if [ -n "$p" ]; then kill "$p" && echo "Watcher stopped."; else echo "The watcher isn't running."; fi
            ;;
        status|"")
            p="$(watcher_pid)"
            if [ -n "$p" ]; then echo "Watcher: running (pid $p)"; else echo "Watcher: stopped (start it with: $0 watcher start)"; fi
            ;;
        *) usage ;;
    esac
    ;;
shell)
    name="${1:-}"; check_name "$name"
    container_running "$name" || { echo "$name isn't running." >&2; exit 1; }
    exec docker exec -it "$name" bash
    ;;
stop)
    name="${1:-}"; check_name "$name"
    container_running "$name" && docker stop -t 10 "$name" >/dev/null && echo "Stopped container $name."
    session_exists "$name" && tmux kill-session -t "=$name" && echo "Closed tmux session $name."
    echo "Workspace kept: $agents_dir/$name"
    ;;
rider-stop)
    name="${1:-}"; check_name "$name"
    container_running "$name" || { echo "$name isn't running." >&2; exit 1; }
    # Only Rider's own processes: command lines that start with its backend folder (same match as
    # rider-idle-stop in the image). Claude Code and the agent's builds keep running.
    if docker exec "$name" pkill -f '^(/bin/sh )?/home/agent/\.(cache/JetBrains|jetbrains/cache)/RemoteDev/dist/'; then
        echo "Stopped the Rider backend in $name. Connecting from Rider starts it again."
    else
        echo "No Rider backend is running in $name."
    fi
    ;;
*)
    usage ;;
esac
