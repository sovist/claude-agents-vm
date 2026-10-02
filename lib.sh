# Shared by the scripts in this repo; sourced, not run.
#  - Loads config.env (exported, so docker compose and the MCP server see the settings).
#  - compose: docker compose with compose.yaml plus project/compose.yaml when that exists, from any folder.
#    With several files, compose resolves relative paths in all of them from the repo root.
#    compose_cmd holds the same command as an array, for "exec" (which can't run a shell function).
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$repo_root/config.env" ]; then
    set -a
    # shellcheck source=/dev/null
    . "$repo_root/config.env"
    set +a
fi

compose_cmd=(docker compose --project-directory "$repo_root" -f "$repo_root/compose.yaml")
[ -f "$repo_root/project/compose.yaml" ] && compose_cmd+=(-f "$repo_root/project/compose.yaml")
compose() { "${compose_cmd[@]}" "$@"; }

need_config() {
    local missing=() v
    for v in "$@"; do [ -n "${!v:-}" ] || missing+=("$v"); done
    if [ ${#missing[@]} -gt 0 ]; then
        echo "Set ${missing[*]} in $repo_root/config.env (see config.example.env)." >&2
        exit 1
    fi
}
