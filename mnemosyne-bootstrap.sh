#!/bin/sh
set -eu

# Runs on every container start. Idempotent: safe to run repeatedly.
#
# Since hermes-agent v0.22 Mnemosyne is a catalog plugin, not a pip package
# baked into the image. The plugin and its Python environment live under
# /opt/data (the persistent hermes-data volume), so the install below only
# does real work on the first boot after the upgrade; later boots find
# plugin.yaml and skip it. Memory itself is /opt/data/mnemosyne/data/
# mnemosyne.db and is never touched by this script.
#
# Failures here must not stop the gateway (a crash loop is worse than
# running with the built-in MEMORY.md/USER.md memory only), so each step logs
# a loud ERROR and continues.
HERMES_HOME=/opt/data
VENV=/opt/hermes/.venv
PLUGIN_DIR="$HERMES_HOME/plugins/mnemosyne"
CHANGED=0

mkdir -p "$HERMES_HOME/plugins"

# Legacy layout (hermes <= v2026.9.x): plugins/mnemosyne held symlinks into the
# old image's mnemosyne_hermes package. After the upgrade they dangle, and the
# catalog installer would refuse to replace them. Move them aside, don't delete.
if [ -d "$PLUGIN_DIR" ] && find "$PLUGIN_DIR" -maxdepth 1 -type l | grep -q .; then
  LEGACY="$HERMES_HOME/plugins/mnemosyne.legacy-symlinks-$(date -u +%Y%m%dT%H%M%SZ)"
  echo "mnemosyne-bootstrap: moving legacy symlink plugin dir to $LEGACY"
  mv "$PLUGIN_DIR" "$LEGACY"
fi

if [ ! -f "$PLUGIN_DIR/plugin.yaml" ]; then
  echo "mnemosyne-bootstrap: installing mnemosyne plugin from the Hermes catalog"
  "$VENV/bin/hermes" plugins install mnemosyne --yes-deps --enable \
    || echo "mnemosyne-bootstrap: ERROR: plugin install failed - gateway will start without Mnemosyne memory" >&2
  CHANGED=1
fi

# Stop-gap for mnemosyne-hermes 0.7.3/0.7.4 (see the script's docstring). No-op
# once the installed version constructs correctly.
FIX_OUT="$("$VENV/bin/python" /usr/local/bin/mnemosyne-ctor-fix.py 2>&1)" \
  || echo "mnemosyne-bootstrap: ERROR: mnemosyne-ctor-fix.py failed" >&2
echo "$FIX_OUT"
case "$FIX_OUT" in *"applied upstream"*) CHANGED=1 ;; esac

"$VENV/bin/hermes" config set memory.provider mnemosyne

# The s6-supervised gateway starts in parallel with this script and may have
# loaded the plugin before it was installed/patched above. Restart it once so
# it picks up the working provider. Later boots find everything already in
# place on the volume and skip this.
if [ "$CHANGED" = "1" ]; then
  echo "mnemosyne-bootstrap: plugin installed/patched this boot - restarting gateway"
  "$VENV/bin/hermes" gateway restart \
    || echo "mnemosyne-bootstrap: ERROR: gateway restart failed" >&2
fi

exec "$VENV/bin/hermes" gateway run
