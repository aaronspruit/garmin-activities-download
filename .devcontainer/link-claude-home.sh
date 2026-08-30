#!/usr/bin/env bash
# Point the Claude Code state of the container user at the host home.
#
# HOST_HOME is mounted at the same absolute path that it has on the host, so
# every absolute path that the host wrote into ~/.claude also resolves here.
# This script only adds the links that the container user needs.
set -euo pipefail

: "${HOST_HOME:?HOST_HOME is not set. Check containerEnv in devcontainer.json.}"

if [ ! -d "$HOST_HOME" ]; then
    echo "link-claude-home: $HOST_HOME is not mounted. No link is made." >&2
    exit 0
fi

# The host home is already this home. Nothing to link.
if [ "$HOST_HOME" = "$HOME" ]; then
    exit 0
fi

# True when the path is a mount point. A mount point holds live host data, so
# the loop below must never delete one.
is_mount() {
    awk -v path="$1" '$5 == path { found = 1 } END { exit !found }' /proc/self/mountinfo
}

for name in .claude .agents .claude.json; do
    target="$HOME/$name"

    if is_mount "$target"; then
        echo "link-claude-home: $target is a mount point. It is left alone." >&2
        continue
    fi

    if [ ! -L "$target" ]; then
        rm -rf "$target"
    fi

    ln -sfn "$HOST_HOME/$name" "$target"
done
