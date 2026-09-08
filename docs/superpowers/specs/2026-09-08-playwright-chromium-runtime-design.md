# Playwright + Chromium runtime for hermes — design

## Contexto y motivación

Se necesita un navegador real dentro del contenedor `hermes` para scripts
dedicados (no para la tool "browser" del propio agente, que solo sabe
hablar con backends pagos en la nube — Browserbase, Browser-Use Cloud,
Firecrawl — confirmado revisando `hermes plugins list`, sin ningún
backend local disponible). El primer consumidor es un script de
sincronización con el online banking de Banco Galicia (ver
`docs/superpowers/specs/2026-09-08-galicia-sync-design.md` si existe, o
la conversación que originó esto): el formulario de login cifra la
contraseña del lado del cliente vía JavaScript antes de enviarla
(campo oculto `EncriptedPassword`, poblado por un listener de teclado),
así que un script de solo-HTTP (`requests`) no alcanza — hace falta un
navegador real ejecutando ese JS.

Este cambio agrega la capacidad de navegador al **contenedor**, como
dependencia de scripts propios ejecutados vía `code_execution`, no como
integración con el toolset "browser" de hermes.

## Qué se confirmó antes de diseñar esto

- La imagen base (`nousresearch/hermes-agent:v2026.8.31`) corre como
  `root` durante el build y tiene `apt-get` disponible (Debian 13
  "trixie") — confirmado con `docker run --rm --entrypoint sh ... -c
  'whoami; which apt-get'`. Esto es lo que hace viable
  `playwright install --with-deps chromium` (necesita apt para las
  dependencias de sistema de Chromium).
- El Dockerfile de este repo ya existe (agregado para Mnemosyne) y ya
  tiene un patrón probado de `RUN uv pip install --python
  /opt/hermes/.venv/bin/python ...` que funciona en producción.

## Cambios

### `Dockerfile`

Agregar, después de la instalación de `mnemosyne-hermes`:

```dockerfile
RUN uv pip install --python /opt/hermes/.venv/bin/python playwright==<version-pineada>
RUN /opt/hermes/.venv/bin/playwright install --with-deps chromium
```

(La versión exacta de `playwright` a pinear se resuelve durante la
implementación, consultando PyPI igual que se hizo con
`mnemosyne-hermes` — no asumir `latest`.)

### `docker-compose.yml`

El servicio `hermes` necesita `shm_size` aumentado — el default de
Docker (64MB) provoca crashes aleatorios de Chromium (mismo problema
documentado en `ai-gateway-infra` para el flavor `-web` de OmniRoute,
que también bundlea Playwright+Chromium):

```yaml
    shm_size: "1gb"
```

No se agrega `cap_drop`/`security_opt` — el comentario existente en el
Dockerfile/compose de `hermes` ya documenta por qué (el entrypoint hace
un `chown`/cambio de UID al arrancar que no está validado bajo
capacidades reducidas). Los scripts que lancen Chromium deben pasar
`--no-sandbox` en los argumentos de lanzamiento del navegador (Playwright
expone esto vía `launch(args=["--no-sandbox"])`) — es el patrón estándar
para correr Chromium sin sandboxing de kernel dentro de un contenedor
Docker, y evita depender de namespaces de usuario no garantizados aquí.

## Validación empírica requerida antes de tocar el compose real

Mismo procedimiento que se usó para Mnemosyne (ver
`docs/superpowers/plans/2026-08-31-mnemosyne-memory-provider.md`, Task 3):
build standalone en un directorio temporal del servidor, `docker run`
suelto (sin tocar el contenedor de producción ni sus volúmenes), y
confirmar con un script mínimo de Playwright (`chromium.launch()` +
navegar a about:blank + cerrar) que el navegador realmente levanta
dentro del contenedor, antes de fusionar esto al `Dockerfile`/compose
que sirve producción.

## Riesgo conocido, no resuelto por este cambio

`hermes-agent-src` es el volumen que sobrevive entre deploys y se debe
recrear en cada cambio de imagen (ver README sección 11 e incidente de
producción documentado en el plan de Mnemosyne). Este cambio agrega
~300-400MB a la imagen (Chromium + deps) — el mismo procedimiento de
recreación de volumen aplica acá, sin excepción.

## Testing / validación post-deploy

1. Build sin errores (`docker compose build`).
2. Contenedor `hermes` queda `healthy`, sin crash-loop.
3. Un script mínimo (`playwright chromium.launch() → about:blank →
   close()`) corre exitosamente dentro del contenedor real, vía
   `docker exec`.
4. El script de Galicia (implementado en un cambio posterior, no en
   este) puede efectivamente levantar el navegador cuando se invoque.

## Rollback

Revertir el Dockerfile/compose a la versión anterior a este cambio
(sin Playwright/Chromium), recrear `hermes-agent-src`, redeploy. No
afecta `hermes-data` ni la configuración de Mnemosyne (cambios
independientes).
