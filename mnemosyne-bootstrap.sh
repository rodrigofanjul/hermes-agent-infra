#!/bin/sh
set -eu

# Runs on every container start. Idempotent: safe to run repeatedly.
# Does NOT install or download anything — only links files already
# baked into the image (see Dockerfile) and sets a config value.
HERMES_HOME=/opt/data
VENV=/opt/hermes/.venv

mkdir -p "$HERMES_HOME/plugins/mnemosyne"

PKG_DIR="$("$VENV/bin/python" -c \
  'import pathlib, mnemosyne_hermes; print(pathlib.Path(mnemosyne_hermes.__file__).resolve().parent)')"

if [ -z "$PKG_DIR" ] || [ ! -d "$PKG_DIR" ]; then
  echo "mnemosyne-bootstrap: PKG_DIR is empty or not a directory ('$PKG_DIR') — refusing to symlink, aborting" >&2
  exit 1
fi

ln -sfn "$PKG_DIR"/* "$HERMES_HOME/plugins/mnemosyne/"

"$VENV/bin/hermes" config set memory.provider mnemosyne

exec "$VENV/bin/hermes" gateway run
