# app/utils/password_reset.py
import hashlib

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def _salt():
    return current_app.config.get("PASSWORD_RESET_SALT", "literaweb-reset-senha")


def _fingerprint_usuario(usuario) -> str:
    base = f"{usuario.id}|{usuario.email or ''}|{usuario.hash_senha or ''}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def gerar_token_reset(usuario) -> str:
    payload = {
        "uid": usuario.id,
        "fp": _fingerprint_usuario(usuario),
    }
    return _serializer().dumps(payload, salt=_salt())


def validar_token_reset(token: str, max_age: int = 3600):
    try:
        data = _serializer().loads(token, salt=_salt(), max_age=max_age)
        return data, None
    except SignatureExpired:
        return None, "O link expirou. Solicite outro."
    except BadSignature:
        return None, "Link inválido."


def token_confere_com_usuario(usuario, data: dict) -> bool:
    return data.get("fp") == _fingerprint_usuario(usuario)