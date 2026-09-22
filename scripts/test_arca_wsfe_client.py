#!/usr/bin/env python3
"""Tests for the parts of arca_wsfe_client.py that don't need a real
certificate or network access: TRA construction and the credential-file
error paths. The actual WSAA/WSFEv1 network calls can only be verified
against homologación once a certificate exists (see
docs/superpowers/specs/2026-09-22-arca-facturas-design.md)."""
import os
import tempfile
from datetime import datetime
from xml.etree import ElementTree as ET

import pytest

from arca_wsfe_client import ArcaError, crear_tra, firmar_tra


def test_crear_tra_produces_well_formed_xml_with_expected_service():
    tra = crear_tra(service="wsfe")
    root = ET.fromstring(tra)
    assert root.tag == "loginTicketRequest"
    assert root.findtext("service") == "wsfe"
    assert root.findtext("header/uniqueId")


def test_crear_tra_expiration_is_after_generation():
    tra = crear_tra(ttl_seconds=2400)
    root = ET.fromstring(tra)
    generation = datetime.strptime(root.findtext("header/generationTime"), "%Y-%m-%dT%H:%M:%S%z")
    expiration = datetime.strptime(root.findtext("header/expirationTime"), "%Y-%m-%dT%H:%M:%S%z")
    assert expiration > generation


def test_firmar_tra_raises_arca_error_when_cert_missing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        missing_cert = os.path.join(tmp_dir, "no_such_cert.crt")
        missing_key = os.path.join(tmp_dir, "no_such_key.key")
        with pytest.raises(ArcaError, match="certificado"):
            firmar_tra(b"<x/>", missing_cert, missing_key)


def test_firmar_tra_raises_arca_error_when_key_missing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        cert_path = os.path.join(tmp_dir, "cert.crt")
        with open(cert_path, "w") as f:
            f.write("not a real cert, just needs to exist")
        missing_key = os.path.join(tmp_dir, "no_such_key.key")
        with pytest.raises(ArcaError, match="clave privada"):
            firmar_tra(b"<x/>", cert_path, missing_key)


if __name__ == "__main__":
    test_crear_tra_produces_well_formed_xml_with_expected_service()
    test_crear_tra_expiration_is_after_generation()
    test_firmar_tra_raises_arca_error_when_cert_missing()
    test_firmar_tra_raises_arca_error_when_key_missing()
    print("OK: all arca_wsfe_client tests passed")
