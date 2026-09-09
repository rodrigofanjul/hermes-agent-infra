# Sincronización diaria de Banco Nación (BNA+) — design

## Contexto y motivación

Script que hermes ejecuta como cron diario (vía `hermes cron create ...
--no-agent`, sin que el LLM participe en la ejecución) para respaldar
en Google Drive el estado del homebanking nuevo de BNA
(`digital.bna.com.ar`): saldos, movimientos de cuenta, movimientos de
tarjeta, resúmenes de tarjeta en PDF, y préstamos (cuotas pagas y a
vencer). Nunca se manda nada por WhatsApp salvo que el login falle o
haya un fallo parcial — mismo contrato que
`docs/superpowers/specs/2026-09-08-galicia-sync-design.md`, del que
este proyecto reutiliza toda la arquitectura.

**Motivación de seguridad**: las credenciales bancarias reales nunca
pasan por el LLM ni por esta conversación — viven como variables de
entorno del contenedor (Coolify), y el script las lee directo de
`os.environ`. Durante el reconocimiento, el usuario tipeó sus propias
credenciales manualmente en un navegador visible; el LLM nunca las vio.

## Reconocimiento previo (hecho manualmente, con supervisión humana)

Sesión de exploración real contra `digital.bna.com.ar` en dos partes:
sin login (estructura pública del formulario, Términos y Condiciones)
y con el usuario logueado manualmente. Hallazgos:

- **Login en 2 pasos**, no 1 como Galicia:
  - Paso 1 (`/loginStep1`): dos `<input>` reales, `#document` (DNI) y
    `#username` (Usuario) → botón `#global\.continue` (el id literal
    contiene un punto, hace falta escaparlo en el selector CSS).
  - Paso 2 (`/loginStep2`): un `<input type="password" id="password">`
    → mismo botón de continuar.
  - `pressSequentially`/`page.type()` (tipeo real carácter por
    carácter) habilita el botón en ambos pasos — confirmado
    empíricamente, igual que Galicia. `.fill()` no se probó pero por
    consistencia se usa `.type()` desde el principio.
  - Hay un botón "Teclado virtual" en ambos pasos, pero es **opcional**
    (accesibilidad) — el campo de texto normal funciona sin él, a
    diferencia de otros bancos argentinos que fuerzan un teclado
    virtual obligatorio para la contraseña.
  - Sin CAPTCHA en el flujo.
- **Riesgo adicional confirmado en los Términos y Condiciones**: BNA
  declara explícitamente el uso de **biometría de comportamiento**
  (velocidad de tipeo, movimiento del mouse, gestos táctiles) para
  detección de fraude — más explícito que lo visto en Galicia. Decisión
  tomada con el usuario: proceder igual (mismo patrón de `.type()` con
  delay, minimizar logins de prueba, confirmación explícita en cada
  paso riesgoso), sin agregar mitigaciones adicionales por ahora.
- **Sin prohibición explícita de automatización** en los Términos y
  Condiciones (revisados completos) — solo cláusulas genéricas de
  fraude/seguridad y la biometría de comportamiento mencionada arriba.
- **Arquitectura del sitio**: SPA única (no subdominios separados como
  Galicia), con API JSON propia bajo `/api/v1/execute/*` autenticada
  por **bearer token JWT** (no cookies) enviado en el header
  `Authorization`. Confirmado via inspección de network requests.
- **Cuentas** (`/accounts/myaccounts`): cada cuenta es un
  `<button id="account_card_number_{i}" role="link">`, que al hacer
  click navega a una URL real y estable por cuenta
  (`/accounts/<hash-opaco>`). El `aria-label`/texto del botón expone
  nombre y últimos 4 dígitos; el saldo se ve en un `<span>` separado
  en la vista de detalle (`$ 66.346,91`), no en el mismo texto que el
  nombre (a diferencia de Galicia, donde el saldo viene en el mismo
  `aria-label`).
- **Movimientos de cuenta**: tabla HTML estándar y limpia —
  `<table><thead><tbody><tr><td>`, columnas Fecha/Comprobante/Concepto/
  Monto. Mucho más simple de parsear que la estructura basada en
  `<div>` de Galicia. La vista por defecto muestra un rango limitado
  ("Llegaste al final de la consulta. Podés ver más movimientos desde
  'Filtrar'") — igual limitación que Galicia, se resuelve solo con el
  dedupe incremental diario.
- **Botón "Descargar" (CSV/XLS/PDF) de movimientos — NO USAR**: dispara
  `POST /api/v1/execute/latest.account.movements.download`, un
  endpoint con **fingerprinting de dispositivo muy pesado** en el body
  (hash de canvas, hash de WebGL, fuentes instaladas, resolución de
  pantalla, etc.) firmado con un campcampo `_std_` (probablemente un
  HMAC del payload). La respuesta no trae el archivo — es
  `{"data": {}}` — y `/digitalDocuments` resultó ser una página de
  preferencias de envío por correo, no una bandeja de descargas: **el
  archivo se manda por email**, no se descarga en el browser.
  **Decisión de diseño**: no replicar esta llamada. Fabricar esos datos
  de fingerprint para pasar por un navegador real cruzaría a evasión
  activa del sistema antifraude del banco, algo que hay que evitar
  explícitamente. Se parsea la tabla HTML en su lugar (ya confirmada
  arriba), igual que se hace con cuentas.
- **Tarjetas** (`/cards`): 4 tarjetas reales en la cuenta de prueba (2
  Crédito, 2 Mastercard Débito), cada una un `<button id="card-{i}">`
  que navega a `/cards/creditCards/detail/<hash-opaco>`. Esa página
  tiene tabs: **Movimientos** (misma tabla HTML que cuentas — Detalle/
  Fecha/Monto) y **Resúmenes** (lista de 12 meses, cada uno con un
  botón de descarga con `icon="receipt_download"`).
  - **Sin confirmar**: qué hace exactamente ese botón de descarga. Un
    primer intento de clickearlo vía JS **cortó la sesión** (la página
    quedó en `about:blank` y una navegación posterior redirigió a
    login) — posiblemente abrió una pestaña nueva con un PDF y el
    contexto de Playwright se confundió, o el patrón de click disparó
    algo que el backend interpretó como sospechoso. **La implementación
    debe investigar esto con cuidado** (probar `page.waitForEvent('popup')`
    en vez de asumir un `download` event, revisar si hace falta un
    gesto de usuario real en vez de `.click()` vía `page.evaluate`,
    etc.) antes de asumir que funciona iterando las 4 tarjetas × 12
    meses en producción — mismo tipo de investigación empírica que hizo
    falta para el timeout de descarga de resúmenes en Galicia.
  - Las tarjetas de **débito probablemente no tienen resúmenes**
    (están ligadas a una cuenta, no tienen ciclo de facturación) — a
    confirmar en la implementación; si el tab "Resúmenes" no existe
    para una tarjeta, tratarlo como "cero resúmenes" (no como error),
    mismo patrón que la tarjeta sin pestaña "Resumen" en Galicia.
- **Préstamos** (`/loans/list` → cada préstamo con `href` real a
  `/loans/<hash-opaco>`): 2 préstamos reales en la cuenta de prueba.
  La página de detalle tiene un tab "Cuotas" con filtros (Todas/Paga/
  Vencida/A vencer); con el filtro **"Todas"** seleccionado, la misma
  tabla HTML estándar (Cuota/Vencimiento/Estado/Monto) muestra el
  **historial completo** de cuotas del préstamo (56 de 56 cuotas en la
  prueba, sin paginación visible) — no hace falta "Mostrar más" como en
  las tarjetas de Galicia. También hay un botón "Descargar listado"
  (mismo patrón de menú CSV/XLS/PDF que cuentas) — mismo criterio: no
  usar, parsear la tabla HTML en su lugar.
- **Las tres secciones (cuentas, tarjetas, préstamos) comparten el
  mismo componente de tabla React** (mismas clases `sc-eJwWBN`/
  `sc-gVFdte`/etc.) — un solo parser genérico de
  `<table><tbody><tr><td>` sirve para las tres, con distinta cantidad
  de columnas.

## Prerequisitos (ya resueltos por el proyecto Galicia)

Playwright+Chromium, `beautifulsoup4`, `requests`, y `google_api.py`
ya están operativos en la imagen de hermes — no hace falta ningún
prerequisito nuevo para este script.

## Almacenamiento y credenciales

Variables de entorno (Coolify → Environment Variables del recurso
`hermes-agent-infra`, marcadas como Secret):

```
BNA_DNI
BNA_USER
BNA_PASSWORD
```

El script las lee con `os.environ["BNA_..."]` — nunca se imprimen,
nunca se loguean, nunca aparecen en el código fuente.

## Estructura en Drive

Carpeta raíz ya creada: `Bancos` (Drive folder ID:
`1wNYrxgVJo6MPN4kc7aZK3q8qi9s9ZlJS`).

```
Bancos/
  BNA/
    Cuentas/
      cta_sueldo_pesos.csv          (movimientos, acumulativo, dedupe por fecha+comprobante+concepto+monto)
      saldo_cta_sueldo_pesos.csv    (snapshot diario: fecha, saldo)
      ca_pesos.csv
      saldo_ca_pesos.csv
      ca_dolares.csv
      saldo_ca_dolares.csv
    Tarjetas/
      tarjeta_3798.csv              (movimientos, acumulativo)
      tarjeta_0685.csv
      Resumenes/
        3798_<fecha>.pdf
        0685_<fecha>.pdf
    Prestamos/
      prestamo_0014682194.csv       (historial completo de cuotas, acumulativo)
      prestamo_0038886020.csv
```

Los nombres se derivan de lo que el script descubre en cada corrida —
slugificados (minúsculas, espacios a `_`, sin tildes), mismo `slugify`
ya usado en `galicia_sync.py`. Las tarjetas de débito (sin resumen ni
ciclo de facturación) sólo generan su CSV de movimientos si tienen
movimientos — de lo contrario no se crea archivo.

## Arquitectura del script

Un solo archivo Python (`bna_sync.py`), estructurado igual que
`galicia_sync.py`: funciones de responsabilidad única, script lineal
(login → extraer cada sección → logout), reutilizando el mismo patrón
de `sync_csv` genérico (dedupe por tupla completa de fila,
delete-then-upload) — sin duplicar esa lógica, factorizarla a un
módulo compartido si el segundo script hace evidente que vale la pena
(a decidir en la fase de implementación, no antes).

### Flujo principal

1. **Login (2 pasos)**: navega a `digital.bna.com.ar/loginStep1`,
   tipea DNI+Usuario con `.type(delay=30)`, click en continuar; en
   `/loginStep2`, tipea la contraseña, click en continuar. Fallo de
   login = seguir en `/loginStep1` o `/loginStep2`, o un mensaje de
   error visible — mismo contrato que Galicia: imprimir una línea
   corta a stdout y `sys.exit(1)` sin reintentar.
2. **Descubrimiento de cuentas**: parsear
   `/accounts/myaccounts`, un `discover_accounts()` que enumera cada
   `button#account_card_number_{i}`, extrae nombre/últimos 4 dígitos,
   y guarda su índice para navegar a la URL real de detalle.
3. **Por cada cuenta**: click para navegar a su URL de detalle, parsear
   la tabla de movimientos (misma tabla genérica), dedupe+upload igual
   que Galicia; parsear el saldo mostrado en la página de detalle y
   syncear un snapshot diario (`saldo_<cuenta>.csv`, una fila por día).
4. **Descubrimiento de tarjetas**: parsear `/cards`, enumerar cada
   `button#card-{i}`, distinguir Crédito de Débito por el texto visible.
5. **Por cada tarjeta**: navegar a su URL de detalle, tab
   "Movimientos" → parsear tabla, dedupe+upload. Si es de Crédito
   (o si el tab "Resúmenes" existe), ir al tab "Resúmenes", investigar
   empíricamente el mecanismo de descarga real (ver hallazgo de la
   sesión cortada arriba) antes de asumir un patrón, y sincronizar los
   PDFs no descargados aún (dedupe por nombre de archivo, igual que
   Galicia).
6. **Descubrimiento de préstamos**: parsear `/loans/list`, enumerar
   cada préstamo por su `href` real.
7. **Por cada préstamo**: navegar a su URL de detalle, seleccionar el
   filtro "Todas", parsear la tabla completa de cuotas, dedupe+upload
   a `prestamo_<numero>.csv`.
8. **Logout**: click en "Salir" (dentro del menú de usuario, hace falta
   abrirlo primero — confirmado durante el reconocimiento) — dentro de
   un `finally`.
9. **Éxito silencioso** y **manejo de errores por sección** — mismo
   contrato exacto que Galicia (un fallo puntual no aborta el resto,
   se imprime un resumen de fallos parciales al final).

## Registro del cron job

```bash
hermes cron create "0 12 * * *" \
  --name "Sync BNA" \
  --script bna_sync.py \
  --no-agent \
  --deliver origin
```

(`0 12 * * *` UTC = 9am Argentina — mismo horario y misma lógica de
huso horario que "Sync Galicia", para no pisarlos entre sí ambos se
pueden correr a horarios ligeramente distintos si en la práctica
compiten por recursos; a decidir en la implementación si hace falta
espaciarlos.)

Igual que Galicia: el script se versiona en este repo pero debe
copiarse manualmente a `/opt/data/scripts/bna_sync.py` (volumen
persistente `hermes-data`) — no hay paso de deploy automático.

## Testing / validación

Mismo procedimiento que Galicia:

1. Correr el script manualmente una vez dentro del contenedor, con
   supervisión humana.
2. Confirmar estructura de Drive sin duplicados.
3. Correr una segunda vez seguida, confirmar dedupe (sin cambios en
   Drive).
4. Confirmar logout real.
5. Prestar especial atención al mecanismo de descarga de resúmenes de
   tarjeta (el punto más incierto de este diseño) — si tras
   investigarlo empíricamente resulta demasiado frágil o riesgoso
   (ej. sigue cortando la sesión), es aceptable entregar este proyecto
   sin resúmenes de tarjeta en PDF (cuentas + movimientos + préstamos
   igual aportan valor) y dejarlo documentado como limitación conocida
   en vez de forzar una solución fragile.
6. Recién después, registrar el cron job real.

## Rollback

`hermes cron remove "Sync BNA"`, borrar
`/opt/data/scripts/bna_sync.py`. Aislado del resto del despliegue,
igual que Galicia.
