# hermes-agent-infra — Hermes Agent en Coolify

Este repositorio es la fuente de verdad del despliegue de
[Hermes Agent](https://github.com/NousResearch/hermes-agent) (asistente
autónomo con memoria persistente, scheduler y dashboard web integrado) en
un servidor propio, gestionado por Coolify vía Docker Compose.

```
Git repository → Coolify → Docker Compose → hermes-agent + hermes-webui
```

Se despliegan **dos contenedores**: `hermes-agent` (gateway + dashboard
integrado) y [`hermes-webui`](https://github.com/nesquena/hermes-webui)
(chat web de terceros, más completo que el dashboard integrado — tareas,
kanban, skills, memoria, archivos). El dominio público apunta a
`hermes-webui`.

Este repo pasó primero por una versión **de un solo contenedor** (sin
`hermes-webui`) por un bug de compatibilidad real entre ambos proyectos —
ver
[docs/superpowers/specs/2026-08-26-hermes-agent-infra-design.md](docs/superpowers/specs/2026-08-26-hermes-agent-infra-design.md).
`hermes-webui` se reincorporó después, deliberadamente, fijado a una
versión verificada que incluye el fix — ver sección 13 "Referencia
histórica" para el detalle completo y por qué importa al actualizar.

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
                        │  contenedor hermes-webui │
                        │ ghcr.io/nesquena/        │
                        │ hermes-webui:0.52.264    │
                        │  chat/tasks/kanban :8787 │
                        └──────────┬───────────────┘
                                   │ (red interna Docker)
                        ┌──────────┴───────────────┐
                        │   contenedor hermes        │
                        │ nousresearch/               │
                        │ hermes-agent:v2026.8.27      │
                        │  dashboard :9119 (sin dominio)│
                        │  gateway   :8642 (interno)    │
                        └──────────┬───────────────────┘
                                   │
                  volúmenes Docker persistentes compartidos
        hermes-data (config, sesiones, memoria, skills, credenciales,
                      cron, logs) · hermes-agent-src (código del agente,
                      solo lectura para webui) · hermes-workspace
```

- Ningún contenedor publica puertos al host — Coolify enruta HTTPS al
  puerto de `hermes-webui` (`8787`) por la red interna de Docker
  (`expose`, no `ports`), igual que en OmniRoute.
- El dashboard integrado de `hermes-agent` (`9119`) y el gateway API
  (`8642`) quedan internos, sin dominio asignado — ver sección "Acceso al
  dashboard integrado" más abajo si necesitás entrar directo.
- Imagen del agente fijada a un tag concreto
  (`nousresearch/hermes-agent:v2026.8.27`, versión de paquete `0.20.6`,
  `arm64`+`amd64`). Imagen de
  `hermes-webui` fijada a `0.52.264` — es un tag del track
  **experimental** del proyecto, no del track "estable" (`vX.Y.Z`), porque
  el track estable todavía no absorbió el fix de compatibilidad que este
  setup necesita (ver sección 13). Esperá más ruido de versión que con un
  tag estable — sección 11 documenta la política de revisión.
- `hermes-data` es el volumen crítico: config (`config.yaml`, `.env`),
  sesiones, **memoria persistente**, skills, credenciales, cron, logs. Lo
  montan ambos contenedores (en rutas distintas — a Docker no le importa,
  es el mismo volumen).
- `hermes-agent-src` expone el código fuente del agente para que
  `hermes-webui` instale sus dependencias Python al arrancar — montado
  de solo lectura en `hermes-webui`. **Importante para actualizaciones**:
  ver sección 11, esta es la causa del bug histórico si no se recrea al
  subir de versión el agente.
- No se agregó `cap_drop: [ALL]` como en OmniRoute: el arranque de
  `hermes-agent` hace un `chown`/cambio de UID que necesita privilegios
  no validados bajo capacidades reducidas.

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
| `HERMES_DASHBOARD_PUBLIC_URL` | Sí | URL pública HTTPS completa (ej. `https://hermes.tu-dominio.com`). El dashboard integrado la necesita para su auth gate aunque el dominio público ahora enrute a `hermes-webui`. |
| `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` | Sí | Usuario de login del dashboard integrado. |
| `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH` | Sí | Hash scrypt de la contraseña del dashboard integrado (nunca texto plano) — ver comando de generación abajo. |
| `HERMES_DASHBOARD_BASIC_AUTH_SECRET` | Sí | Clave de firma de las cookies de sesión del dashboard integrado. |
| `API_SERVER_KEY` | Sí | Habilita el gateway API (puerto 8642, interno) y lo comparten agent+webui — sin esto, `hermes-webui` reporta "Gateway endpoint not reachable" (no bloqueante, pero pierde Tasks/cron). Mínimo 16 caracteres. |
| `HERMES_WEBUI_PASSWORD` | Sí | Login propio de `hermes-webui` — un único password, sin usuario, distinto del basic auth del dashboard integrado. |

Variables fijas en `docker-compose.yml` (no hace falta tocarlas):
`HERMES_DASHBOARD=1`, `HERMES_UID=1000`, `HERMES_GID=1000`,
`API_SERVER_ENABLED=true`, `API_SERVER_HOST=0.0.0.0`.

**Basic auth del dashboard integrado no es HTTP Basic Auth clásico** (el
de `curl -u`): sin sesión, cualquier request se redirige a una página de
login (`/login`) con un formulario real; al enviar usuario/password
correctos, el backend responde con cookies de sesión `HttpOnly` —
confirmado probando el contenedor real, incluyendo que un password
incorrecto devuelve `401 Unauthorized`. Soporta también OAuth de Nous
Research y OIDC self-hosted si más adelante se necesita algo más robusto
— ver la
[documentación oficial del dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard).

**`hermes-webui` tampoco usa HTTP Basic Auth** — es un password único (sin
usuario) enviado vía un formulario de login propio.

Este repo **no** carga API keys de proveedores de LLM (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, etc.) — el modelo se configura post-deploy editando
`config.yaml` directamente (sección 9), no por variable de entorno ni
desde ninguna de las dos UIs web.

Generación de secretos fuertes:

```bash
openssl rand -base64 48   # HERMES_DASHBOARD_BASIC_AUTH_SECRET
openssl rand -base64 24   # API_SERVER_KEY
openssl rand -base64 18   # HERMES_WEBUI_PASSWORD
```

Generación del hash de password del dashboard integrado (reemplazá
`TU_PASSWORD`, después descartá el texto plano). Este comando bootea todo
el gateway (sincroniza 82 skills, ~10-15s de logs de arranque ruidosos)
antes de imprimir el hash en su propia línea justo antes de apagarse —
es esperable, no es que se colgó. En Windows usá Git Bash para el `\` de
continuación de línea (PowerShell necesita `` ` `` en su lugar):

```bash
docker run --rm nousresearch/hermes-agent:v2026.8.27 \
  python -c "from plugins.dashboard_auth.basic import hash_password; print(hash_password('TU_PASSWORD'))"
```

**IMPORTANTE — escapar cada `$` como `$$`** al pegar el hash en Coolify.
Ver sección 13 "Contraseña incorrecta con el password correcto" para el
detalle completo de por qué y cómo verificarlo.

## 4. Crear el recurso en Coolify desde este repositorio

1. En Coolify: **New Resource → Application → Docker Compose**, y
   seleccioná este repositorio Git (rama principal) como fuente.
2. Coolify detectará `docker-compose.yml` en la raíz, con dos servicios:
   `hermes` y `hermes-webui`.
3. En la pestaña **Environment Variables / Secrets** del recurso, cargá
   las 6 variables de la sección 3 (marcalas como **Secret** salvo
   `HERMES_DASHBOARD_PUBLIC_URL`, que puede quedar en claro).
4. En la configuración del servicio `hermes-webui`, seteá **Ports
   Exposes** a `8787`.
5. Deploy. Coolify trae ambas imágenes y levanta los tres volúmenes
   (`hermes-data`, `hermes-agent-src`, `hermes-workspace`).

## 5. Configuración del dominio

En el recurso, pestaña **Domains**, asigná tu dominio al servicio
`hermes-webui`, ej. `hermes.tu-dominio.com`, apuntando al puerto `8787`.
Asegurate de que el DNS (registro A/AAAA) de ese dominio apunte a la IP
del servidor Coolify **antes** de pedir el certificado. El servicio
`hermes` (dashboard integrado) queda sin dominio asignado a propósito.

## 6. HTTPS

Coolify emite y renueva automáticamente el certificado Let's Encrypt para
el dominio asignado (Traefik como proxy). No hay nada que configurar en
el `docker-compose.yml` para esto.

## 7. Persistencia

Todo el estado vive en el volumen Docker nombrado `hermes-data`,
compartido por ambos contenedores: config (`config.yaml`, `.env`),
sesiones, **memoria persistente** (skills aprendidos, contexto acumulado
de conversaciones), credenciales, cron jobs, logs. `hermes-agent-src`
guarda el código fuente del agente (necesario para que `hermes-webui`
arranque — ver sección 11 sobre por qué esto importa al actualizar).
`hermes-workspace` es el directorio de archivos que se navega/edita desde
`hermes-webui`.

Es más crítico de respaldar que un servicio sin estado: perder
`hermes-data` no solo rompe la configuración, sino todo lo que el agente
"aprendió" con el uso — ver sección 10.

Un redeploy o upgrade de imagen **no borra estos volúmenes** — Coolify
los reutiliza entre deployments del mismo recurso, con la excepción
documentada en la sección 11 para `hermes-agent-src` al subir de versión
`hermes-agent`.

## 8. Primer login

**Dashboard integrado** (sin dominio público, ver "Acceso al dashboard
integrado" abajo si necesitás entrar): usuario/password que configuraste
en `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / el que hasheaste para
`HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH`.

**hermes-webui** (`https://hermes.tu-dominio.com`): un único campo de
password — el valor de `HERMES_WEBUI_PASSWORD`, sin usuario.

Ninguno de los dos tiene "cambiar password luego del primer login" — para
cambiarlo, regenerá el valor (hash o password según corresponda) y
actualizalo en Coolify.

### Acceso al dashboard integrado (sin dominio propio)

El dashboard integrado (puerto 9119) queda sin dominio público a
propósito, para no duplicar UIs expuestas a internet. Si necesitás
entrar directo (por ejemplo, para editar `config.yaml` a mano — sección
9), hacelo por túnel SSH:

```bash
ssh -N -L 9119:127.0.0.1:9119 usuario@tu-servidor
```

Y abrí `http://localhost:9119` en tu navegador. El gateway API (8642)
puede tunelearse de la misma forma si lo necesitás.

## 9. Conectar OmniRoute como provider

**Confirmado empíricamente contra un deployment real: ninguna de las dos
UIs web permite configurar un endpoint custom.** El selector "Custom
endpoint" del dashboard integrado no tiene campos de base URL/API key
(solo dice `run 'hermes model' to configure`), y la lista de Providers de
`hermes-webui` directamente no incluye una opción de endpoint custom —
es un gap conocido y todavía abierto en el propio proyecto
([NousResearch/hermes-agent#42042](https://github.com/NousResearch/hermes-agent/issues/42042)).
Tampoco funciona editar `config.yaml` a través del exportador/importador
JSON del dashboard integrado — se probó y corrompe la estructura
silenciosamente (el campo `model` es un objeto anidado en el archivo
real, no el string plano que expone ese JSON).

**Lo que sí funciona, verificado end-to-end con un mensaje real
respondido a través de OmniRoute:** editar `config.yaml` directamente.

1. Conseguí una API key de OmniRoute (dashboard de OmniRoute → **API
   Keys → Create**) y confirmá el/los model ID que querés usar con:
   ```bash
   curl https://router.tu-dominio.com/v1/models \
     -H "Authorization: Bearer TU_API_KEY_OMNIROUTE"
   ```
2. Entrá a una terminal con acceso al contenedor `hermes` — la pestaña
   **Terminal** del recurso en Coolify sirve.
3. Localizá el bloque `model:` en `/opt/data/config.yaml` (cerca de la
   línea 34 en una instalación nueva; buscá `^model:` si cambió) y editá
   estas tres líneas dentro de ese bloque (dejá el resto de los
   comentarios como están):
   ```yaml
   model:
     default: "TU-MODEL-ID"           # ej. "cc/claude-sonnet-5"
     provider: "custom"
     base_url: "https://router.tu-dominio.com/v1"
     api_key: "TU_API_KEY_OMNIROUTE"
   ```
   Con `sed`, sin abrir un editor interactivo (ajustá los números de
   línea si tu archivo difiere — confirmalos primero con
   `grep -n '^model:\|default:\|provider: "auto"\|base_url: "https://openrouter' /opt/data/config.yaml`):
   ```bash
   sed -i \
     -e 's|default: "anthropic/claude-opus-4.6"|default: "TU-MODEL-ID"|' \
     -e 's|provider: "auto"|provider: "custom"|' \
     -e 's|base_url: "https://openrouter.ai/api/v1"|base_url: "https://router.tu-dominio.com/v1"\n  api_key: "TU_API_KEY_OMNIROUTE"|' \
     /opt/data/config.yaml
   ```
4. Reiniciá el gateway para que tome el cambio — desde el dashboard
   integrado (botón **Restart Gateway**) o desde `hermes-webui`
   (`Hermes Dashboard` en el menú lateral tiene el mismo botón), o con
   `docker restart hermes hermes-webui` si tenés acceso al host
   directamente.
5. Probá con un mensaje real en el chat de `hermes-webui` y confirmá que
   la respuesta muestra el model ID esperado debajo del mensaje.

Conectar canales de mensajería (WhatsApp, Telegram, etc.) y configurar el
allowlist de usuarios permitidos se hace desde cualquiera de las dos UIs
una vez desplegado — no es configuración de infraestructura, así que no
se documenta paso a paso acá. Ver la
[documentación oficial de Hermes](https://hermes-agent.nousresearch.com/docs/)
para esa parte.

## 10. Backups

Los volúmenes pueden perderse igual que cualquier disco de servidor —
para backups reales hace falta una copia fuera del servidor.

### Backup off-site a Backblaze B2 con restic

Este repo incluye [`scripts/backup.sh`](scripts/backup.sh), que respalda
el volumen `hermes-data` (config, sesiones, memoria persistente, skills,
credenciales, cron, logs — el volumen con estado real; `hermes-agent-src`
y `hermes-workspace` no se respaldan, son reproducibles/no críticos) a un
repositorio `restic` en Backblaze B2, usando la imagen oficial
`restic/restic` — no hace falta instalar `restic` en el host.

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

## 11. Actualización

Ambas imágenes están fijadas a versiones concretas en
`docker-compose.yml` para evitar upgrades inesperados en cada redeploy.

Dado el ritmo de releases de ambos proyectos (casi diario, más aún en el
track experimental de `hermes-webui`), la política de actualización es:

- Revisión de changelog cada 2-4 semanas, no por cada release individual
  ([hermes-agent](https://github.com/NousResearch/hermes-agent/releases),
  [hermes-webui](https://github.com/nesquena/hermes-webui/releases)).
- Excepción: un release marcado explícitamente como fix de seguridad se
  aplica de inmediato, sin esperar al ciclo.
- Antes de actualizar: leer las release notes desde la versión actual
  hasta la nueva buscando cambios de esquema o breaking changes, y
  confirmar que `hermes-data` tiene un backup reciente (sección 10).

### ⚠️ Al subir de versión `hermes-agent`: recrear `hermes-agent-src`

**Esto es obligatorio, no opcional — es la causa raíz de un bug real que
este repo ya sufrió dos veces.** El volumen `hermes-agent-src` se puebla
desde `/opt/hermes` de la imagen del agente **solo la primera vez** que
se levanta, y Docker lo reutiliza tal cual en corridas posteriores —
**incluso después de bajar una imagen más nueva**. Está confirmado tanto
empíricamente en este repo como en la documentación oficial de
`hermes-webui` (`docs/docker.md`, comentario en su
`docker-compose.two-container.yml`). Si no se recrea, `hermes-webui`
sigue viendo el código fuente de la versión vieja del agente — en el
mejor caso funciona igual, en el peor rompe con errores crípticos (un
`exec: entrypoint-dispatch.sh: no such file or directory` fue el síntoma
la primera vez que esto pasó acá).

Procedimiento completo para actualizar `hermes-agent`:

1. Elegí la versión destino en el changelog. Preferí siempre un tag
   `vYYYY.M.D` (versión inmutable) — `:latest` puede cambiar sin que vos
   lo controles.
2. Editá `docker-compose.yml`, cambiando el tag de imagen de `hermes`.
3. Commiteá y pusheá el cambio a este repositorio.
4. **Antes o inmediatamente después del redeploy**, borrá el volumen
   `hermes-agent-src` para forzar su repoblado desde la imagen nueva. En
   la Terminal del servidor (no la del contenedor):
   ```bash
   docker compose -f /ruta/al/proyecto/docker-compose.yml stop
   docker volume rm hermes-agent-src
   docker compose -f /ruta/al/proyecto/docker-compose.yml up -d
   ```
   O, si preferís hacerlo desde Coolify: parar el recurso, borrar el
   volumen desde la pestaña **Persistent Storage**, y volver a arrancar.
5. Confirmá en **Runtime Logs** de `hermes-webui` que el arranque
   instala las dependencias sin el error de "Building wheels or sdists...
   is not supported" (si aparece, `hermes-webui` quedó desalineado con
   la nueva versión del agente — ver sección 13 y buscar un tag de
   `hermes-webui` más reciente que incluya el fix correspondiente).

`hermes-data` **sí** persiste normalmente entre versiones del agente
(config, memoria, sesiones) — el paso de arriba es específico de
`hermes-agent-src`.

### Actualizar `hermes-webui`

Mismo patrón de tag fijo. Al elegir un nuevo tag, confirmá que incluye
cualquier fix relevante comparándolo contra el commit del fix en GitHub
(`git compare <fix-commit>...<tag>` en el repo de `hermes-webui` — si
`behind` da `0`, lo incluye). No hace falta tocar `hermes-agent-src` al
actualizar solo `hermes-webui` (ese volumen depende de la versión del
agente, no de la webui).

### Configurar compresión de contexto (`tail_mode: lean`)

Desde `v2026.8.27`/`0.20.6`, `lean` es el `tail_mode` por defecto en
`hermes-agent` (antes era `legacy`) — igual, este repo fija los valores
explícitamente en vez de depender del default, para que una futura
versión que cambie el default no cambie silenciosamente este deployment.
Se aplica con `hermes config set` (edita `/opt/data/config.yaml` sin
tocar el resto del archivo) — no reescribas `config.yaml` a mano para
esto. El comando es idempotente: correrlo varias veces con el mismo valor
no duplica ni corrompe nada, solo reescribe la misma clave.

Desde la Terminal del servidor (o la pestaña **Terminal** del recurso
`hermes` en Coolify):

```bash
docker exec hermes hermes config set compression.enabled true
docker exec hermes hermes config set compression.tail_mode lean
docker exec hermes hermes config set compression.threshold 0.50
docker exec hermes hermes config set compression.in_place true
docker exec hermes hermes config set compression.progress_notices false
docker exec hermes hermes config set compression.idle_compact_after_seconds 0
```

Reiniciá el gateway para que tome el cambio (botón **Restart Gateway** en
cualquiera de las dos UIs, o `docker restart hermes hermes-webui`), y
confirmá con:

```bash
docker exec hermes hermes config get compression
```

**No se tocan** `compression.micro_compact`,
`compression.proactive_prune_tokens` ni
`compression.codex_responses_native` — quedan en su default (`False`/`0`)
salvo que haya evidencia concreta de que hace falta activarlos.
Tampoco se toca `model.default`, `model.provider`, la longitud de
contexto, el modelo auxiliar de compresión, ni
`whatsapp.reply_prefix` (debe seguir vacío).

## 12. Rollback

1. Editá `docker-compose.yml` y volvé al tag de imagen anterior conocido
   como estable. Rollback exacto para el upgrade a `v2026.8.27`:
   ```bash
   git revert <commit-del-upgrade>   # o edición manual del tag a v2026.8.19
   ```
   equivalente manual: cambiar `image: nousresearch/hermes-agent:v2026.8.27`
   de vuelta a `image: nousresearch/hermes-agent:v2026.8.19` en
   [docker-compose.yml](docker-compose.yml).
2. Si el rollback es de `hermes-agent`, aplicá también el paso 4 de la
   sección 11 (recrear `hermes-agent-src`) — el mismo problema de volumen
   stale aplica en cualquier dirección.
3. Commiteá ese cambio (`git revert` del commit de upgrade es la forma
   más trazable).
4. Redeploy en Coolify.

`hermes-data` no se recrea entre deploys, así que un rollback no pierde
configuración ni memoria — solo revertí la imagen si sabés que la versión
anterior es compatible con el esquema de datos actual. La configuración
de compresión de la sección anterior vive en `config.yaml` dentro de
`hermes-data`, así que sobrevive a un rollback de imagen sin acción
adicional (`v0.20.5` ya soporta `tail_mode: lean` como opción, solo no
lo trae como default).

## 13. Troubleshooting básico

**El sitio no carga / 502 desde Coolify**
- Verificá que ambos contenedores estén `healthy` en Coolify.
- Revisá logs: en Coolify, pestaña **Runtime Logs** del recurso, o
  `docker logs -f hermes` / `docker logs -f hermes-webui` en el servidor.

**`hermes-webui` responde "Building wheels or sdists for hermes-agent is
not supported" en un loop de crash**
- El tag de `hermes-webui` que estás usando no incluye el fix de
  [nesquena/hermes-webui#6458](https://github.com/nesquena/hermes-webui/pull/6458).
  Confirmá con `git compare 705b75c16c31ad0cbbad194787801aaa0ccc060c...<tu-tag>`
  en el repo de `hermes-webui` (`behind: 0` = lo incluye). El track
  "estable" (`vX.Y.Z`) todavía no lo absorbió al 2026-08-26 — usá un tag
  del track experimental (`exp-vX.Y.Z` en git / `X.Y.Z` sin prefijo en
  Docker Hub/ghcr.io) que sí lo incluya.
- Si el tag sí incluye el fix y el error persiste de todas formas, es
  casi seguro el volumen `hermes-agent-src` stale — ver sección 11.

**"Gateway endpoint not reachable" en `hermes-webui`**
- Confirmá que `API_SERVER_KEY` está seteada en Coolify (mínimo 16
  caracteres) y que coincide entre lo que recibe `hermes` (env
  `API_SERVER_KEY`) y `hermes-webui` (env `HERMES_WEBUI_GATEWAY_API_KEY`)
  — en este compose ambas leen la misma variable, así que si falta en
  Coolify, falta en los dos a la vez. No es un error fatal — Tasks/cron
  del lado de la UI dejan de funcionar, el chat normal sigue andando.

**El dashboard integrado o `hermes-webui` responden 401/403 constante o
no dejan loguear**
- Confirmá que `HERMES_DASHBOARD_PUBLIC_URL` coincide exactamente con el
  dominio HTTPS que le corresponde (mismo esquema, mismo host, sin barra
  final).
- Confirmá que `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH` es un hash
  generado con el comando de la sección 3, no la contraseña en texto
  plano.

**Perdí el hash de password del dashboard integrado / la password de
`hermes-webui`, no puedo loguear**
- No hay "recuperar contraseña" en ninguno de los dos — regenerá el
  valor correspondiente (sección 3), actualizalo en Coolify, y redeploy.

**"Contraseña incorrecta" con el password correcto (dashboard integrado)**
- Confirmado empíricamente: Coolify aplica interpolación estilo Compose
  (`$VAR`) al valor de `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH` antes
  de inyectarlo al contenedor. El hash (`scrypt$N$r$p$salt$digest`) tiene
  varios segmentos separados por `$` — cualquier segmento que empiece con
  una **letra** justo después del `$` (típicamente el salt, en base64) se
  interpreta como una variable inexistente y se borra silenciosamente; los
  segmentos que empiezan con **dígito** (los parámetros de costo N/r/p)
  sobreviven porque ninguna variable puede empezar con un número — por
  eso solo desaparece una parte del hash, no todo, y es fácil no notarlo.
  **Fix:** al pegar el hash en Coolify, duplicá cada `$` como `$$` (escape
  estándar de Compose para un `$` literal). Verificá en la pestaña
  **Terminal** del recurso:
  ```bash
  echo "$HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH"
  ```
  Tiene que imprimir el hash completo con `$` simples, idéntico al que
  generaste — si falta algún segmento, todavía te falta escapar algún `$`.

**`exec format error` en los logs del contenedor**
- Problema de arquitectura de CPU: la imagen no es compatible con el
  procesador del servidor (típicamente amd64 vs arm64 en un VPS Ampere).
  Confirmá la arquitectura del servidor con `uname -m` y asegurate de que
  el tag de imagen usado tenga build para esa arquitectura. Verificado
  para ambas imágenes de este compose: `nousresearch/hermes-agent` es
  multi-arch desde mediados de 2026, y `ghcr.io/nesquena/hermes-webui`
  publica `amd64`+`arm64` en sus tags recientes — pero digests pinneados
  a mano (en vez de tags) pueden no serlo.

**Configurar el provider (OmniRoute) no aparece en ninguna UI web**
- Es esperado, no un bug de este repo — ver sección 9 completa para el
  método que sí funciona (editar `config.yaml` directo).

**Referencia histórica: la ida y vuelta con `hermes-webui`**
- El template de Coolify original combinaba `hermes-agent` con
  `hermes-webui`. Un cambio en `hermes-agent` que prohíbe compilarlo como
  wheel/sdist fuera de sus instaladores oficiales rompió el arranque de
  `hermes-webui`, que intentaba compilarlo así. El fix
  ([nesquena/hermes-webui#6458](https://github.com/nesquena/hermes-webui/pull/6458))
  solo se había publicado en el track experimental, nunca backporteado a
  un tag estable — por eso este repo pasó primero a un único contenedor
  (solo `hermes-agent` con su dashboard integrado). Después se confirmó
  que **ninguna de las dos UIs web** (ni el dashboard integrado ni
  `hermes-webui`) soporta configurar un provider custom como OmniRoute
  desde la interfaz — es un gap abierto del propio proyecto Hermes, no
  algo que `hermes-webui` resolviera. Aun así, se decidió reincorporar
  `hermes-webui` (fijado a un tag `0.52.264` verificado que sí incluye el
  fix #6458) porque ofrece una experiencia de chat día a día
  significativamente mejor que el dashboard integrado — la configuración
  del provider se resuelve igual en ambos casos, editando `config.yaml`
  a mano (sección 9). Ver
  [docs/superpowers/specs/2026-08-26-hermes-agent-infra-design.md](docs/superpowers/specs/2026-08-26-hermes-agent-infra-design.md)
  para el diseño original de un solo contenedor.
