# Diagnóstico de cuentas vacías en BNA+

## Problema

El reconocimiento interactivo realizado con Playwright mostró tres cuentas
reales, pero ejecuciones posteriores de `scripts/bna_sync.py` desde el
contenedor de Hermes mostraron `Mis Cuentas (0)` y terminaron exitosamente sin
crear archivos. La presencia de `navigator.webdriver === true` coincide con el
fallo, pero no demuestra que sea su causa. El reconocimiento exitoso también
usó un navegador automatizado.

El cambio local que elimina `Headless` del User-Agent no recuperó las cuentas.
Por lo tanto, debe retirarse junto con el comentario que afirma lo contrario.

## Objetivo

Determinar con una sola sesión bancaria si la respuesta vacía se origina en
la navegación de la SPA, el estado de autenticación, una respuesta del backend
o una diferencia del entorno de Oracle, sin ocultar indicadores de
automatización y sin registrar credenciales ni datos financieros.

## Cambios previos a la prueba

1. Restaurar el contexto normal de Playwright, sin modificar el User-Agent.
2. Navegar a Cuentas mediante el enlace o botón visible de la SPA, reproduciendo
   el recorrido interactivo que funcionó. No usar `page.goto(ACCOUNTS_URL)` para
   el primer acceso posterior al login.
3. Esperar un resultado semántico: al menos una tarjeta de cuenta, el mensaje
   explícito de cero cuentas o un error visible. `networkidle` por sí solo no
   define que la sección terminó de cargar.
4. Tratar cero cuentas, saldo ausente o una tabla inesperadamente vacía como
   fallo parcial. El proceso debe salir distinto de cero y no escribir CSV
   vacíos.
5. Agregar diagnóstico temporal para solicitudes `fetch`/XHR. Registrar sólo
   método, ruta sin query string, estado HTTP, tipo de recurso y una categoría
   de resultado. No registrar headers, tokens, cuerpos, nombres, saldos,
   movimientos ni identificadores de cuenta.

## Prueba única autorizada

La prueba se ejecutará una vez dentro del contenedor de Hermes con sus
variables de entorno existentes. Se comprobará:

- URL y estado de la página al finalizar el login.
- Resultado de la navegación interna a Cuentas.
- Metadatos sanitizados de todas las solicitudes `fetch`/XHR producidas por
  esa navegación.
- Cantidad de elementos `account_card_number_*`, sin imprimir su contenido.

La ejecución terminará tras esa observación; no recorrerá detalles, no subirá
archivos a Drive y cerrará la sesión mediante el logout existente.

## Interpretación

- Si la navegación interna recupera las cuentas, se reemplazarán las
  recargas directas por navegación SPA y esperas semánticas.
- Si una API responde con error o datos vacíos, se conservará el fallo seguro
  y se diagnosticará esa frontera concreta; no se intentará evadir controles
  antifraude.
- Si no aparece ninguna solicitud de datos, el problema está en el flujo o
  estado cliente y no en el parser HTML.
- Si el mismo flujo funciona fuera de Oracle pero no dentro de Oracle, se
  evaluará ejecutar el cron desde un equipo o red aprobados por el banco, en
  vez de disfrazar el navegador del servidor.

## Criterios de aceptación

- Una sola autenticación en vivo.
- Ningún secreto o dato financiero en stdout, archivos diagnósticos o Git.
- Ninguna manipulación de `navigator.webdriver`, fingerprint o identidad del
  navegador.
- El estado `cero cuentas` nunca vuelve a producir una ejecución silenciosa
  exitosa.
- La causa queda aislada a una frontera verificable o el sistema queda en modo
  de fallo seguro con evidencia suficiente para decidir el siguiente paso.
