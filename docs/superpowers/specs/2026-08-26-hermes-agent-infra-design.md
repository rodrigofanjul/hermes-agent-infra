# hermes-agent-infra — Hermes Agent en Coolify

## Contexto

Reemplaza el recurso de Coolify `hermes-agent-with-webui` (template de dos
contenedores: `nousresearch/hermes-agent` + `ghcr.io/nesquena/hermes-webui`),
que se dio de baja por un bug de compatibilidad no resuelto en el track
estable de `hermes-webui` (el build de `hermes-webui` intenta compilar el
código fuente de `hermes-agent` como wheel, algo que las versiones
recientes de `hermes-agent` rechazan explícitamente; el fix
[nesquena/hermes-webui#6458](https://github.com/nesquena/hermes-webui/pull/6458)
solo se publicó en el track experimental, nunca se backporteó a un tag
estable).

Investigando la causa se confirmó que `hermes-agent` trae, desde la
v0.16+, un **dashboard web integrado** (FastAPI + React, puerto 9119,
activado con `HERMES_DASHBOARD=1`) que cubre toda la superficie que
ofrecía `hermes-webui`: configuración, API keys, MCP, mensajería, cron,
sesiones, logs, skills. La documentación oficial de Nous Research lo
confirma explícitamente: "elimina la necesidad de un contenedor
`hermes-webui` separado". Por lo tanto, la arquitectura correcta es un
**único contenedor**, sin el componente de terceros que causó el problema.

No hay datos reales en el agente todavía (deploy nuevo, sin historial de
conversaciones ni memoria acumulada) — no hace falta migrar estado desde
el recurso anterior.

Este repo es la fuente de verdad del despliegue, siguiendo el mismo patrón
que [ai-gateway-infra](https://github.com/rodrigofanjul/ai-gateway-infra)
(el repo hermano que despliega OmniRoute en el mismo servidor Coolify).

```
Git repository → Coolify → Docker Compose → hermes-agent (con dashboard)
```

## Arquitectura

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

- Un solo servicio en el `docker-compose.yml`, imagen
  `nousresearch/hermes-agent`, comando `gateway run`.
- El contenedor **no publica puertos al host** — Coolify enruta HTTPS al
  puerto del dashboard (`9119`) por la red interna de Docker (`expose`,
  no `ports`), igual que en OmniRoute.
- El puerto del gateway (`8642`, API OpenAI-compatible del propio Hermes)
  queda interno — no hace falta exponerlo a internet salvo que en algún
  momento se necesite pegarle desde afuera del servidor.
- Imagen fijada a un tag de versión concreto
  (`nousresearch/hermes-agent:v2026.8.19`, confirmado con build `arm64`
  disponible), no `:latest` — mismo criterio anti-sorpresas que
  OmniRoute.
- Toda la persistencia vive en `/opt/data` dentro del contenedor,
  respaldada por el volumen nombrado `hermes-data`.

## Variables de entorno

Sin API keys de proveedores de LLM (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, etc.) en el compose ni en Coolify. El único modelo
configurado es un **"custom endpoint"** apuntando a OmniRoute
(`https://router.tu-dominio.com/v1` + API key generada en el dashboard de
OmniRoute), cargado **post-deploy desde el propio dashboard de Hermes**
(Settings → Providers → Custom endpoint) — no existe una vía soportada
para hacerlo por env var (`OPENAI_BASE_URL` es solo un fallback legacy,
y además Coolify no lo pasaría al contenedor salvo que el compose lo
referencie explícitamente).

| Variable | Obligatoria | Propósito |
|---|---|---|
| `HERMES_DASHBOARD` | Sí | `1` — activa el servicio de dashboard supervisado por s6 junto al gateway |
| `HERMES_UID` / `HERMES_GID` | Sí | `1000` / `1000` — evita problemas de permisos entre el contenedor y el volumen |
| `HERMES_DASHBOARD_PUBLIC_URL` | Sí | URL pública HTTPS completa (ej. `https://hermes.tu-dominio.com`) — necesaria para que el auth gate arme bien las URLs de sesión/callback |
| `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` | Sí | Usuario de login del dashboard |
| `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH` | Sí | Hash scrypt de la contraseña (nunca texto plano) |
| `HERMES_DASHBOARD_BASIC_AUTH_SECRET` | Sí | Clave de firma de las cookies de sesión |

Auth elegida: **basic auth** (usuario/password hasheado). El dashboard
exige siempre un auth provider cuando no está en bind loopback — no
existe opción de exposición pública sin autenticación. Basic auth es
suficiente detrás del HTTPS que ya provee Traefik/Coolify para este caso
de uso personal; queda documentado en el README que existen alternativas
(OAuth de Nous Research, OIDC self-hosted) si más adelante se necesita
algo más robusto.

## Persistencia y backups

Volumen nombrado `hermes-data` montado en `/opt/data`: config
(`config.yaml`, `.env`), sesiones, memoria persistente, skills,
credenciales, cron jobs, logs.

Se incluye `scripts/backup.sh`, adaptado del de `ai-gateway-infra`: mismo
enfoque restic + Backblaze B2 (repositorio cifrado en reposo,
deduplicación, snapshots incrementales, retención automática 7 diarios +
4 semanales, imagen oficial `restic/restic` sin instalar nada en el
host). Cambia únicamente el volumen origen (`hermes-data` en vez de
`omniroute-data`) y el flag `--host hermes-agent-backup` (en vez de
`omniroute-backup`) para diferenciar los snapshots de ambos servicios
dentro del mismo bucket/repositorio B2 que ya usa OmniRoute — no hace
falta un bucket separado, `restic` distingue snapshots por `--host` y
`--tag` dentro de un mismo repositorio.

Es especialmente relevante acá porque, a diferencia de OmniRoute (que
guarda config/credenciales/estadísticas), este volumen también va a
acumular **memoria persistente del agente** (skills aprendidos,
contexto de conversaciones) — perderlo no solo rompe la configuración,
sino el "aprendizaje" acumulado del asistente.

## Actualización

Mismo criterio que OmniRoute: tag de imagen fijo en `docker-compose.yml`,
nunca `:latest`. Dado el ritmo de releases de `hermes-agent` (casi
diario), la política de actualización documentada en el README es:

- Revisión de changelog cada 2-4 semanas, no por cada release individual.
- Excepción: un release marcado explícitamente como fix de seguridad se
  aplica de inmediato, sin esperar al ciclo.
- Antes de actualizar: leer las release notes desde la versión actual
  hasta la nueva buscando cambios de esquema/breaking changes, y
  confirmar que el volumen `hermes-data` tiene un backup reciente.
- Ventaja estructural sobre el setup anterior: al ser un solo contenedor
  con el dashboard integrado, **no existe el riesgo de desincronizar dos
  imágenes de proyectos distintos** (el problema raíz que rompió el
  deploy con `hermes-webui`) — actualizar es cambiar un solo tag.

## Estructura del repo (README)

Mismo esqueleto que `ai-gateway-infra`, adaptado:

1. Arquitectura
2. Requisitos
3. Variables de entorno (tabla de arriba + generación de secretos)
4. Crear el recurso en Coolify desde este repositorio
5. Configuración del dominio
6. HTTPS
7. Persistencia
8. Primer login (dashboard, basic auth)
9. Conectar OmniRoute como provider (breve — Settings → Providers →
   Custom endpoint, con el endpoint/API key de OmniRoute)
10. Backups (off-site a Backblaze B2 con restic)
11. Actualización de la imagen
12. Rollback
13. Troubleshooting básico (incluye una nota corta documentando el
    problema histórico de `hermes-webui` como referencia, por si en el
    futuro se reconsidera esa vía)

Fuera de alcance para este repo (documentado como nota breve, no como
guía paso a paso): conectar WhatsApp (escaneo de QR) y configurar el
allowlist de usuarios permitidos — son pasos que se hacen desde el
dashboard una vez desplegado, no configuración de infraestructura.

## Fuera de alcance

- Migración de datos desde el recurso anterior (no existían datos
  reales).
- Guía detallada de conexión de canales de mensajería (WhatsApp,
  Telegram, etc.) — se linkea a la documentación oficial de Hermes,
  no se reescribe acá.
- Configuración de OAuth de Nous Research u OIDC self-hosted para el
  dashboard — queda como alternativa mencionada, no implementada, salvo
  que se decida necesaria más adelante.
