#!/usr/bin/env bash
# Saves one secret for the agent containers into secrets/<name>, readable only by you.
# The value is typed or pasted at a prompt, so it is not echoed and stays out of shell history.
# Usage: ./save-secret.sh <name>
#        FORCE=1 ./save-secret.sh <name>   saves a token even if its prefix looks unexpected
set -euo pipefail
cd "$(dirname "$0")"

names="git-token claude-oauth-token jira-token confluence-token atlassian-email"
name="${1:-}"
case " $names " in
    *" $name "*) ;;
    *) echo "Usage: $0 <name>   where <name> is one of: $names" >&2; exit 1 ;;
esac

# Terminals wrap pasted text in "bracketed paste" markers (ESC[200~ ... ESC[201~) when that mode
# is on; switch it off for the prompt, and strip any markers and control characters that get through.
[ -t 1 ] && printf '\e[?2004l'
if [ "$name" = atlassian-email ]; then
    read -rp "Atlassian account email: " value
else
    read -rsp "Paste $name (nothing is shown), then press Enter: " value
    echo
fi
value="$(printf '%s' "$value" | sed 's/\x1b\[20[01]~//g' | tr -d '[:cntrl:]' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"

if [ -z "$value" ]; then
    echo "Nothing entered; $name was not saved." >&2
    exit 1
fi
# The same text twice in a row is a double paste; keep one copy.
half=$(( ${#value} / 2 ))
if [ $(( ${#value} % 2 )) -eq 0 ] && [ "${value:0:half}" = "${value:half}" ]; then
    value="${value:0:half}"
    echo "The value was pasted twice; keeping one copy."
fi
case "$value" in
    *[[:space:]]*)
        echo "That value contains spaces, so it isn't a $name (a pasted command?). Not saved." >&2
        exit 1 ;;
esac

# Known prefixes: git-token is a GitHub (ghp_, github_pat_), GitLab (glpat-) or Bitbucket token (Atlassian API
# token ATATT, repository access token ATCTT); Atlassian API tokens (ATATT) for Jira and Confluence;
# Claude Code tokens from "claude setup-token" (sk-ant-oat).
case "$name" in
    git-token)                  expected="ghp_ github_pat_ glpat- ATATT ATCTT" ;;
    jira-token|confluence-token) expected="ATATT" ;;
    claude-oauth-token)         expected="sk-ant-oat" ;;
    atlassian-email)            expected="" ;;
esac
if [ "$name" = atlassian-email ]; then
    case "$value" in *@*.*) ;; *) echo "That doesn't look like an email address. Not saved." >&2; exit 1 ;; esac
elif [ "${FORCE:-}" != 1 ]; then
    ok=
    for p in $expected; do case "$value" in "$p"*) ok=1 ;; esac; done
    if [ -z "$ok" ]; then
        echo "A $name normally starts with: $expected. Not saved." >&2
        echo "If yours really looks different, run again with FORCE=1." >&2
        exit 1
    fi
fi

mkdir -p secrets
chmod 700 secrets
(umask 077; printf '%s' "$value" > "secrets/$name")
echo "Saved secrets/$name (${#value} characters)."
