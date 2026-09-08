# Sincronización diaria de Banco Galicia — design

## Contexto y motivación

Script que hermes ejecuta como cron diario (vía `hermes cron create ...
--no-agent`, sin que el LLM participe en la ejecución) para respaldar
en Google Drive el estado del online banking de Banco Galicia: saldos,
movimientos de cuenta, movimientos de tarjeta, y resúmenes de tarjeta
en PDF. Nunca se manda nada por WhatsApp salvo que el login falle — ver
sección "Notificación".

**Motivación de seguridad**: las credenciales bancarias reales nunca
pasan por el LLM ni por esta conversación — viven como variables de
entorno del contenedor (Coolify), y el script las lee directo de
`os.environ`.

## Reconocimiento previo (hecho manualmente, con supervisión humana)

Sesión de exploración real contra `onlinebanking.bancogalicia.com.ar`,
con el usuario tipeando sus propias credenciales en un navegador
visible (Playwright, controlado desde esta sesión), sin que el LLM
tocara ni viera la contraseña en ningún momento. Hallazgos:

- **Login**: formulario con 3 campos reales (`DocumentNumber`,
  `UserName`, `Password`, todos `<input>` normales, class `keyboard`).
  El toggle "Teclado Virtual" solo cambia el método visual de tipeo,
  no la estructura del form. Hay un campo oculto `EncriptedPassword`
  que un listener JS puebla mientras se tipea — **la clave se cifra
  client-side antes de enviarse**, por eso hace falta un navegador real
  ejecutando ese JS (un script de solo-`requests` no alcanza). También
  hay un campo `DevicePrintAdaptive` (fingerprinting de dispositivo,
  confirma que sí hay detección de bot activa, independiente del
  teclado).
- **Sin captcha ni 2FA** en los dos intentos reales de esta sesión.
- **Arquitectura del sitio**: cada sección vive en su propio subdominio
  (`cuentas.bancogalicia.com.ar`, `tarjetas.bancogalicia.com.ar`),
  todos dentro de la misma sesión autenticada (cookies compartidas).
- **Cuentas y sus movimientos**: sin API JSON — los datos vienen
  renderizados directo en el HTML de
  `cuentas.bancogalicia.com.ar/cuentas/mis-cuentas`. Estructura:
  lista de `<button>`, cada uno con fecha / descripción / monto como
  hijos. Hay que parsear HTML (BeautifulSoup), no hay atajo de API acá.
- **Resúmenes de tarjeta (PDF)**: sí hay API JSON real, confirmada
  empíricamente (se bajó un PDF real durante el reconocimiento):
  - `GET https://tarjetas.bancogalicia.com.ar/api/resumen/list?from=YYYY-MM-DD&end=YYYY-MM-DD`
  - `POST https://tarjetas.bancogalicia.com.ar/api/resumen/getresumen`
  - Ambos endpoints aceptan las cookies de sesión de Playwright
    reusadas en un `requests.Session()` normal — no hace falta el
    navegador para esta parte específica.
- **Movimientos de tarjeta** (compras individuales, antes de
  consolidarse en el resumen mensual): vistos renderizados en HTML en
  `tarjetas.bancogalicia.com.ar/tarjetas/ini` (sección "MOVIMIENTOS",
  con paginación "Mostrar más"). **No se confirmó si existe una API
  limpia para esto** (no se investigó a fondo) — la Fase de
  implementación debe confirmarlo empíricamente (repitiendo el mismo
  método: inspeccionar `browser_network_requests` al cargar/paginar
  esa sección) antes de asumir que hace falta scrapear HTML.

## Prerequisitos (deben estar resueltos antes de este script)

1. **Playwright + Chromium en la imagen de hermes** — ver
   `docs/superpowers/specs/2026-09-08-playwright-chromium-runtime-design.md`.
   Debe estar implementado y validado (contenedor real, `hermes`
   healthy, Chromium levanta) antes de escribir este script.
2. **`beautifulsoup4`** — confirmado ausente del venv de hermes
   (`ModuleNotFoundError: No module named 'bs4'`, verificado por SSH).
   Agregar al mismo `RUN uv pip install ...` del Dockerfile que instala
   Playwright, no como paso separado.
3. **`requests`** — confirmado ya presente en el venv de hermes, no
   requiere instalación.
4. **Google Workspace skill ya operativo** — confirmado: hermes ya
   tiene `google_api.py` con soporte de Drive (`search`, `get`,
   `upload`, `download`, `create-folder`, `share`, `delete`), invocado
   como **CLI vía subprocess**, no como librería importable. No hace
   falta ninguna credencial nueva.

## Almacenamiento y credenciales

Variables de entorno (Coolify → Environment Variables del recurso
`hermes-agent-infra`, marcadas como Secret):

```
GALICIA_DNI
GALICIA_USER
GALICIA_PASSWORD
```

El script las lee con `os.environ["GALICIA_..."]` — nunca se imprimen,
nunca se loguean, nunca aparecen en el código fuente.

## Estructura en Drive

Carpeta raíz ya creada por el usuario: `Bancos` (Drive folder ID:
`1wNYrxgVJo6MPN4kc7aZK3q8qi9s9ZlJS`).

```
Bancos/
  Galicia/
    Cuentas/
      caja_ahorro_pesos.csv       (acumulativo, dedupe por fecha+desc+monto)
      caja_ahorro_dolares.csv
    Tarjetas/
      visa_8477/
        movimientos.csv           (acumulativo, dedupe por fecha+comercio+monto)
        RESUMEN_VISA_08_2026.pdf  (nombre tal cual lo entrega el banco)
        RESUMEN_VISA_07_2026.pdf
      mastercard_3652/
        movimientos.csv
        ...
```

Los nombres de subcarpeta de cuenta/tarjeta se derivan de lo que el
script descubre en cada corrida (ver "Descubrimiento automático") —
slugificados (minúsculas, espacios a `_`, sin tildes).

## Arquitectura del script

Un solo archivo Python (`galicia_sync.py`), estructurado en funciones
con responsabilidad única — sin clases innecesarias, dado que es un
script de un solo uso lineal (login → extraer → guardar → logout), no
un servicio de larga vida.

### Flujo principal

1. **Login** (Playwright): navega a
   `onlinebanking.bancogalicia.com.ar/login`, completa los 3 campos
   con `page.fill()` (dispara los eventos de teclado que el JS de
   cifrado necesita — confirmar esto empíricamente en la
   implementación, no asumir que `fill()` alcanza; si no dispara el
   listener correctamente, usar `page.type()` carácter por carácter
   en su lugar), click en "iniciar sesión".
2. **Detección de fallo de login**: si después del submit la URL sigue
   siendo `/login` (o contiene `/login`), o hay un mensaje de error
   visible en el DOM — imprimir una línea corta a stdout (sin datos de
   la credencial) y terminar con `sys.exit(1)` **sin reintentar**.
   Ejemplo: `"Galicia: no se pudo iniciar sesión, revisar manualmente"`.
3. **Descubrimiento automático de cuentas**: parsear
   `cuentas.bancogalicia.com.ar/cuentas/inicio` para enumerar cada
   cuenta (nombre, número, moneda, saldo).
4. **Por cada cuenta**: navegar a su página de detalle, parsear la
   lista de movimientos (HTML), dedupe contra el CSV existente en
   Drive (bajado primero vía `google_api.py drive download`), agregar
   solo lo nuevo, resubir vía `google_api.py drive upload` (mismo
   nombre → sobrescribe; confirmar en la implementación si `upload`
   sobrescribe por nombre+parent o crea duplicado — si crea duplicado,
   hay que buscar el `file_id` existente primero con `drive search` y
   ver si hay un modo update, o borrar el viejo antes de subir el
   nuevo).
5. **Descubrimiento automático de tarjetas**: parsear
   `tarjetas.bancogalicia.com.ar/tarjetas/ini` para enumerar cada
   tarjeta (marca, últimos 4 dígitos).
6. **Por cada tarjeta — movimientos**: confirmar empíricamente si hay
   API (pendiente, ver "Reconocimiento previo"); si no la hay, parsear
   HTML con paginación ("Mostrar más") hasta agotar; dedupe y
   guardar igual que cuentas.
7. **Por cada tarjeta — resúmenes**: extraer cookies de la sesión de
   Playwright (`context.cookies()`), armar un `requests.Session()`
   con ellas, llamar `GET /api/resumen/list`, por cada resumen listado
   chequear si ya existe un archivo con ese nombre en la subcarpeta de
   Drive correspondiente (`drive search`); si no existe, `POST
   /api/resumen/getresumen`, guardar el PDF a un archivo temporal
   (`tempfile`, nunca dentro del repo), subir con `drive upload`,
   borrar el temporal.
8. **Logout**: click en "Cerrar Sesión" — dentro de un `finally`, para
   que corra incluso si algo falló a mitad de camino.
9. **Éxito silencioso**: si todo el flujo corre sin login fallido, el
   script no imprime nada a stdout (stdout vacío = "todo bien" para el
   modo `--no-agent` de hermes — no dispara ninguna entrega).

### Manejo de errores por sección

Un fallo en una sección (ej. no se pudo parsear una tarjeta puntual)
**no aborta las demás** — se seguen las demás cuentas/tarjetas, y al
final, si hubo algún fallo parcial (no de login), se imprime un resumen
corto a stdout (ej. `"Galicia: sync parcial, falló tarjeta terminada en
3652"`) para que quede como entrega de WhatsApp igual — visibilidad de
que algo no se pudo procesar, sin ser tan grave como un login fallido.

## Registro del cron job

```bash
hermes cron create "0 9 * * *" \
  --name "Sync Galicia" \
  --script galicia_sync.py \
  --no-agent \
  --deliver origin
```

El script debe copiarse a `/opt/data/scripts/galicia_sync.py` dentro
del contenedor (volumen `hermes-data`, persistente) — se versiona en
este repo para su revisión, pero **no** se ejecuta desde ahí
directamente; hace falta un paso de deploy (copia manual por SSH la
primera vez, o un paso en el bootstrap del contenedor) que lo lleve a
esa ruta.

## Testing / validación

1. Confirmar Playwright+Chromium ya validado (prerequisito).
2. Confirmar `beautifulsoup4` instalado tras el build.
3. Correr el script manualmente una vez (`python galicia_sync.py`,
   dentro del contenedor, credenciales reales vía env vars ya
   seteadas) — con supervisión humana viendo qué hace, dado que toca
   una cuenta bancaria real.
4. Confirmar en Drive que la estructura de carpetas/archivos quedó
   como se diseñó, sin duplicados.
5. Confirmar que correr el script una SEGUNDA vez seguida no duplica
   nada (dedupe funcionando).
6. Confirmar logout real (probar loguear de nuevo manualmente después
   y ver que pide credenciales, no que la sesión seguía viva).
7. Recién después de estas validaciones manuales, registrar el cron
   job real.

## Rollback

Si el script falla de forma persistente o se decide discontinuar:
`hermes cron remove "Sync Galicia"`, borrar
`/opt/data/scripts/galicia_sync.py`. No afecta nada más del
despliegue — es un script aislado, sin dependencias de otros cron
jobs ni de Mnemosyne.
