# Emisión mensual de Facturas C en ARCA (ex-AFIP) — design

## Contexto y motivación

Reemplaza el cron "ARCA - Facturas C mensuales (auto-calculadas)", que
corría en **modo agente** con el CUIT y la contraseña reales
**embebidos en texto plano dentro del prompt del cron** — enviados al
proveedor de inferencia resuelto en cada corrida (confirmado que llegó
al menos una vez a un proveedor de terceros, `kr/deepseek-3.2`, vía el
"combo" de OmniRoute). El job nunca completó una factura real: se
atascaba reiteradamente en el login por falta de un Chromium utilizable
en el contenedor `hermes-webui` (librerías de sistema faltantes, sin
relación con el sitio de ARCA en sí).

**Cambio de rumbo respecto al intento original:** en vez de seguir
automatizando el **portal web** de ARCA (pensado para humanos, basado
en JSF con ViewState, sensible a timing/CAPTCHA), este diseño usa el
**servicio web oficial de Factura Electrónica de ARCA (WSFEv1)** —
la misma API que usa cualquier sistema de facturación comercial
(Tango, Bejerman, etc.). Es más robusto (sin scraping de HTML, sin
depender de que el DOM del portal no cambie) y es el camino soportado
oficialmente para automatización.

## Prerequisito manual (solo lo puede hacer el titular del CUIT)

WSFEv1 se autentica con un **certificado digital** (par `.crt`/`.key`),
no con usuario/contraseña. Generarlo requiere Clave Fiscal nivel 3 y
son pasos que **solo Rodrigo puede hacer**, dentro de su propia sesión
de ARCA (no automatizable, ni por mí ni por Hermes):

1. Entrar a ARCA → "Administrador de Relaciones de Clave Fiscal".
2. Generar un certificado digital (nuevo par clave/certificado) —
   ARCA lo emite asociado al CUIT.
3. En el mismo administrador, **asociar el servicio "wsfe"** (Factura
   Electrónica) a ese certificado — sin este paso, WSAA rechaza el
   ticket de acceso aunque el certificado sea válido.
4. Descargar el `.crt` (certificado) y conservar la `.key` (clave
   privada) generada en el paso 2 — **la clave privada nunca se sube a
   ningún lado de ARCA, solo se genera una vez, localmente**.

Referencia oficial: [Webservices de factura electrónica — documentación ARCA](https://www.afip.gob.ar/ws/documentacion/ws-factura-electronica.asp),
[Manual para el desarrollador (PDF)](https://www.afip.gob.ar/ws/documentacion/manuales/manual-desarrollador-ARCA-COMPG-v4-0.pdf).

## Arquitectura

Dos servicios SOAP encadenados, igual patrón que cualquier integrador
de WSFE (referencia real: [pyafipws](https://github.com/reingart/pyafipws),
proyecto open-source maduro usado en producción por numerosos sistemas
de facturación argentinos desde 2010 — no reinventar el protocolo,
pero sí escribir un cliente propio, simple y auditable, en vez de
depender de esa librería completa, que trae dependencias viejas
(`pysimplesoap`, `M2Crypto`) con dudosa compatibilidad con Python 3.13):

1. **WSAA (autenticación)** — un flujo de una sola llamada, repetido
   cada vez que el ticket expira (~12hs de validez):
   - Construir un XML pequeño ("Ticket de Requerimiento de Acceso",
     TRA): `uniqueId` (timestamp), `generationTime`/`expirationTime`
     (ventana de validez corta), y `service` = `"wsfe"`.
   - Firmarlo como **CMS/PKCS7 detached** con la clave privada +
     certificado del usuario. No hace falta M2Crypto: `openssl smime
     -sign -signer cert.crt -inkey clave.key -outform DER` (binario
     `openssl`, ya presente en la imagen Debian de este contenedor)
     produce exactamente lo que WSAA espera.
   - Base64 del CMS firmado → body de un POST SOAP a
     `LoginCms` en la URL de WSAA (`https://wsaa.afip.gov.ar/ws/services/LoginCms`
     en producción; hay un endpoint de homologación/pruebas separado,
     `wsaahomo.afip.gov.ar`, con su propio certificado de prueba — NO
     sirve para facturar de verdad, es solo para testear el código sin
     tocar el circuito real).
   - Respuesta: un `Token` + `Sign` (el "ticket de acceso"), válidos
     ~12hs — cachear en disco (`/opt/data/arca_ta.json` o similar) para
     no re-autenticar en cada factura de la corrida.

2. **WSFEv1 (facturación)** — con el Token/Sign del paso anterior:
   - `FECompUltimoAutorizado(PtoVta=1, CbteTipo=11)` — Factura C es
     `CbteTipo=11` — para obtener el último número de comprobante
     emitido y calcular el siguiente (`CbteDesde = CbteHasta = último + 1`).
   - `FECAESolicitar(...)` — el llamado real que emite la factura y
     devuelve el **CAE** (Código de Autorización Electrónico) y su
     vencimiento. Este es el único paso que efectivamente "genera" la
     factura ante ARCA — antes de esto, cualquier cálculo/preparación
     es local y reversible.
   - Campos confirmados por el prompt original del cron (a mapear 1:1
     a los parámetros de `FECAESolicitar`): `PtoVta=1`, `CbteTipo=11`
     (Factura C), `Concepto=2` (Servicios), `DocTipo=99` /
     `DocNro=0` (Consumidor Final sin CUIT), `ImpTotal`/`ImpNeto` =
     monto calculado (sin IVA discriminado en Factura C), `CondicionIVAReceptorId`
     acorde a "Consumidor Final" (confirmar el código exacto contra
     `FEParamGetCondicionIvaReceptor` antes de la primera emisión real
     — no asumido aquí).

## Cálculo del monto (ya confirmado, sin cambios respecto al cron original)

- Tope anual categoría G monotributo, objetivo 80% de ese tope,
  dividido en 12 meses, dividido en N facturas de hasta $299.999,99
  cada una — misma fórmula que ya tenía el prompt del cron original
  (ver commit histórico del job, o pedirle a Rodrigo los valores
  vigentes si la categoría/tope cambió).

## Credenciales y almacenamiento

```
ARCA_CUIT               # ya existente como concepto — sin cambios
ARCA_CERT_PATH          # ruta al .crt dentro del volumen persistente (NO texto plano en env var)
ARCA_KEY_PATH           # ruta a la .key — mismo criterio
```

El certificado/clave son **archivos**, no van en variables de entorno
de Coolify como texto (a diferencia de `GALICIA_PASSWORD` etc.) —
subirlos directo al volumen persistente `hermes-data` (ej.
`/opt/data/arca/cert.crt`, `/opt/data/arca/clave.key`) vía `scp`+`docker cp`,
igual mecanismo que usamos para desplegar los scripts, nunca pegados
en un prompt ni en este repo.

## Por qué no browser automation

El intento original (agente en modo chat + navegador, y luego mi propio
intento con Playwright interactivo) confirmó que el **login** al portal
funciona bien con un click real — el problema real de esa sesión era el
entorno (`hermes-webui` sin Chromium utilizable), no el sitio. Pero el
**flujo de emisión** (4 pasos de formulario JSF con ViewState) nunca se
reconoció en vivo, y automatizarlo seguiría siendo frágil ante cualquier
cambio de layout del portal — WSFEv1 es un contrato de API estable,
versionado, pensado exactamente para este caso de uso.

## Testing

1. **Homologación primero, sin excepción**: `wsaahomo.afip.gov.ar` +
   un certificado de prueba (ARCA provee certificados de testing
   separados de los de producción) — nunca probar código nuevo contra
   el circuito real directamente.
2. Confirmar `FEDummy()` (chequeo de salud del servicio) responde antes
   de intentar autenticar.
3. Una vez autenticado en homologación, `FECompUltimoAutorizado` de
   prueba (de solo lectura, no genera nada) antes de intentar
   `FECAESolicitar` de prueba.
4. Recién con homologación end-to-end confirmada, y con supervisión
   humana en vivo, pasar a producción — la primera factura real la
   debería confirmar Rodrigo mirando la respuesta (CAE recibido) antes
   de asumir que el resto del lote (10-12 facturas/mes) puede correr
   desatendido.

## Estado actual

- `scripts/arca_facturas.py` (commit `774f32b`) todavía apunta al
  enfoque de browser automation (login confirmado real, emisión sin
  implementar) — **a reemplazar** por el cliente WSFEv1 descripto acá
  una vez exista el certificado.
- **Bloqueante real, no resoluble sin Rodrigo:** el certificado no
  existe todavía. Nadie más puede generarlo (requiere su propia sesión
  de Clave Fiscal nivel 3 en ARCA).
