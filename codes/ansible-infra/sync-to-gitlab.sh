#!/usr/bin/env bash
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Documents/projects/hci/ansible-infra"

if [[ ! -d "$DEST" ]]; then
  echo "Destination not found: $DEST"
  exit 1
fi

rsync -av --delete \
  --exclude='ansible.cfg' \
  --exclude='.git/' \
  --exclude='sync-to-gitlab.sh'\
  "$SRC/" "$DEST/"

echo "Synced to $DEST (ansible.cfg excluded)"
