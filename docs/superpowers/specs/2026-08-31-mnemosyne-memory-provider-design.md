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
RUN /opt/hermes/.venv/bin/python -m pip install mnemosyne-hermes==0.5.0
COPY mnemosyne-bootstrap.sh /usr/local/bin/mnemosyne-bootstrap.sh
RUN chmod +x /usr/local/bin/mnemosyne-bootstrap.sh
```

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
ln -sfn "$PKG_DIR"/* "$HERMES_HOME/plugins/mnemosyne/"

"$VENV/bin/hermes" config set memory.provider mnemosyne

exec gateway run
```

Corre en cada boot del contenedor. No ejecuta código de terceros nuevo
ni hace llamadas de red — solo linkea archivos ya instalados en la
imagen (paso 1) y setea una config. Es seguro que corra
automáticamente sin supervisión, a diferencia de un `pip install` en
cada arranque (alternativa descartada explícitamente por el riesgo de
reinstalar código no auditado en cada boot sin control de versión).

## Cambios en `docker-compose.yml`

```diff
   hermes:
-    image: nousresearch/hermes-agent:v2026.8.31
+    build: .
     container_name: hermes
-    command: gateway run
+    command: ["/usr/local/bin/mnemosyne-bootstrap.sh"]
```

## Riesgo abierto — requiere validación empírica

No hay visibilidad del `entrypoint-dispatch.sh` interno de la imagen
base (mismo mecanismo que causó el bug histórico de
`hermes-agent-src` documentado en README sección 11/13). El wrapper
asume que puede terminar con `exec gateway run` reproduciendo el
comportamiento del `command:` original, pero **esto no está
confirmado** — hay que validarlo contra el arranque real antes de
confiar en que no rompe nada. Es el paso de mayor riesgo de todo el
plan.

## Procedimiento de upgrade futuro de `hermes-agent`

Mismo procedimiento ya documentado (parar, borrar volumen
`hermes-agent-src`, levantar), con un cambio: el paso de traer la
imagen nueva pasa de `docker compose pull` a `docker compose build`
(o `build --no-cache` si se sospecha de cache stale), porque ahora el
Dockerfile de este repo, no Docker Hub directo, es la fuente de la
imagen final. Bump del tag base en el `FROM` del Dockerfile en el
mismo commit que el bump de versión.

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
