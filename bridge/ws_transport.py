from __future__ import annotations

import http
import ipaddress
import os
import secrets
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

# dev-only: liga a validação de sessão em process_request(). Nunca usar com dados reais.
WS_ALLOW_ANONYMOUS = os.environ.get("CAREWEAR_WS_ALLOW_ANONYMOUS", "0") == "1"


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
    """Handshake do WebSocket: valida o token estático opcional e resolve a sessão do
    utilizador para identidade + perfil (`_carewear_user_role` usado em
    ble_bridge.handle_dashboard_command()). Sessão válida é obrigatória para aceitar a ligação."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.path).query)

    if WS_TOKEN:
        presented = query.get("token", [None])[0] or ""
        if not secrets.compare_digest(presented, WS_TOKEN):  # timing-safe compare
            return connection.respond(http.HTTPStatus.UNAUTHORIZED, "token invalido ou ausente\n")

    session_token = query.get("session", [None])[0]
    connection._carewear_user_id = None
    connection._carewear_user_role = None

    if auth_sessions is None or sa is None:  # sem ORM, falha fechada
        if WS_ALLOW_ANONYMOUS:
            print("[BRIDGE] AVISO: ORM indisponivel e CAREWEAR_WS_ALLOW_ANONYMOUS=1 "
                  "— ligacao WebSocket aceite SEM autenticacao")
            return None
        return connection.respond(http.HTTPStatus.SERVICE_UNAVAILABLE,
                                  "base de dados indisponivel — sessao nao verificavel\n")

    user = None
    if session_token:
        db = sa.get_db_session()
        try:
            user = auth_sessions.resolve_session(db, session_token)
            if user is not None:
                connection._carewear_user_id = user.id
                connection._carewear_user_role = user.role
                db.commit()  # persiste last_used_at escrito por resolve_session()
        finally:
            db.close()

    if user is None:
        if WS_ALLOW_ANONYMOUS:
            print("[BRIDGE] AVISO: ligacao WebSocket sem sessao valida aceite "
                  "(CAREWEAR_WS_ALLOW_ANONYMOUS=1)")
            return None
        return connection.respond(http.HTTPStatus.UNAUTHORIZED,
                                  "sessao ausente, invalida ou expirada\n")

    return None
