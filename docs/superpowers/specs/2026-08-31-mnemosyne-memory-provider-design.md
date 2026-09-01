# Mnemosyne memory provider — design

## Contexto y motivación

Hermes Agent soporta un memory provider externo opcional (además de la
memoria integrada MEMORY.md/USER.md). Se evaluó agregar
[Mnemosyne](https://github.com/AxDSan/mnemosyne) (paquete PyPI real:
`mnemosyne-hermes`, no `mnemosyne-harness` como indica su propia
documentación — primera de varias inconsistencias encontradas en el
proyecto) como ese proveedor externo, en vez de uno de los 8 oficiales
documentados por NousResearch.

**Es un proyecto de terceros, no oficial, de un solo mantenedor**, sin
trayectoria establecida (hay un issue abierto en
`NousResearch/hermes-agent` proponiendo sumarlo a la documentación
oficial, todavía sin resolución). Durante la investigación se encontraron
varias inconsistencias entre su documentación y la realidad del paquete
publicado (nombre de paquete incorrecto, dependencias reales que
contradicen el claim de "zero deps", un release yankeado por error de
versionado, un claim de integración "nativa" con Hermes que no se
corrobora en la instalación real).

**Valor real para el uso actual**: las 3 tareas cron que corren hoy
(resumen diario de gastos, recordatorios, carga de cierres de tarjeta)
leen datos frescos de Google Sheets en cada corrida — no dependen de
memoria recordada entre sesiones. El beneficio de Mnemosyne (recall
semántico vs. keyword) solo se nota en uso conversacional libre
acumulativo, no en estos jobs. Se procede de todas formas, con el
entendimiento de que es una apuesta a futuro uso conversacional, no una
necesidad del uso actual.

## Arquitectura

Dos piezas con distinto nivel de confianza, separadas deliberadamente:

### 1. Imagen Docker custom (build-time, controlado y versionado)

Nuevo `Dockerfile` en este repo, construido sobre la imagen pineada de
`hermes-agent`:

```dockerfile
FROM nousresearch/hermes-agent:v2026.8.31
RUN uv pip install --python /opt/hermes/.venv/bin/python mnemosyne-hermes==0.5.0
COPY mnemosyne-bootstrap.sh /usr/local/bin/mnemosyne-bootstrap.sh
RUN chmod +x /usr/local/bin/mnemosyne-bootstrap.sh
```

(Originally written as `python -m pip install`. Production build failed
with "No module named pip" — the base image's venv was built with `uv`
and has no `pip` at all. Fixed to `uv pip install --python ...`; see the
plan doc for the full incident trail.)

- `mnemosyne-hermes==0.5.0` arrastra `mnemosyne-memory[embeddings]` como
  dependencia dura (confirmado vía `requires_dist` en PyPI) — perfil
  `[embeddings]` (~800MB, búsqueda vectorial local con `fastembed`, sin
  LLM local extra). Versión pineada explícitamente, no `latest`.
- Se instala en `/opt/hermes/.venv`, que vive dentro del volumen
  `hermes-agent-src` — por lo tanto sobrevive a restarts normales pero
  **se pierde en cada futuro upgrade de `hermes-agent`** (mismo volumen
  que ya se recrea deliberadamente por el procedimiento documentado en
  README sección 11). Cada bump de versión de `hermes-agent` requiere
  rebuildear esta imagen custom con el nuevo tag base.

### 2. Wrapper de arranque (runtime, idempotente, sin instalar nada nuevo)

`mnemosyne-bootstrap.sh`, copiado a la imagen, seteado como `command:`
del servicio `hermes` en el compose:

```bash
#!/bin/sh
set -eu

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
```

Corre en cada boot del contenedor. No ejecuta código de terceros nuevo
ni hace llamadas de red — solo linkea archivos ya instalados en la
imagen (paso 1) y setea una config. Es seguro que corra
automáticamente sin supervisión, a diferencia de un `pip install` en
cada arranque (alternativa descartada explícitamente por el riesgo de
reinstalar código no auditado en cada boot sin control de versión).

(Dos correcciones sobre la primera versión de este script, ambas
encontradas durante la implementación — ver el plan doc para el
detalle completo: el guard de `PKG_DIR` vacío, que evita un
`ln -sfn /*` catastrófico; y el `exec` final, que originalmente decía
`exec gateway run` y crasheaba en producción con `exec: gateway: not
found` — `gateway` solo se resuelve como subcomando de `hermes` a
través del routing de `main-wrapper.sh` de la imagen base, que este
script no atraviesa una segunda vez.)

## Cambios en `docker-compose.yml`

```diff
   hermes:
-    image: nousresearch/hermes-agent:v2026.8.31
+    build: .
     container_name: hermes
-    command: gateway run
+    command: ["/usr/local/bin/mnemosyne-bootstrap.sh"]
```

## Riesgo abierto — resuelto durante la implementación

Este riesgo (no había visibilidad del `entrypoint-dispatch.sh` interno
de la imagen base) se validó empíricamente antes de deployar, y **el
deploy real igual encontró dos problemas que la validación aislada no
detectó** — ambos con causa raíz confirmada leyendo el código real de
la imagen base (`main-wrapper.sh`), no por prueba y error:

1. **`pip` no existe en el venv de la imagen base** (usa `uv`) — la
   validación standalone también lo hubiera encontrado si se hubiera
   corrido antes del primer intento real; se corrigió antes de llegar
   a producción.
2. **`exec gateway run` no es una invocación válida por sí sola** —
   solo funciona como argumento que `main-wrapper.sh` interpreta y
   traduce a `hermes gateway run` (routing de subcomando). Nuestro
   script, al ser él mismo el `command:` del contenedor, no pasa por
   ese routing una segunda vez para su propio `exec` final. **Esto sí
   escapó a la validación aislada** (Task 3 del plan) porque esa
   prueba corrió la imagen con su comportamiento de entrypoint por
   defecto, sin pasar realmente por nuestro script como `command:` —
   un gap real en el diseño de esa validación, documentado en el plan
   para futura referencia.

Ambos se encontraron y corrigieron en el primer despliegue real (ver
plan para el detalle completo, incluyendo el incidente de producción
—el contenedor entró en crash-loop y Coolify lo derribó— y su
resolución). El mecanismo de arranque quedó confirmado funcionando en
producción.

## Procedimiento de upgrade futuro de `hermes-agent`

Mismo procedimiento ya documentado (parar, borrar volumen
`hermes-agent-src`, levantar), con un cambio: el paso de traer la
imagen nueva pasa de `docker compose pull` a `docker compose build`
(o `build --no-cache` si se sospecha de cache stale), porque ahora el
Dockerfile de este repo, no Docker Hub directo, es la fuente de la
imagen final. Bump del tag base en el `FROM` del Dockerfile en el
mismo commit que el bump de versión.

**Esto también aplica a un bump de solo `mnemosyne-hermes` sin tocar
la imagen base** — el volumen `hermes-agent-src` igual necesita
recrearse, porque el paquete vive dentro de él y Docker no lo
repuebla si el volumen ya existe. Confirmado empíricamente: el primer
deploy de esta feature crasheó exactamente por esto (el volumen ya
existía de antes, shadowing el venv nuevo del build).

**Nota operativa pendiente:** README sección 11 ("Actualización de
Hermes Agent") todavía describe el procedimiento viejo (editar
`image:` directo) — no refleja que `hermes` ahora usa `build: .` con
el tag en el `Dockerfile`. Actualizar esa sección antes del próximo
upgrade de `hermes-agent` para evitar que alguien edite el lugar
equivocado.

## Testing / validación

1. Build local (`docker compose build`) sin errores.
2. Deploy y confirmar `docker ps` con el contenedor `hermes` `healthy`
   (no crash-loop — el síntoma histórico fue justamente esto).
3. `docker exec ... hermes memory status` confirma `provider: mnemosyne`
   activo.
4. `docker exec ... hermes tools list | grep mnemosyne_` confirma que
   las tools del plugin están expuestas.
5. Mensaje real en hermes-webui, confirmar que no rompe el flujo normal
   de chat.
6. Confirmar que los 3 cron jobs existentes (`Resumen diario de
   gastos`, `Recordatorios diarios`, `Carga cierres de tarjeta`) siguen
   corriendo sin error tras el cambio — no deberían verse afectados,
   pero se verifica igual dado que comparten el mismo contenedor.

## Rollback

Revertir `docker-compose.yml` a `image: nousresearch/hermes-agent:v2026.8.31`
+ `command: gateway run` (git revert del commit), redeploy. La memoria
built-in (MEMORY.md/USER.md) sigue disponible en paralelo durante toda
la transición — no se desactiva en ningún momento — así que un rollback
no pierde memoria funcional, solo descarta el proveedor externo.
