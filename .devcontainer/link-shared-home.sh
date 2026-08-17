#!/usr/bin/env bash
# Points the durable parts of ~/.claude at the shared volume that the DevPod
# pod manifest template mounts at /shared. Every workspace of the provider
# mounts the same volume, so one login and one set of skills serve all of them.
#
# Session state stays out of the shared volume on purpose. projects/,
# history.jsonl, todos/ and statsig/ get one writer for each workspace, and NFS
# does not lock them against each other. Those paths live on the workspace PVC,
# which the pod mounts at ~/.claude.
#
# CAUTION: ~/.claude/projects holds the per-project memory files. Memory is
# therefore per workspace, not shared. To share it, add "projects" to
# SHARED_DIRS below and accept the risk of two live workspaces.
#
# The script is safe to run again. It is also safe on a workstation, where
# /shared does not exist and the script stops at the first test.
set -euo pipefail

SHARED_ROOT="${SHARED_HOME:-/shared}"
CLAUDE_DIR="${HOME}/.claude"

if [ ! -d "${SHARED_ROOT}" ]; then
  echo "link-shared-home: no shared volume at ${SHARED_ROOT}. Nothing to link."
  exit 0
fi

# Read-mostly folders. A folder survives an atomic write, which replaces a file.
SHARED_DIRS=(skills agents commands plugins)

# Small files. An atomic write can replace one of these links with a regular
# file. The next run of this script moves that file to the shared volume and
# makes the link again.
SHARED_FILES=(.credentials.json settings.json CLAUDE.md)

mkdir -p "${CLAUDE_DIR}" "${SHARED_ROOT}/claude"

link_entry() {
  local name="$1" kind="$2"
  local link="${CLAUDE_DIR}/${name}"
  local target="${SHARED_ROOT}/claude/${name}"

  if [ -L "${link}" ]; then
    return
  fi

  if [ -e "${link}" ]; then
    if [ -e "${target}" ]; then
      echo "link-shared-home: ${link} and ${target} both hold data. Left both alone."
      return
    fi
    # First run, or a write that replaced the link. Move the data to the
    # shared volume so that no copy is lost.
    mv "${link}" "${target}"
    echo "link-shared-home: moved ${name} to the shared volume."
  elif [ "${kind}" = "dir" ]; then
    mkdir -p "${target}"
  fi

  # A file that no copy exists for gets a link to a path that is still empty.
  # The first write then lands on the shared volume.
  ln -s "${target}" "${link}"
}

for name in "${SHARED_DIRS[@]}"; do
  link_entry "${name}" dir
done

for name in "${SHARED_FILES[@]}"; do
  link_entry "${name}" file
done

echo "link-shared-home: ~/.claude is linked to ${SHARED_ROOT}/claude."
