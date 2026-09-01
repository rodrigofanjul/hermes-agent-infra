# Mnemosyne Memory Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Mnemosyne external memory provider to the `hermes` service, built into a custom Docker image (versioned, pinned) with an idempotent runtime bootstrap script — without regressing the container's ability to boot correctly.

**Architecture:** A new `Dockerfile` builds on the pinned `nousresearch/hermes-agent:v2026.8.31` base, installing `mnemosyne-hermes==0.5.0` (which pulls `mnemosyne-memory[embeddings]`) into the agent's venv at build time. A new `mnemosyne-bootstrap.sh` script, baked into the image, runs on every container start: it idempotently symlinks the installed plugin into `$HERMES_HOME/plugins/mnemosyne`, sets `memory.provider` to `mnemosyne` via the `hermes` CLI, then `exec`s the real gateway command. `docker-compose.yml` switches the `hermes` service from `image:` to `build: .` and from `command: gateway run` to the bootstrap script.

**Tech Stack:** Docker, Docker Compose, Coolify (deploy via GitHub webhook), `hermes` CLI (Python, inside a `uv`-managed venv), `mnemosyne-hermes` PyPI package.

**Reference:** Design doc at `docs/superpowers/specs/2026-08-31-mnemosyne-memory-provider-design.md` — read it first, it has the full rationale and the risk analysis this plan mitigates.

---

### Task 1: Create the Dockerfile

**Files:**
- Create: `E:\Repositorios\hermes-agent-infra\Dockerfile`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# Custom hermes-agent image with the Mnemosyne external memory provider
# baked in at a pinned version. See docs/superpowers/specs/2026-08-31-
# mnemosyne-memory-provider-design.md for why this exists and what it
# does NOT do (the actual plugin wiring happens at container startup,
# in mnemosyne-bootstrap.sh — this Dockerfile only installs the package).
#
# Upgrading hermes-agent: bump the FROM tag here AND in the comment
# below, in the same commit. This image must be rebuilt (docker compose
# build), not pulled, after any change to this file or to the FROM tag.
FROM nousresearch/hermes-agent:v2026.8.31

RUN /opt/hermes/.venv/bin/python -m pip install mnemosyne-hermes==0.5.0

COPY mnemosyne-bootstrap.sh /usr/local/bin/mnemosyne-bootstrap.sh
RUN chmod +x /usr/local/bin/mnemosyne-bootstrap.sh
```

- [ ] **Step 2: Commit**

```bash
cd E:\Repositorios\hermes-agent-infra
git add Dockerfile
git commit -m "Add Dockerfile for custom hermes-agent image with Mnemosyne"
```

---

### Task 2: Create the bootstrap script

**Files:**
- Create: `E:\Repositorios\hermes-agent-infra\mnemosyne-bootstrap.sh`

- [ ] **Step 1: Write the script**

```bash
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

exec gateway run
```

(This guard was added after a code-quality review caught a real risk: without it, an empty `$PKG_DIR` — which `set -e` does not catch, since a command substitution can succeed with empty output — turns the `ln -sfn "$PKG_DIR"/*` glob into `ln -sfn /*`, symlinking the entire root filesystem into the plugin directory.)

- [ ] **Step 2: Make it executable locally (git preserves the mode bit on push)**

```bash
cd E:\Repositorios\hermes-agent-infra
git update-index --add --chmod=+x mnemosyne-bootstrap.sh
```

If `git update-index` errors with "not something we can add" because
the file isn't staged yet, run `git add mnemosyne-bootstrap.sh` first,
then retry the `update-index` command above.

- [ ] **Step 3: Commit**

```bash
git add mnemosyne-bootstrap.sh
git commit -m "Add mnemosyne-bootstrap.sh: idempotent plugin wiring at container start"
```

---

### Task 3: Validate the entrypoint assumption empirically, before touching the live compose

This is the highest-risk step in the whole plan (see design doc's "Riesgo
abierto" section). Do this on the actual server, in complete isolation
from the running Coolify-managed containers — it must not touch
`hermes-e3eeyshcsvecewtkagam3np2-*` or its volumes.

**Files:** none (server-side, throwaway build directory)

- [ ] **Step 1: Copy the two new files to a scratch directory on Server B**

```bash
ssh opc@oracle-us-west "mkdir -p /tmp/mnemosyne-test"
scp E:\Repositorios\hermes-agent-infra\Dockerfile opc@oracle-us-west:/tmp/mnemosyne-test/Dockerfile
scp E:\Repositorios\hermes-agent-infra\mnemosyne-bootstrap.sh opc@oracle-us-west:/tmp/mnemosyne-test/mnemosyne-bootstrap.sh
```

- [ ] **Step 2: Build the image standalone (not via Coolify)**

```bash
ssh opc@oracle-us-west "cd /tmp/mnemosyne-test && sudo docker build -t mnemosyne-test:latest ."
```

Expected: build completes with exit code 0, ending in
`Successfully tagged mnemosyne-test:latest` (or the buildkit
equivalent "naming to docker.io/library/mnemosyne-test:latest done").
If `pip install mnemosyne-hermes==0.5.0` fails here, stop — do not
proceed to Task 4. Report the error; it means the pinned version needs
re-checking on PyPI before continuing.

- [ ] **Step 3: Run it standalone with the new command, watch what happens**

```bash
ssh opc@oracle-us-west "sudo docker run --rm --name mnemosyne-entrypoint-test mnemosyne-test:latest 2>&1 | head -60"
```

Expected: the same startup sequence you'd see from a normal
`hermes-agent` boot (UID/GID chown messages, then the gateway startup
banner) — NOT an error like `exec: entrypoint-dispatch.sh: no such
file or directory` or `command not found: gateway`. Since this
container has no real `HERMES_DASHBOARD_BASIC_AUTH_*` / API key env
vars set, it is expected to then fail on **its own** config validation
(missing required env vars) — that failure is fine and expected. What
you're checking is that it gets far enough to prove `exec gateway run`
at the end of the bootstrap script is being reached and handled the
same way the original `command: gateway run` was.

- [ ] **Step 4: Clean up the scratch image**

```bash
ssh opc@oracle-us-west "sudo docker rmi mnemosyne-test:latest; rm -rf /tmp/mnemosyne-test"
```

- [ ] **Step 5: Decision point**

If Step 3's output showed the expected startup sequence reaching the
gateway logic (even if it then failed on missing env vars) — proceed
to Task 4. If it showed an entrypoint-level failure instead, STOP and
report back; the bootstrap script's final `exec gateway run` line
needs to be changed to match whatever the real entrypoint expects
(this cannot be resolved without seeing that failure's exact message).

---

### Task 4: Update docker-compose.yml

**Files:**
- Modify: `E:\Repositorios\hermes-agent-infra\docker-compose.yml`

- [ ] **Step 1: Change the `hermes` service's `image:` to `build: .`**

Find this line (currently near the top of the `hermes` service block):

```yaml
    image: nousresearch/hermes-agent:v2026.8.31
```

Replace it with:

```yaml
    build: .
```

- [ ] **Step 2: Change the `hermes` service's `command:`**

Find:

```yaml
    command: gateway run
```

Replace with:

```yaml
    command: ["/usr/local/bin/mnemosyne-bootstrap.sh"]
```

- [ ] **Step 3: Update the top-of-file comment block that documents the image tag**

Find the comment mentioning `nousresearch/hermes-agent:v2026.8.31` near
the top of the file (in the header comment block, not the service
definition) and add a note that this service now builds from the local
`Dockerfile` instead of pulling the image directly — so future readers
don't go looking for `image:` and get confused. Insert this sentence
right after the existing sentence that names the pinned tag:

```
The `hermes` service builds from this repo's Dockerfile (see
Dockerfile) instead of pulling the image directly, to bundle the
Mnemosyne memory provider — see
docs/superpowers/specs/2026-08-31-mnemosyne-memory-provider-design.md.
```

- [ ] **Step 4: Commit**

```bash
cd E:\Repositorios\hermes-agent-infra
git add docker-compose.yml
git commit -m "Switch hermes service to build: ., wire in Mnemosyne bootstrap"
```

---

### Task 5: Deploy and verify

**Files:** none (deployment + verification only)

- [ ] **Step 1: Push to trigger the Coolify webhook**

```bash
cd E:\Repositorios\hermes-agent-infra
git push
```

- [ ] **Step 2: Confirm the deployment finishes and the container is healthy**

```bash
ssh opc@oracle-us-west "sudo docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | grep '^hermes-e'"
```

Expected: one line, status `Up ... (healthy)` (allow up to ~60s for
the healthcheck to pass after a fresh start — if it still says
`(health: starting)`, wait and re-run). If it's crash-looping
(`Restarting`), the entrypoint assumption from Task 3 didn't hold in
the real Coolify-managed environment even though the standalone test
passed — check logs with
`ssh opc@oracle-us-west "sudo docker logs <container-name> --tail 50"`
and stop here to report back rather than guessing at a fix.

- [ ] **Step 3: Confirm the Mnemosyne provider is active**

```bash
ssh opc@oracle-us-west "sudo docker exec <container-name> /opt/hermes/.venv/bin/hermes memory status"
```

(Replace `<container-name>` with the name from Step 2's output.)
Expected: output includes `provider: mnemosyne` (or equivalent — the
active external provider is `mnemosyne`, not empty/none).

- [ ] **Step 4: Confirm the plugin's tools are registered**

```bash
ssh opc@oracle-us-west "sudo docker exec <container-name> /opt/hermes/.venv/bin/hermes tools list | grep mnemosyne_"
```

Expected: at least one line starting with `mnemosyne_` (e.g.
`mnemosyne_search`, `mnemosyne_write` — exact names depend on the
installed plugin version, any match confirms registration worked).

- [ ] **Step 5: Confirm the existing cron jobs still work**

```bash
ssh opc@oracle-us-west "sudo docker exec <container-name> python3 -c \"
import json
d = json.load(open('/opt/data/cron/jobs.json'))
for j in d['jobs']:
    print(j['name'], '-', j.get('last_status'))
\""
```

Expected: all three jobs (`Resumen diario de gastos`, `Recordatorios
diarios - pagos y actividades`, `Carga cierres de tarjeta en Sheet`)
still listed — their `last_status` won't change until their next
scheduled run, this step only confirms `jobs.json` wasn't corrupted or
lost by the redeploy.

- [ ] **Step 6: Manual smoke test in hermes-webui**

Send a real message in hermes-webui (`http://100.98.37.62:8787`) and
confirm it responds normally. This has no automatable pass/fail
check — it's a human confirmation that the redeploy didn't break the
normal chat path.

---

## Rollback

If Task 5 fails and cannot be fixed quickly:

```bash
cd E:\Repositorios\hermes-agent-infra
git revert HEAD~2..HEAD    # reverts Task 4's commit and Task 5's push commit if any
git push
```

This restores `image: nousresearch/hermes-agent:v2026.8.31` and
`command: gateway run`, triggering a redeploy back to the known-good
state. `hermes-data` (config, memory, cron) is untouched by any of
this — built-in memory keeps working throughout, per the design doc.
