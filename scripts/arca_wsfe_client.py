#!/usr/bin/env python3
"""Minimal WSAA + WSFEv1 client for ARCA (ex-AFIP) electronic invoicing.

Implements the two chained SOAP services needed to emit a Factura C —
see docs/superpowers/specs/2026-09-22-arca-facturas-design.md for the
full protocol writeup and why this replaces browser automation of the
RCEL portal.

- WSAA (Web Service de Autenticación y Autorización): signs a small
  XML "ticket request" (TRA) as a detached CMS/PKCS7 blob using the
  CUIT holder's certificate + private key, and exchanges it for a
  short-lived Token/Sign pair. Signing shells out to the system
  `openssl` binary (already present in this Debian image) rather than
  depending on a Python crypto binding — this mirrors what the
  reference implementation (github.com/reingart/pyafipws) does when
  M2Crypto isn't available, and keeps this file dependency-free.
- WSFEv1 (Web Service de Factura Electrónica v1): the actual invoicing
  calls, authenticated with the Token/Sign from WSAA. Uses raw SOAP 1.1
  envelopes over `requests` rather than a SOAP client library (zeep/
  pysimplesoap) — this service's contract is small and stable enough
  that a full SOAP client is unnecessary weight, and raw XML keeps
  every request auditable before it goes anywhere near a real tax
  authority.

STATUS: written from the publicly documented WSAA/WSFEv1 protocol and
cross-checked against pyafipws's real, in-production implementation —
but NEVER RUN, because no certificate exists yet (see the design doc's
"Prerequisito manual" section — only the CUIT holder can generate one).
Do not point this at the production endpoints (ARCA_HOMOLOGACION=false)
until it has been exercised successfully against homologación first.
"""
import base64
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import requests

# Homologación (testing) vs producción — confirmed URLs from ARCA's own
# webservice documentation (see design doc). NEVER hardcode a switch to
# producción as the default; require it to be explicit.
WSAA_URL = {
    "homologacion": "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
    "produccion": "https://wsaa.afip.gov.ar/ws/services/LoginCms",
}
WSFEV1_URL = {
    "homologacion": "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
    "produccion": "https://servicios1.afip.gov.ar/wsfev1/service.asmx",
}

class ArcaError(RuntimeError):
    """Raised for any WSAA/WSFEv1-level failure (auth or invoicing)."""


def _environment() -> str:
    env = os.environ.get("ARCA_ENVIRONMENT", "homologacion").strip().lower()
    if env not in WSAA_URL:
        raise ArcaError(f"ARCA_ENVIRONMENT debe ser 'homologacion' o 'produccion', no {env!r}")
    return env


def crear_tra(service: str = "wsfe", ttl_seconds: int = 2400) -> bytes:
    """Builds the Ticket de Requerimiento de Acceso (TRA) XML.

    A short-lived request naming the target service ("wsfe") and a
    validity window — WSAA rejects a TRA outside a small clock skew of
    the server's own time, so ttl_seconds should stay well under the
    ~24min the real service tolerates (2400s = 40min is the value the
    reference implementation uses and is known to work).
    """
    now = datetime.now(timezone.utc)
    generation = now - timedelta(seconds=60)  # small backward skew, matches reference impl
    expiration = now + timedelta(seconds=ttl_seconds)
    unique_id = str(int(now.timestamp()))

    tra = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<loginTicketRequest version="1.0">'
        "<header>"
        f"<uniqueId>{unique_id}</uniqueId>"
        f"<generationTime>{generation.strftime('%Y-%m-%dT%H:%M:%S%z')}</generationTime>"
        f"<expirationTime>{expiration.strftime('%Y-%m-%dT%H:%M:%S%z')}</expirationTime>"
        "</header>"
        f"<service>{service}</service>"
        "</loginTicketRequest>"
    )
    return tra.encode("utf-8")


def firmar_tra(tra_xml: bytes, cert_path: str, key_path: str) -> str:
    """Signs the TRA as detached CMS/PKCS7 (DER, base64-encoded) using
    the system `openssl` binary — the exact transform WSAA expects.

    Equivalent to:
        openssl smime -sign -in tra.xml -signer cert.crt -inkey clave.key
                       -outform DER -nodetach
    then base64-encoding the DER output for the SOAP body.
    """
    if not os.path.isfile(cert_path):
        raise ArcaError(f"No se encontró el certificado en {cert_path}")
    if not os.path.isfile(key_path):
        raise ArcaError(f"No se encontró la clave privada en {key_path}")

    with tempfile.NamedTemporaryFile(suffix=".xml") as tra_file:
        tra_file.write(tra_xml)
        tra_file.flush()
        result = subprocess.run(
            [
                "openssl", "smime", "-sign",
                "-in", tra_file.name,
                "-signer", cert_path,
                "-inkey", key_path,
                "-outform", "DER",
                "-nodetach",
            ],
            capture_output=True, check=False,
        )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise ArcaError(f"openssl smime falló firmando el TRA: {stderr}")
    return base64.b64encode(result.stdout).decode("ascii")


def login_wsaa(cms_b64: str, environment: str) -> tuple[str, str, datetime]:
    """Exchanges a signed TRA for a (Token, Sign, expiration) ticket.

    Returns the raw Token/Sign strings (opaque to us — WSFEv1 just
    wants them echoed back in its own SOAP header) and the ticket's
    expiration time, so the caller can cache and reuse it (~12h
    validity) instead of re-authenticating on every invoice.
    """
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:wsaa="http://wsaa.view.sua.dvadac.desein.afip.gov">'
        "<soapenv:Header/>"
        "<soapenv:Body>"
        "<wsaa:loginCms>"
        f"<wsaa:in0>{cms_b64}</wsaa:in0>"
        "</wsaa:loginCms>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )
    response = requests.post(
        WSAA_URL[environment],
        data=envelope.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise ArcaError(f"WSAA respondió {response.status_code}: {response.text[:500]}")

    envelope_root = ET.fromstring(response.text)
    body_text_elements = envelope_root.findall(".//{*}loginCmsReturn")
    if not body_text_elements or not body_text_elements[0].text:
        raise ArcaError(f"WSAA no devolvió loginCmsReturn: {response.text[:800]}")

    # The ticket itself is XML, escaped inside loginCmsReturn's text content.
    ticket_root = ET.fromstring(body_text_elements[0].text)
    token = ticket_root.findtext(".//token")
    sign = ticket_root.findtext(".//sign")
    expiration_text = ticket_root.findtext(".//header/expirationTime")
    if not token or not sign:
        raise ArcaError(f"Ticket WSAA sin token/sign: {body_text_elements[0].text[:800]}")

    expiration = datetime.fromisoformat(expiration_text) if expiration_text else (
        datetime.now(timezone.utc) + timedelta(hours=1)
    )
    return token, sign, expiration


def fe_dummy(environment: str) -> dict:
    """Health check — confirms WSFEv1's app/db/auth servers are up
    before attempting anything else. Takes no credentials."""
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ar="http://ar.gov.afip.dif.FEV1/">'
        "<soapenv:Header/>"
        "<soapenv:Body><ar:FEDummy/></soapenv:Body>"
        "</soapenv:Envelope>"
    )
    response = requests.post(
        WSFEV1_URL[environment],
        data=envelope.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://ar.gov.afip.dif.FEV1/FEDummy",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise ArcaError(f"FEDummy respondió {response.status_code}: {response.text[:500]}")
    root = ET.fromstring(response.text)
    return {
        "AppServer": root.findtext(".//AppServer"),
        "DbServer": root.findtext(".//DbServer"),
        "AuthServer": root.findtext(".//AuthServer"),
    }


def fe_comp_ultimo_autorizado(token: str, sign: str, cuit: str, environment: str, pto_vta: int, cbte_tipo: int = 11) -> int:
    """Returns the last authorized invoice number for this punto de
    venta + comprobante type (11 = Factura C) — read-only, generates
    nothing. The next invoice's number is this + 1."""
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ar="http://ar.gov.afip.dif.FEV1/">'
        "<soapenv:Header/>"
        "<soapenv:Body>"
        "<ar:FECompUltimoAutorizado>"
        "<ar:Auth>"
        f"<ar:Token>{token}</ar:Token>"
        f"<ar:Sign>{sign}</ar:Sign>"
        f"<ar:Cuit>{cuit}</ar:Cuit>"
        "</ar:Auth>"
        f"<ar:PtoVta>{pto_vta}</ar:PtoVta>"
        f"<ar:CbteTipo>{cbte_tipo}</ar:CbteTipo>"
        "</ar:FECompUltimoAutorizado>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )
    response = requests.post(
        WSFEV1_URL[environment],
        data=envelope.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "http://ar.gov.afip.dif.FEV1/FECompUltimoAutorizado",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise ArcaError(f"FECompUltimoAutorizado respondió {response.status_code}: {response.text[:500]}")
    root = ET.fromstring(response.text)
    error = root.findtext(".//Errors/Err/Msg")
    if error:
        raise ArcaError(f"FECompUltimoAutorizado: {error}")
    numero = root.findtext(".//CbteNro")
    if numero is None:
        raise ArcaError(f"FECompUltimoAutorizado sin CbteNro: {response.text[:800]}")
    return int(numero)


# fe_cae_solicitar (the actual invoice-emitting call) is deliberately
# NOT implemented yet. Its exact field set (CondicionIVAReceptorId in
# particular) needs to be confirmed against FEParamGetCondicionIvaReceptor
# in homologación before being used for real — see the design doc's
# "Testing" section. Do not add a guessed version of this function;
# write it against real homologación responses when that testing
# happens.
