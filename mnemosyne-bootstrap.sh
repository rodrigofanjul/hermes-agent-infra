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

# hermes-webui runs the agent in its OWN Python venv, which cannot see the
# Mnemosyne plugin's dependency environment under /opt/data/installs (that
# environment is composed only into the agent's own processes). Without this,
# webui chats log "Memory provider 'mnemosyne' loaded but no provider instance
# found" and have no long-term memory. The webui service sets
# PYTHONPATH=/home/hermeswebui/.hermes/webui-site (see docker-compose.yml);
# Python imports sitecustomize.py from there at startup and it APPENDS (never
# prepends) the plugin environment, so the webui's own packages keep priority.
# Located with a glob, so it survives plugin updates that change the env hashes.
# Rewritten only when its content changes (idempotent).
WEBUI_SITE="$HERMES_HOME/webui-site"
mkdir -p "$WEBUI_SITE"
cat > "$WEBUI_SITE/sitecustomize.py.new" <<'PYEOF'
# Managed by mnemosyne-bootstrap.sh (hermes-agent-infra) - do not edit by hand.
# hermes-webui runs the agent in its own venv, which cannot see the Mnemosyne
# plugin's dependency environment under ~/.hermes/installs/. Append (never
# prepend) that environment so the webui's own packages keep priority.
import glob as _glob
import sys as _sys

try:
    _tag = "python%d.%d" % (_sys.version_info[0], _sys.version_info[1])
    for _p in sorted(_glob.glob(
        "/home/hermeswebui/.hermes/installs/*/environments/*/venv/lib/%s/site-packages" % _tag
    )):
        if _p not in _sys.path:
            _sys.path.append(_p)
except Exception:
    pass
PYEOF
if cmp -s "$WEBUI_SITE/sitecustomize.py.new" "$WEBUI_SITE/sitecustomize.py" 2>/dev/null; then
  rm -f "$WEBUI_SITE/sitecustomize.py.new"
else
  mv "$WEBUI_SITE/sitecustomize.py.new" "$WEBUI_SITE/sitecustomize.py"
  echo "mnemosyne-bootstrap: wrote $WEBUI_SITE/sitecustomize.py (Mnemosyne for hermes-webui)"
fi

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

# Hermes's browser tool in this setup is `browser_exec`, which drives Chromium
# through the agent-browser CLI. Since v0.22 that CLI is managed by `hermes pm`
# and the availability check never installs it, so without this step the model
# is simply told it has no browser (and never reaches the lazy installer).
# Installs into /opt/data/tools (persistent hermes-data volume); a ~2s no-op when
# already installed. Needs internet only the first time. Not fatal if it fails.
"$VENV/bin/hermes" pm install agent-browser \
  || echo "mnemosyne-bootstrap: ERROR: could not install agent-browser - Hermes will report having no browser" >&2

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
