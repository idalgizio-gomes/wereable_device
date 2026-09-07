from __future__ import annotations

import http
import ipaddress
import os
import ssl
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

WS_HOST = "localhost"
WS_PORT = 8765

WS_TLS_ENABLED = os.environ.get("CAREWEAR_WS_TLS", "0") == "1"
WS_TLS_CERT_PATH = Path(__file__).parent / "tls_cert.pem"
WS_TLS_KEY_PATH = Path(__file__).parent / "tls_key.pem"

WS_TOKEN = os.environ.get("CAREWEAR_WS_TOKEN")


def ensure_tls_cert() -> None:
    if WS_TLS_CERT_PATH.exists() and WS_TLS_KEY_PATH.exists():
        return
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "carewear-bridge-local")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.IPAddress(ipaddress.ip_address("::1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    WS_TLS_KEY_PATH.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    WS_TLS_CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"[BRIDGE] certificado TLS autoassinado gerado em {WS_TLS_CERT_PATH}")


def build_ssl_context() -> Optional[ssl.SSLContext]:
    if not WS_TLS_ENABLED:
        return None
    ensure_tls_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(WS_TLS_CERT_PATH), keyfile=str(WS_TLS_KEY_PATH))
    return ctx


async def process_request(connection, request, sa=None, auth_sessions=None):
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.path).query)

    if WS_TOKEN:
        presented = query.get("token", [None])[0]
        if presented != WS_TOKEN:
            return connection.respond(http.HTTPStatus.UNAUTHORIZED, "token invalido ou ausente\n")

    session_token = query.get("session", [None])[0]
    connection._carewear_user_id = None
    if session_token and auth_sessions is not None and sa is not None:
        db = sa.get_db_session()
        try:
            user = auth_sessions.resolve_session(db, session_token)
            connection._carewear_user_id = user.id if user else None
        finally:
            db.close()
    return None
