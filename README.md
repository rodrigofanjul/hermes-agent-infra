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

Se eligió basic auth por ser suficiente para un uso personal detrás del
HTTPS que ya provee Coolify/Traefik. El dashboard también soporta OAuth
de Nous Research y OIDC self-hosted (Keycloak, Authelia, etc.) si más
adelante se necesita algo más robusto — no están configurados en este
repo, ver la [documentación oficial del dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard)
para esas alternativas.

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
