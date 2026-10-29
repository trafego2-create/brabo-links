"""Senha (PBKDF2) e sessão em cookie assinado (HMAC), sem dependência externa."""
import base64
import hashlib
import hmac
import json
import secrets
import time

from . import config

_ITER = 240_000


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), _ITER).hex()
    return f"pbkdf2${_ITER}${salt}${dk}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), int(it)).hex()
        return hmac.compare_digest(got, dk)
    except (ValueError, AttributeError):
        return False


def _sign(payload: bytes) -> str:
    return hmac.new(config.SESSION_SECRET.encode(), payload, hashlib.sha256).hexdigest()


def make_session(user_id: int) -> str:
    data = {"u": user_id, "exp": int(time.time()) + config.SESSION_DAYS * 86400,
            "csrf": secrets.token_urlsafe(16)}
    # sem "=" de padding: com ele o cookie sai entre aspas e o navegador devolve as aspas
    raw = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=")
    return raw.decode() + "." + _sign(raw)


def read_session(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(raw.encode()), sig):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode() + b"=" * (-len(raw) % 4)))
    except ValueError:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data
