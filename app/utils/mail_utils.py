# app/utils/mail_utils.py
import smtplib
from email.message import EmailMessage

from flask import current_app, url_for


def enviar_email_redefinicao(usuario, token: str):
    link = url_for("auth.redefinir_senha", token=token, _external=True)

    msg = EmailMessage()
    msg["Subject"] = "Redefinição de senha - LiteraWEB"
    msg["From"] = current_app.config["MAIL_FROM"]
    msg["To"] = usuario.email

    msg.set_content(
        f"""Olá, {usuario.nome_usuario}!

Recebemos uma solicitação para redefinir sua senha no LiteraWEB.

Acesse o link abaixo para cadastrar uma nova senha:
{link}

Se você não solicitou isso, ignore este e-mail.

Este link expira em 1 hora.
"""
    )

    host = current_app.config["MAIL_HOST"]
    port = int(current_app.config.get("MAIL_PORT", 587))
    username = current_app.config.get("MAIL_USERNAME")
    password = current_app.config.get("MAIL_PASSWORD")
    use_tls = bool(current_app.config.get("MAIL_USE_TLS", True))
    use_ssl = bool(current_app.config.get("MAIL_USE_SSL", False))

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
            if username and password:
                smtp.login(username, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(msg)