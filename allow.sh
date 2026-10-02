#!/usr/bin/env bash
# Manages the agents' proxy allow-list. Hosts are added to project/allowed-domains.txt, the project's own
# list; proxy/allowed-domains.txt is the base list (Claude Code's hosts). The proxy allows both.
# Changes apply by signalling the proxy to reload, so open agent connections are not cut.
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh

list=project/allowed-domains.txt
base=proxy/allowed-domains.txt
section="# Added with allow.sh"

usage() {
    cat >&2 <<'EOF'
Usage:
  ./allow.sh <host> [host...]   add hosts and apply, e.g. ./allow.sh api.example.com '*.example.com'
  ./allow.sh --refused          list hosts the proxy has refused, most frequent first
  ./allow.sh --reload           apply edits made to the lists by hand
EOF
    exit 1
}

reload() {
    # Not running yet (a new VM): it reads both lists when it starts, with the first agent.
    if [ -z "$(compose ps -q --status running proxy 2>/dev/null)" ]; then
        echo "The proxy isn't running; it reads the lists when it starts (with the first agent)."
        return 0
    fi
    # merge-filter rebuilds the proxy's filter from both lists; "kill -s USR1" makes tinyproxy reload it.
    # The container keeps running.
    if ! compose exec -T proxy merge-filter >/dev/null || ! compose kill -s USR1 proxy >/dev/null 2>&1; then
        echo "The proxy didn't reload; see: docker logs agents-proxy-1" >&2
        exit 1
    fi
    echo "Proxy reloaded the allow-list."
}

case "${1:-}" in
    "") usage ;;
    --refused)
        compose logs proxy --no-log-prefix \
            | grep -o 'filtered domain "[^"]*"' | cut -d'"' -f2 | sort | uniq -c | sort -rn
        exit 0 ;;
    --reload)
        reload
        exit 0 ;;
    -*) usage ;;
esac

if [ ! -f "$list" ]; then
    mkdir -p project
    cat > "$list" <<'EOF'
# The project's hosts the agent containers may reach through the proxy, one shell-style pattern per line,
# on top of the base list in proxy/allowed-domains.txt. "*.example.com" does not match "example.com" itself.
# Add hosts with ./allow.sh <host>; after editing this file by hand, run ./allow.sh --reload
EOF
fi

added=0
for host in "$@"; do
    host="${host,,}"
    # A host name with at least one dot, optionally prefixed by "*." ("*.com" is rejected).
    if ! [[ "$host" =~ ^(\*\.)?([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
        echo "Skipped '$host': not a host name (expected api.example.com or *.example.com)" >&2
        continue
    fi
    if grep -qxF -- "$host" "$list" "$base" 2>/dev/null; then
        echo "Already allowed: $host"
        continue
    fi
    # Keep the file ending in a newline, and group added hosts under one heading.
    [ -z "$(tail -c1 "$list")" ] || echo >> "$list"
    grep -qxF -- "$section" "$list" || printf '\n%s\n' "$section" >> "$list"
    printf '%s\n' "$host" >> "$list"
    echo "Added: $host"
    case "$host" in \*.*) echo "  note: allows every subdomain of ${host#\*.}, but not ${host#\*.} itself" ;; esac
    added=1
done

if [ "$added" -eq 1 ]; then reload; fi
