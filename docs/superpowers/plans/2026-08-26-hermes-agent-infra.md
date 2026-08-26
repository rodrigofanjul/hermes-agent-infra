# Hermes Agent Infra Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `hermes-agent-infra`, a Coolify-deployable repo that runs Hermes Agent as a single container using its built-in web dashboard, replacing the broken two-container `hermes-agent-with-webui` template.

**Architecture:** One Docker Compose service (`nousresearch/hermes-agent`, pinned tag, `HERMES_DASHBOARD=1`) behind Coolify/Traefik HTTPS, a named volume for `/opt/data`, basic-auth-protected dashboard, no LLM provider keys (model routed through OmniRoute, configured post-deploy from the dashboard), and a restic+Backblaze B2 backup script reusing the same bucket as `ai-gateway-infra`.

**Tech Stack:** Docker Compose, Bash (backup script), restic, Coolify (Traefik reverse proxy), Markdown docs.

---

## File Structure

```
hermes-agent-infra/
├── .gitignore
├── .env.example
├── docker-compose.yml
├── README.md
├── scripts/
│   └── backup.sh
└── docs/superpowers/
    ├── specs/2026-08-26-hermes-agent-infra-design.md   (already committed)
    └── plans/2026-08-26-hermes-agent-infra.md           (this file)
```

Each file has one job: `.gitignore` keeps secrets/cruft out of git, `.env.example` documents required variables without real values, `docker-compose.yml` is the single source of truth Coolify deploys, `scripts/backup.sh` is a standalone host-cron script (not invoked by Coolify), `README.md` is the deployment runbook.

---

### Task 1: `.gitignore`

**Files:**
- Create: `.gitignore`

- [ ] **Step 1: Write the file**

```gitignore
# --- Secrets ---------------------------------------------------------------
# Real env files are never committed — Coolify holds the actual values.
# See .env.example for the reference of what's needed.
.env
.env.*
!.env.example

# --- AI tooling / agent local state -----------------------------------------
# Claude Code's local session state (locks, caches) — machine-specific,
# never meant to be shared or versioned.
.claude/
.cursor/
.aider*

# --- OS cruft ----------------------------------------------------------------
.DS_Store
.DS_Store?
._*
.Spotlight-V100
.Trashes
ehthumbs.db
Thumbs.db
desktop.ini

# --- Editor / IDE cruft --------------------------------------------------
.vscode/
!.vscode/extensions.json
.idea/
*.swp
*.swo
*~
*.sublime-workspace
*.sublime-project

# --- Local scratch / logs -------------------------------------------------
*.log
*.tmp
.scratch/
scratchpad/
```

- [ ] **Step 2: Verify no unintended files are tracked**

Run: `cd /e/Repositorios/hermes-agent-infra && git status --short`
Expected: only `docs/superpowers/specs/...` (already committed) shows as clean; `.gitignore` shows as a new untracked file ready to add.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "Add .gitignore"
```

---

### Task 2: `.env.example`

**Files:**
- Create: `.env.example`

- [ ] **Step 1: Write the file**

```dotenv
# hermes-agent-infra — environment reference for Coolify
#
# Do NOT put real values in this file and do NOT commit a populated .env.
# In Coolify, configure these under the resource's
# "Environment Variables" / "Secrets" tab instead. This file only documents
# what is required and what each variable does.

# --- Required: dashboard public URL -------------------------------------

# Public HTTPS URL Coolify assigns to this service, e.g. https://hermes.example.com
# Required so the dashboard's auth gate builds correct session/OAuth-callback
# URLs behind Coolify's reverse proxy. Must match the domain configured in
# Coolify exactly, scheme included.
HERMES_DASHBOARD_PUBLIC_URL=https://hermes.example.com

# --- Required: dashboard basic auth --------------------------------------
#
# The dashboard always requires an auth provider when bound to a
# non-loopback address (confirmed in Hermes Agent's own docs — there is no
# "expose without auth" option). Basic auth is enough for a personal,
# single-user deployment behind Coolify's HTTPS.

# Login username for the dashboard.
HERMES_DASHBOARD_BASIC_AUTH_USERNAME=replace-with-a-username

# Scrypt hash of the login password — never store the plaintext password.
# Generate with (replace TU_PASSWORD, then discard the plaintext):
#   docker run --rm nousresearch/hermes-agent:v2026.8.19 \
#     python -c "from plugins.dashboard_auth.basic import hash_password; print(hash_password('TU_PASSWORD'))"
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH=replace-with-generated-hash

# Signs the dashboard's session cookies.
# Generate with:
#   openssl rand -base64 48
HERMES_DASHBOARD_BASIC_AUTH_SECRET=replace-with-a-long-random-secret

# --- Notes on variables NOT in this file --------------------------------
#
# HERMES_DASHBOARD, HERMES_UID and HERMES_GID are fixed in
# docker-compose.yml for this deployment and do not need to be set here.
#
# No LLM provider API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.) are
# configured via environment variables in this deployment. The agent's
# model provider is configured post-deploy from the dashboard itself
# (Settings → Providers → Custom endpoint), pointed at OmniRoute's
# OpenAI-compatible endpoint. See README "Conectar OmniRoute como
# provider".

# --- Off-site backups (scripts/backup.sh, run on the host via cron) -----
#
# These are NOT read by the docker-compose.yml services — they configure
# scripts/backup.sh directly, which is invoked from a host crontab, not
# through Coolify. See README "Backups" for full setup instructions.
#
# Reuses the same Backblaze B2 bucket/repository as ai-gateway-infra's
# OmniRoute backups — restic distinguishes snapshots by --host and --tag,
# so a separate bucket isn't needed.

# restic repository target — the same bucket already used for OmniRoute:
#   b2:your-bucket-name:omniroute-backups
RESTIC_REPOSITORY=b2:your-bucket-name:omniroute-backups

# Encrypts the restic repository. A restic repository has exactly ONE
# password shared by everything backed up into it — if reusing OmniRoute's
# bucket, reuse the SAME password already generated for that repository.
# Generate a NEW one only if this is a fresh, separate repository:
#   openssl rand -base64 32
RESTIC_PASSWORD=replace-with-the-repository-password

# Backblaze B2 application key (same one used for OmniRoute's backups if
# reusing the bucket — needs read AND write/delete, see ai-gateway-infra
# README "Backups" for why).
B2_ACCOUNT_ID=replace-with-b2-key-id
B2_ACCOUNT_KEY=replace-with-b2-application-key
```

- [ ] **Step 2: Verify every variable referenced here also appears in docker-compose.yml or scripts/backup.sh**

This is a manual cross-check done after Tasks 3 and 4 are written — revisit this step then. No action yet.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "Add .env.example"
```

---

### Task 3: `docker-compose.yml`

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Write the file**

```yaml
# hermes-agent-infra — production deployment for Coolify
#
# Coolify treats this file as the source of truth: environment values are
# injected via Coolify's Environment Variables/Secrets UI (backed by the
# repo's .env.example as the reference), not committed here.
#
# The service is intentionally NOT bound to a host port. Coolify's proxy
# (Traefik) reaches containers over the internal Docker network, so
# publishing 9119:9119 would only add an unnecessary public exposure path.
# In Coolify, set the service's "Ports Exposes" value to 9119 and attach
# your domain — HTTPS/Let's Encrypt is then handled by Coolify
# automatically.
#
# Single container: Hermes Agent's built-in web dashboard
# (HERMES_DASHBOARD=1) replaces the separate hermes-webui container used
# previously — see
# docs/superpowers/specs/2026-08-26-hermes-agent-infra-design.md for why
# that combination broke and why this is the current recommended setup.

services:
  hermes:
    image: nousresearch/hermes-agent:v2026.8.19
    container_name: hermes
    command: gateway run
    restart: unless-stopped
    expose:
      - "9119"
    volumes:
      - hermes-data:/opt/data
    environment:
      HERMES_DASHBOARD: "1"
      HERMES_UID: "1000"
      HERMES_GID: "1000"

      # Public HTTPS URL of this deployment (e.g. https://hermes.example.com).
      # Required so the dashboard's auth gate builds correct
      # session/OAuth-callback URLs behind Coolify's reverse proxy.
      HERMES_DASHBOARD_PUBLIC_URL: ${HERMES_DASHBOARD_PUBLIC_URL:?HERMES_DASHBOARD_PUBLIC_URL must be set to your public HTTPS domain}

      # Dashboard auth (basic auth) — see .env.example for how to generate
      # the password hash. The dashboard refuses to bind to a non-loopback
      # address without an auth provider configured, so these are required.
      HERMES_DASHBOARD_BASIC_AUTH_USERNAME: ${HERMES_DASHBOARD_BASIC_AUTH_USERNAME:?HERMES_DASHBOARD_BASIC_AUTH_USERNAME must be set}
      HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH: ${HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH:?HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH must be set}
      HERMES_DASHBOARD_BASIC_AUTH_SECRET: ${HERMES_DASHBOARD_BASIC_AUTH_SECRET:?HERMES_DASHBOARD_BASIC_AUTH_SECRET must be set}
    healthcheck:
      # Mirrors the healthcheck the original Coolify template used for
      # this same image family (`test -d /home/hermes/.hermes || exit 1`):
      # confirms the persistent data directory exists and is mounted,
      # without depending on curl/wget being present in the image or
      # guessing at an unconfirmed HTTP health path.
      test: ["CMD-SHELL", "test -d /opt/data || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 30s
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  hermes-data:
    name: hermes-data
```

- [ ] **Step 2: Validate the compose file parses correctly**

Run:
```bash
cd /e/Repositorios/hermes-agent-infra
HERMES_DASHBOARD_PUBLIC_URL=https://hermes.example.com \
HERMES_DASHBOARD_BASIC_AUTH_USERNAME=test \
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH=test \
HERMES_DASHBOARD_BASIC_AUTH_SECRET=test \
docker compose config
```
Expected: prints the fully-resolved YAML with no errors (the `:?` guards would fail the command with a clear "must be set" message if any variable were missing — that failure mode is itself the thing being verified here, so also run once with the env vars unset and confirm it fails with the expected message).

Run (negative check):
```bash
cd /e/Repositorios/hermes-agent-infra && docker compose config
```
Expected: fails with `error while interpolating services.hermes.environment.HERMES_DASHBOARD_PUBLIC_URL: required variable HERMES_DASHBOARD_PUBLIC_URL is missing a value: HERMES_DASHBOARD_PUBLIC_URL must be set to your public HTTPS domain` (or equivalent for whichever required var docker-compose reports first).

- [ ] **Step 3: Cross-check `.env.example` against this file (completes Task 2 Step 2)**

Confirm every `${VAR}` referenced in `docker-compose.yml` (`HERMES_DASHBOARD_PUBLIC_URL`, `HERMES_DASHBOARD_BASIC_AUTH_USERNAME`, `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH`, `HERMES_DASHBOARD_BASIC_AUTH_SECRET`) has a matching entry in `.env.example`. All four are present — no action needed.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "Add docker-compose.yml for single-container Hermes Agent deployment"
```

---

### Task 4: `scripts/backup.sh`

**Files:**
- Create: `scripts/backup.sh`

- [ ] **Step 1: Write the file**

```bash
#!/usr/bin/env bash
# Backs up the hermes-data Docker volume to a restic repository
# (Backblaze B2 in production) and applies the retention policy. Runs
# entirely via the official restic/restic image — no host install of
# restic required.
#
# Required environment (see .env.example for the full reference):
#   RESTIC_REPOSITORY   e.g. b2:your-bucket-name:omniroute-backups
#                        (reuses the same bucket as ai-gateway-infra's
#                        OmniRoute backups — restic distinguishes
#                        snapshots by --host/--tag, see below)
#   RESTIC_PASSWORD     encrypts the repository — losing it means losing the backups
#   B2_ACCOUNT_ID
#   B2_ACCOUNT_KEY
#
# Optional environment:
#   HERMES_DATA_VOLUME   Docker volume to back up (default: hermes-data)
#   RESTIC_CACHE_VOLUME  Docker volume for restic's local cache (default: restic-cache)
#   RESTIC_IMAGE         restic image:tag to run (default: restic/restic:0.18.1)
#
# --host is pinned to "hermes-agent-backup" on every restic call below.
# This is required because each `docker run` gets a random container
# hostname, and restic groups retention (forget --keep-daily/--keep-weekly)
# by host — without a pinned host, forget would silently never prune
# anything across separate cron runs (same reasoning as
# ai-gateway-infra/scripts/backup.sh, which uses "omniroute-backup" for the
# same reason on the same repository).

set -euo pipefail

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY must be set (e.g. b2:bucket-name:omniroute-backups)}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD must be set}"
: "${B2_ACCOUNT_ID:?B2_ACCOUNT_ID must be set}"
: "${B2_ACCOUNT_KEY:?B2_ACCOUNT_KEY must be set}"

DATA_VOLUME="${HERMES_DATA_VOLUME:-hermes-data}"
CACHE_VOLUME="${RESTIC_CACHE_VOLUME:-restic-cache}"
RESTIC_IMAGE="${RESTIC_IMAGE:-restic/restic:0.18.1}"

run_restic() {
  docker run --rm \
    -v "${CACHE_VOLUME}:/root/.cache/restic" \
    -e RESTIC_REPOSITORY \
    -e RESTIC_PASSWORD \
    -e B2_ACCOUNT_ID \
    -e B2_ACCOUNT_KEY \
    "${RESTIC_IMAGE}" "$@"
}

echo "==> Backing up volume '${DATA_VOLUME}'"
docker run --rm \
  -v "${DATA_VOLUME}:/data:ro" \
  -v "${CACHE_VOLUME}:/root/.cache/restic" \
  -e RESTIC_REPOSITORY \
  -e RESTIC_PASSWORD \
  -e B2_ACCOUNT_ID \
  -e B2_ACCOUNT_KEY \
  "${RESTIC_IMAGE}" backup /data --tag hermes-data --host hermes-agent-backup

echo "==> Applying retention policy (7 daily, 4 weekly)"
run_restic forget --keep-daily 7 --keep-weekly 4 --prune --tag hermes-data --host hermes-agent-backup

echo "==> Current snapshots"
run_restic snapshots --tag hermes-data --host hermes-agent-backup
```

- [ ] **Step 2: Make it executable and check syntax**

Run:
```bash
cd /e/Repositorios/hermes-agent-infra
chmod +x scripts/backup.sh
bash -n scripts/backup.sh
```
Expected: `bash -n` prints nothing and exits 0 (no syntax errors).

- [ ] **Step 3: Verify the required-variable guards actually fail closed**

Run:
```bash
cd /e/Repositorios/hermes-agent-infra && ./scripts/backup.sh
```
Expected: fails immediately with `scripts/backup.sh: line X: RESTIC_REPOSITORY: RESTIC_REPOSITORY must be set (e.g. b2:bucket-name:omniroute-backups)` and exit code 1 — confirms `set -euo pipefail` plus the `:?` guards stop execution before any `docker run` happens when secrets are missing.

- [ ] **Step 4: Commit**

```bash
git add scripts/backup.sh
git commit -m "Add restic+B2 backup script for the hermes-data volume"
```

---

### Task 5: `README.md`

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write the file**

```markdown
# hermes-agent-infra — Hermes Agent en Coolify

Este repositorio es la fuente de verdad del despliegue de
[Hermes Agent](https://github.com/NousResearch/hermes-agent) (asistente
autónomo con memoria persistente, scheduler y dashboard web integrado) en
un servidor propio, gestionado por Coolify vía Docker Compose.

```
Git repository → Coolify → Docker Compose → hermes-agent (con dashboard)
```

Se despliega **un solo contenedor** — sin `hermes-webui` (el proyecto de
terceros usado en el template original de Coolify). Ver
[docs/superpowers/specs/2026-08-26-hermes-agent-infra-design.md](docs/superpowers/specs/2026-08-26-hermes-agent-infra-design.md)
para el porqué: `hermes-agent` trae desde la v0.16+ un dashboard web
integrado (FastAPI + React, puerto 9119, activado con `HERMES_DASHBOARD=1`)
que cubre la misma superficie que ofrecía `hermes-webui` — configuración,
API keys, MCP, mensajería, cron, sesiones, logs, skills — sin depender de
un segundo proyecto que se desincroniza de versión con el agente.

Repo hermano: [ai-gateway-infra](https://github.com/rodrigofanjul/ai-gateway-infra)
despliega OmniRoute en el mismo servidor — este repo asume que OmniRoute
ya está corriendo y lo usa como único proveedor de modelo (ver sección 9).

## 1. Arquitectura

```
                         HTTPS (Coolify / Traefik)
                                   │
                          hermes.tu-dominio.com
                                   │
                        ┌──────────────────────┐
                        │   contenedor hermes    │
                        │ nousresearch/          │
                        │ hermes-agent:X.Y.Z     │
                        │  dashboard  :9119      │
                        │  gateway    :8642      │
                        └──────────┬────────────┘
                                   │
                        volumen Docker persistente
                             hermes-data
                        (/opt/data: config, sesiones,
                         memoria, skills, credenciales,
                         cron, logs)
```

- El contenedor **no publica puertos al host** — Coolify enruta HTTPS al
  puerto del dashboard (`9119`) por la red interna de Docker (`expose`,
  no `ports`), igual que en OmniRoute.
- El puerto del gateway (`8642`, API OpenAI-compatible del propio Hermes)
  queda interno — no se expone en este compose. Si en el futuro necesitás
  pegarle desde otro servicio en el mismo servidor, se puede agregar a
  `expose` sin cambiar nada más.
- Imagen fijada a un tag de versión concreto
  (`nousresearch/hermes-agent:v2026.8.19`, confirmado con build `arm64`
  disponible), nunca `:latest` — ver sección 11 sobre actualizaciones.
- Toda la persistencia vive en `/opt/data` dentro del contenedor,
  respaldada por el volumen nombrado `hermes-data`.
- No se agregó `cap_drop: [ALL]` como en OmniRoute: el propio arranque del
  contenedor hace un `chown`/cambio de UID (`[stage2] Changing hermes UID
  to 1000` en los logs) que necesita privilegios que no se validaron bajo
  capacidades reducidas — a diferencia de OmniRoute, que sí se confirmó
  empíricamente que corre bien con `cap_drop: ALL`.

## 2. Requisitos

- Servidor con Coolify instalado (v4+) y un dominio apuntando a él por DNS.
- Acceso de Coolify a este repositorio Git (público o con credenciales
  configuradas en Coolify).
- [OmniRoute](https://github.com/rodrigofanjul/ai-gateway-infra) ya
  desplegado y accesible, con una API key generada — se usa como único
  proveedor de modelo para este agente (sección 9).

## 3. Variables de entorno

Ver [.env.example](.env.example) para la referencia completa con
comentarios. **No se commitea ningún `.env` real** — los valores reales se
cargan en Coolify (sección 4).

| Variable | Obligatoria | Propósito |
|---|---|---|
| `HERMES_DASHBOARD_PUBLIC_URL` | Sí | URL pública HTTPS completa (ej. `https://hermes.tu-dominio.com`). Necesaria para que el auth gate del dashboard arme bien las URLs de sesión/callback detrás de Coolify. |
| `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` | Sí | Usuario de login del dashboard. |
| `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH` | Sí | Hash scrypt de la contraseña (nunca texto plano) — ver comando de generación en `.env.example`. |
| `HERMES_DASHBOARD_BASIC_AUTH_SECRET` | Sí | Clave de firma de las cookies de sesión del dashboard. |

Variables fijas en `docker-compose.yml` (no hace falta tocarlas):
`HERMES_DASHBOARD=1`, `HERMES_UID=1000`, `HERMES_GID=1000`.

Este repo **no** carga API keys de proveedores de LLM (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, etc.) — el modelo se configura post-deploy apuntando a
OmniRoute (sección 9), no por variable de entorno.

Generación de secretos fuertes:

```bash
openssl rand -base64 48   # HERMES_DASHBOARD_BASIC_AUTH_SECRET
```

Generación del hash de password (reemplazá `TU_PASSWORD`, después
descartá el texto plano):

```bash
docker run --rm nousresearch/hermes-agent:v2026.8.19 \
  python -c "from plugins.dashboard_auth.basic import hash_password; print(hash_password('TU_PASSWORD'))"
```

## 4. Crear el recurso en Coolify desde este repositorio

1. En Coolify: **New Resource → Application → Docker Compose**, y
   seleccioná este repositorio Git (rama principal) como fuente.
2. Coolify detectará `docker-compose.yml` en la raíz. Confirmá que el
   servicio detectado sea `hermes`.
3. En la pestaña **Environment Variables / Secrets** del recurso, cargá:
   `HERMES_DASHBOARD_PUBLIC_URL`, `HERMES_DASHBOARD_BASIC_AUTH_USERNAME`,
   `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH`,
   `HERMES_DASHBOARD_BASIC_AUTH_SECRET` (marcalas como **Secret** salvo
   `HERMES_DASHBOARD_PUBLIC_URL`, que puede quedar en claro ya que no es
   sensible).
4. En la configuración del servicio, seteá **Ports Exposes** a `9119`
   (coincide con el `expose` del compose y con el puerto del dashboard).
5. Deploy. Coolify traerá la imagen `nousresearch/hermes-agent:v2026.8.19`
   y levantará el contenedor con el volumen `hermes-data`.

## 5. Configuración del dominio

En el recurso, pestaña **Domains**, asigná tu dominio, ej.
`hermes.tu-dominio.com`, apuntando al puerto `9119` (Coolify lo infiere
del "Ports Exposes" configurado en el paso anterior). Asegurate de que el
DNS (registro A/AAAA) de ese dominio apunte a la IP del servidor Coolify
**antes** de pedir el certificado.

## 6. HTTPS

Coolify emite y renueva automáticamente el certificado Let's Encrypt para
el dominio asignado (Traefik como proxy). No hay nada que configurar en
el `docker-compose.yml` para esto — por eso el contenedor solo expone el
puerto por red interna (`expose`, no `ports`).

## 7. Persistencia

Todo el estado de Hermes vive en el volumen Docker nombrado
`hermes-data`, montado en `/opt/data` dentro del contenedor: config
(`config.yaml`, `.env`), sesiones, **memoria persistente** (skills
aprendidos, contexto acumulado de conversaciones), credenciales, cron
jobs, logs.

Es más crítico de respaldar que un servicio sin estado: perder este
volumen no solo rompe la configuración, sino todo lo que el agente
"aprendió" con el uso — ver sección 10.

Un redeploy o upgrade de imagen **no borra este volumen** — Coolify
reutiliza volúmenes nombrados entre deployments del mismo recurso
mientras no borres el recurso o el volumen explícitamente.

## 8. Primer login

1. Entrá a `https://hermes.tu-dominio.com`.
2. Iniciá sesión con el usuario/password que configuraste en
   `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / el password que hasheaste
   para `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH`.
3. A diferencia de OmniRoute, no hay un "cambiar password luego del
   primer login" — el password vive en el hash que vos generaste y cargás
   en Coolify. Para cambiarlo, generá un hash nuevo (sección 3) y
   actualizá la variable en Coolify.

## 9. Conectar OmniRoute como provider

Desde el dashboard: **Settings → Providers → Custom endpoint**.

- Base URL: `https://router.tu-dominio.com/v1` (el endpoint OpenAI-compatible
  de tu deployment de OmniRoute, terminado en `/v1`).
- API key: la que generaste en el dashboard de OmniRoute (**API Keys →
  Create** en ese repo).
- Model identifier: el ID exacto que devuelve `GET /v1/models` de
  OmniRoute.

No hace falta ninguna variable de entorno para esto — queda guardado en
`config.yaml` dentro del volumen `hermes-data`, sobrevive a redeploys.

Conectar canales de mensajería (WhatsApp, Telegram, etc.) y configurar el
allowlist de usuarios permitidos también se hace desde el dashboard una
vez desplegado — no es configuración de infraestructura, así que no se
documenta paso a paso acá. Ver la
[documentación oficial de Hermes](https://hermes-agent.nousresearch.com/docs/)
para esa parte.

## 10. Backups

El volumen `hermes-data` puede perderse igual que cualquier disco de
servidor — para backups reales hace falta una copia fuera del servidor.

### Backup off-site a Backblaze B2 con restic

Este repo incluye [`scripts/backup.sh`](scripts/backup.sh), que respalda
el volumen `hermes-data` completo (config, sesiones, memoria persistente,
skills, credenciales, cron, logs) a un repositorio `restic` en Backblaze
B2, usando la imagen oficial `restic/restic` — no hace falta instalar
`restic` en el host.

**Reutiliza el mismo bucket que OmniRoute:** un repositorio restic
distingue snapshots por `--host` y `--tag` (acá: `hermes-agent-backup` /
`hermes-data`, vs. `omniroute-backup` / `omniroute-data` en
`ai-gateway-infra`), así que no hace falta un bucket B2 separado — solo
la misma `RESTIC_PASSWORD` que ya generaste para ese repositorio.

#### Setup inicial (si todavía no corriste el backup de OmniRoute en este servidor)

Seguí el mismo setup inicial documentado en
[ai-gateway-infra README, sección "Backups"](https://github.com/rodrigofanjul/ai-gateway-infra#13-backups)
— bucket B2, Application Key, y el archivo de entorno para el cron
(`/etc/omniroute-backup.env` ya sirve; podés agregarle las mismas
variables acá, o crear un `/etc/hermes-backup.env` separado si preferís
mantenerlos independientes).

Si ya lo corriste para OmniRoute, el repositorio restic ya existe — no
hace falta `restic init` de nuevo, solo apuntar este script al mismo
`RESTIC_REPOSITORY`/`RESTIC_PASSWORD`.

#### Backup manual / verificación

```bash
set -a; source /etc/hermes-backup.env; set +a
./scripts/backup.sh
```

#### Cron diario

```cron
0 4 * * * set -a; . /etc/hermes-backup.env; set +a; /ruta/a/hermes-agent-infra/scripts/backup.sh >> /var/log/hermes-backup.log 2>&1
```

(Corrido una hora después del cron de OmniRoute a las 3am, para no
competir por I/O si ambos vuelcan al mismo servidor B2 al mismo tiempo.)

Retención aplicada por el script: 7 snapshots diarios + 4 semanales
(`restic forget --keep-daily 7 --keep-weekly 4 --prune`).

#### Restore

Mismo procedimiento que en `ai-gateway-infra`, cambiando `--host
omniroute-backup` por `--host hermes-agent-backup`, `--tag omniroute-data`
por `--tag hermes-data`, y el volumen destino (`hermes-data` en vez de
`omniroute-data`). Ver
[ai-gateway-infra README, sección "Restore"](https://github.com/rodrigofanjul/ai-gateway-infra#restore)
para los comandos completos — la lógica es idéntica, solo cambian esos
tres nombres.

## 11. Actualización de Hermes Agent

La imagen está fijada a una versión concreta en `docker-compose.yml`
(`nousresearch/hermes-agent:v2026.8.19`) para evitar upgrades inesperados
en cada redeploy.

Dado el ritmo de releases de este proyecto (casi diario), la política de
actualización es:

- Revisión del [changelog de releases](https://github.com/NousResearch/hermes-agent/releases)
  cada 2-4 semanas, no por cada release individual.
- Excepción: un release marcado explícitamente como fix de seguridad se
  aplica de inmediato, sin esperar al ciclo.
- Antes de actualizar: leer las release notes desde la versión actual
  hasta la nueva buscando cambios de esquema o breaking changes, y
  confirmar que el volumen `hermes-data` tiene un backup reciente
  (sección 10).

Para actualizar:

1. Elegí la versión destino en el changelog. Preferí siempre un tag
   `vYYYY.M.D` (versión inmutable) — `:latest` sigue la versión estable
   más reciente pero puede cambiar sin que vos lo controles.
2. Editá `docker-compose.yml`, cambiando el tag de la imagen.
3. Commiteá y pusheá el cambio a este repositorio.
4. En Coolify, disparás un **Redeploy** del recurso (o queda automático si
   tenés auto-deploy on push habilitado).

El volumen `hermes-data` persiste entre versiones; no hace falta ninguna
migración manual salvo que el changelog lo indique explícitamente.

**Ventaja sobre el setup anterior:** al ser un solo contenedor con el
dashboard integrado, no existe el riesgo de desincronizar dos imágenes de
proyectos distintos (el problema raíz que rompió el deploy con
`hermes-webui`) — actualizar es cambiar un solo tag.

## 12. Rollback

1. Editá `docker-compose.yml` y volvé al tag de imagen anterior conocido
   como estable.
2. Commiteá ese cambio (`git revert` del commit de upgrade es la forma
   más trazable).
3. Redeploy en Coolify.

Como el volumen de datos no se recrea entre deploys, un rollback de
imagen no pierde configuración ni memoria — solo revertí la imagen si
sabés que la versión anterior es compatible con el esquema de datos
actual.

## 13. Troubleshooting básico

**El dashboard no carga / 502 desde Coolify**
- Verificá que el contenedor esté `healthy` en Coolify (usa el
  healthcheck definido en `docker-compose.yml`, que confirma que
  `/opt/data` está montado).
- Revisá logs: en Coolify, pestaña **Runtime Logs** del recurso, o
  `docker logs -f hermes` en el servidor.

**El dashboard responde 401/403 constante o no deja loguear**
- Confirmá que `HERMES_DASHBOARD_PUBLIC_URL` coincide exactamente con el
  dominio HTTPS público que Coolify te asignó (mismo esquema, mismo host,
  sin barra final).
- Confirmá que `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH` es un hash
  generado con el comando de la sección 3, no la contraseña en texto
  plano.

**Perdí el hash de password / no puedo loguear**
- No hay "recuperar contraseña" — generá un hash nuevo (sección 3),
  actualizá `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH` en Coolify, y
  redeploy.

**`exec format error` en los logs del contenedor**
- Problema de arquitectura de CPU: la imagen no es compatible con el
  procesador del servidor (típicamente amd64 vs arm64 en un VPS Ampere).
  Confirmá la arquitectura del servidor con `uname -m` y asegurate de que
  el tag de imagen usado tenga build para esa arquitectura — los tags de
  `nousresearch/hermes-agent` desde mediados de 2026 son multi-arch
  (`amd64`+`arm64`), pero digests pinneados a mano pueden no serlo.

**Referencia histórica: por qué no usamos `hermes-webui`**
- El template de Coolify original combinaba `hermes-agent` con
  [`hermes-webui`](https://github.com/nesquena/hermes-webui) (proyecto de
  terceros). Un cambio en `hermes-agent` que prohíbe compilarlo como
  wheel/sdist fuera de sus instaladores oficiales rompió el proceso de
  arranque de `hermes-webui`, que sí intentaba compilarlo así. El fix
  ([nesquena/hermes-webui#6458](https://github.com/nesquena/hermes-webui/pull/6458))
  solo se publicó en el track experimental del proyecto, nunca se
  backporteó a un tag estable — por eso se abandonó esa combinación en
  favor del dashboard integrado de `hermes-agent`. Ver
  [docs/superpowers/specs/2026-08-26-hermes-agent-infra-design.md](docs/superpowers/specs/2026-08-26-hermes-agent-infra-design.md)
  para el detalle completo.
```

- [ ] **Step 2: Verify all internal links resolve to files that exist in this repo**

Run: `grep -o '\[.*\](\(docs/[^)]*\|scripts/[^)]*\|\.env\.example\)' README.md`
Expected output includes exactly these repo-relative paths, all of which must exist on disk:
- `docs/superpowers/specs/2026-08-26-hermes-agent-infra-design.md` (created in the brainstorming phase)
- `scripts/backup.sh` (Task 4)
- `.env.example` (Task 2)

Confirm with: `ls docs/superpowers/specs/2026-08-26-hermes-agent-infra-design.md scripts/backup.sh .env.example`
Expected: all three paths print without "No such file or directory".

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Add README with deployment, backup, update and troubleshooting docs"
```

---

### Task 6: Final repo verification

**Files:** none created — this task only runs checks across the files from Tasks 1-5.

- [ ] **Step 1: Re-validate the compose file end-to-end**

Run:
```bash
cd /e/Repositorios/hermes-agent-infra
HERMES_DASHBOARD_PUBLIC_URL=https://hermes.example.com \
HERMES_DASHBOARD_BASIC_AUTH_USERNAME=test \
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH=test \
HERMES_DASHBOARD_BASIC_AUTH_SECRET=test \
docker compose config --quiet
```
Expected: exit code 0, no output (valid config).

- [ ] **Step 2: Confirm the backup script is still syntactically valid and executable**

Run: `bash -n scripts/backup.sh && test -x scripts/backup.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: Confirm git history is clean and complete**

Run: `git log --oneline`
Expected: 6 commits total — the design spec commit (already existed before this plan) plus one commit per Task 1-5 (`.gitignore`, `.env.example`, `docker-compose.yml`, `scripts/backup.sh`, `README.md`).

Run: `git status --short`
Expected: empty (nothing uncommitted).

- [ ] **Step 4: No commit needed — this task only verifies prior commits**

---

## Plan Self-Review Notes

- **Spec coverage:** Architecture (Task 3), env vars (Task 2/3), persistence + backups (Task 4, README §7/§10), update policy (README §11), README structure with all 13 sections from the spec (Task 5), troubleshooting note about the historical `hermes-webui` issue (README §13) — all spec sections have a corresponding task/section.
- **Deliberately out of scope, per spec:** no data migration task (none existed to migrate), no WhatsApp/messaging step-by-step task (README §9 links out instead), no OAuth/OIDC dashboard task (basic auth only, per spec decision).
- **Type/name consistency checked:** `HERMES_DASHBOARD_PUBLIC_URL`, `HERMES_DASHBOARD_BASIC_AUTH_USERNAME`, `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH`, `HERMES_DASHBOARD_BASIC_AUTH_SECRET` are spelled identically across `.env.example`, `docker-compose.yml`, and `README.md`. Volume name `hermes-data` and restic `--host hermes-agent-backup` / `--tag hermes-data` are consistent across `docker-compose.yml`, `scripts/backup.sh`, and `README.md`.
