import os
import secrets
from datetime import date

import requests
from flask import render_template, request, redirect, url_for, session, flash, current_app
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import bd, oauth
from app.models.usuario import Usuario
from app.models.funcionario import Funcionario
from app.utils.password_reset import (
    gerar_token_reset,
    validar_token_reset,
    token_confere_com_usuario,
)
from app.utils.mail_utils import enviar_email_redefinicao
from . import auth_bp


def validar_recaptcha(token: str, remoteip: str | None = None) -> tuple[bool, str]:
    secret = os.getenv("RECAPTCHA_SECRET_KEY", "").strip()

    if not secret:
        return False, "RECAPTCHA_SECRET_KEY não está configurado no .env"

    if not token:
        return False, "Marque o 'Não sou um robô'."

    data = {
        "secret": secret,
        "response": token,
    }
    if remoteip:
        data["remoteip"] = remoteip

    try:
        r = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data=data,
            timeout=8
        )
        result = r.json()
    except Exception as e:
        return False, f"Falha ao validar reCAPTCHA (rede). Detalhe: {e}"

    if result.get("success") is True:
        return True, "ok"

    codes = result.get("error-codes") or []
    return False, f"reCAPTCHA inválido. Código(s): {', '.join(codes) if codes else 'desconhecido'}"


# =========================
# CADASTRO (NORMAL)
# =========================
@auth_bp.get("/cadastro")
def cadastro():
    return render_template(
        "auth/cadastro.html",
        RECAPTCHA_SITE_KEY=os.getenv("RECAPTCHA_SITE_KEY", ""),
        error=None
    )


@auth_bp.post("/cadastro")
def cadastro_post():
    # Campos
    nome_usuario = (request.form.get("nome") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    senha = request.form.get("senha") or ""
    genero = (request.form.get("genero") or "").strip()

    mes_raw = request.form.get("mes") or ""
    dia_raw = request.form.get("dia") or ""
    ano_raw = request.form.get("ano") or ""

    # Função para renderizar reaproveitando os valores digitados
    def voltar_com_erro(msg: str):
        return render_template(
            "auth/cadastro.html",
            RECAPTCHA_SITE_KEY=os.getenv("RECAPTCHA_SITE_KEY", ""),
            error=msg,
            nome=nome_usuario,
            email=email,
            genero=genero,
            mes=mes_raw,
            dia=dia_raw,
            ano=ano_raw,
        )

    # =========================
    # reCAPTCHA (BACK-END)
    # =========================
    recaptcha_token = request.form.get("g-recaptcha-response", "")
    ok, msg = validar_recaptcha(recaptcha_token, request.remote_addr)

    if not ok:
        print("reCAPTCHA FAIL:", msg)
        return voltar_com_erro(msg)

    # Data nascimento
    try:
        mes = int(mes_raw or 0)
        dia = int(dia_raw or 0)
        ano = int(ano_raw or 0)
        data_nascimento = date(ano, mes, dia)
    except Exception:
        return voltar_com_erro("Data de nascimento inválida. Preencha corretamente.")

    # Validações básicas
    if len(nome_usuario) < 3:
        return voltar_com_erro("Nome de usuário muito curto.")

    if len(senha) < 8:
        return voltar_com_erro("A senha deve ter pelo menos 8 caracteres.")

    if genero not in ("F", "M"):
        return voltar_com_erro("Selecione um gênero.")

    #  EVITA conflito: se o email é de funcionário, não pode cadastrar como usuário
    if Funcionario.query.filter_by(email=email).first():
        return voltar_com_erro("Este e-mail já esta cadastrado, tente novamente com outro.")

    if Usuario.query.filter_by(email=email).first():
        return voltar_com_erro("Esse e-mail já está cadastrado.")

    if Usuario.query.filter_by(nome_usuario=nome_usuario).first():
        return voltar_com_erro("Esse nome de usuário já existe.")

    # Cria usuário
    hash_senha = generate_password_hash(senha)

    usuario = Usuario(
        nome_usuario=nome_usuario,
        email=email,
        hash_senha=hash_senha,
        genero=genero,
        data_nascimento=data_nascimento,
        google_id=None,
    )

    bd.session.add(usuario)
    bd.session.commit()

    # Auto-login após cadastrar
    session.clear()
    session["usuario_id"] = usuario.id
    session["nome_usuario"] = usuario.nome_usuario

    return redirect(url_for("main.home"))


# =========================
# LOGIN (MESMA TELA: USUÁRIO OU FUNCIONÁRIO)
# =========================
@auth_bp.get("/login")
def login():
    return render_template("auth/login.html", email="", error=None)


@auth_bp.post("/login")
def login_post():
    email = (request.form.get("email") or "").strip().lower()
    senha = request.form.get("senha") or ""

    # =========================
    # 1) TENTA FUNCIONÁRIO PRIMEIRO
    # =========================
    func = Funcionario.query.filter_by(email=email).first()
    if func:
        # Se existe funcionário com esse email, NÃO tenta usuário (mais seguro)
        if (not func.ativo) or (not check_password_hash(func.hash_senha, senha)):
            return render_template("auth/login.html", error="E-mail ou senha inválidos.", email=email)

        session.clear()
        session["funcionario_id"] = func.id
        session["funcionario_nome"] = func.nome
        session["funcionario_email"] = func.email

        return redirect(url_for("funcionario.painel"))

    # =========================
    # 2) SE NÃO FOR FUNCIONÁRIO, TENTA USUÁRIO NORMAL
    # =========================
    usuario = Usuario.query.filter_by(email=email).first()

    if not usuario:
        return render_template("auth/login.html", error="E-mail ou senha inválidos.", email=email)

    # bloqueia conta apagada
    status = (getattr(usuario, "status", "") or "").strip().lower()
    if status == "apagado":
        return render_template(
            "auth/login.html",
            error="Esta conta não está mais disponível para acesso.",
            email=email
        )

    # Se conta é do Google e não tem senha local
    if not usuario.hash_senha:
        return render_template(
            "auth/login.html",
            error="Essa conta foi criada com Google. Use 'Continue com o Google'.",
            email=email
        )

    # Verifica senha
    if not check_password_hash(usuario.hash_senha, senha):
        return render_template("auth/login.html", error="E-mail ou senha inválidos.", email=email)

    # Login OK
    session.clear()

    session["usuario_id"] = usuario.id
    session["nome_usuario"] = usuario.nome_usuario

    # FOTO: caminho relativo (ex: "uploads/perfis/abc.webp")
    foto = (getattr(usuario, "foto", None) or "").strip()
    if foto:
        session["usuario_foto"] = foto
    else:
        session.pop("usuario_foto", None)

    print("FOTO NO LOGIN:", repr(usuario.foto), "| NORMALIZADA:", repr(foto), "| SESSION:",
          repr(session.get("usuario_foto")))

    return redirect(url_for("main.home"))


@auth_bp.get("/sair")
def sair():
    session.clear()
    return redirect(url_for("main.home"))


# =========================
# LOGIN COM GOOGLE (OAUTH)
# =========================
@auth_bp.get("/login/google")
def login_google():
    # Gera nonce e guarda na sessão (exigido pelo parse_id_token em algumas versões)
    nonce = secrets.token_urlsafe(16)
    session["google_nonce"] = nonce

    redirect_uri = url_for("auth.google_callback", _external=True)

    # Envia o nonce junto na autorização (OpenID Connect)
    return oauth.google.authorize_redirect(
        redirect_uri,
        nonce=nonce
    )


@auth_bp.get("/login/google/callback")
def google_callback():
    # Recupera nonce salvo
    nonce = session.pop("google_nonce", None)

    token = oauth.google.authorize_access_token()

    # Agora passa o nonce
    userinfo = oauth.google.parse_id_token(token, nonce=nonce)

    email = (userinfo.get("email") or "").lower().strip()
    nome = (userinfo.get("name") or "").strip()
    google_sub = (userinfo.get("sub") or "").strip()

    if not email or not google_sub:
        return redirect(url_for("auth.login"))

    usuario = Usuario.query.filter_by(google_id=google_sub).first()
    if not usuario:
        usuario = Usuario.query.filter_by(email=email).first()

    # Se o email é de funcionário, bloqueia login Google para não virar usuário comum
    if Funcionario.query.filter_by(email=email).first():
        return redirect(url_for("auth.login"))

    # Se já existe usuário e está apagado, não permite entrar
    if usuario:
        status = (getattr(usuario, "status", "") or "").strip().lower()
        if status == "apagado":
            flash("Esta conta não está mais disponível para acesso.", "danger")
            return redirect(url_for("auth.login"))

    if not usuario:
        base = (nome.split(" ")[0] if nome else email.split("@")[0])[:20]
        candidato = base or "usuario"
        i = 0
        while Usuario.query.filter_by(nome_usuario=candidato).first():
            i += 1
            candidato = f"{base}{i}"[:50]

        usuario = Usuario(
            nome_usuario=candidato,
            email=email,
            google_id=google_sub,
            hash_senha=None,
            genero=None,
            data_nascimento=None,
        )
        bd.session.add(usuario)
        bd.session.commit()

    if not usuario.google_id:
        usuario.google_id = google_sub
        bd.session.commit()

    session.clear()
    session["usuario_id"] = usuario.id
    session["nome_usuario"] = usuario.nome_usuario

    # FOTO: caminho relativo (ex: "uploads/perfis/abc.webp")
    foto = (getattr(usuario, "foto", None) or "").strip()
    if foto:
        session["usuario_foto"] = foto
    else:
        session.pop("usuario_foto", None)

    return redirect(url_for("main.home"))

# =========================
# ESQUECI MINHA SENHA
# =========================
@auth_bp.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()

        try:
            usuario = Usuario.query.filter_by(email=email).first()

            # não revela se o e-mail existe ou não
            if usuario:
                status = (getattr(usuario, "status", "") or "").strip().lower()

                # não envia para conta apagada
                if status != "apagado":
                    token = gerar_token_reset(usuario)
                    enviar_email_redefinicao(usuario, token)

        except Exception:
            current_app.logger.exception("Erro ao enviar e-mail de redefinição.")

        flash(
            "Se existir uma conta com esse e-mail, enviaremos um link para redefinir a senha.",
            "info"
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/esqueci_senha.html", email="", error=None)


# =========================
# REDEFINIR SENHA POR TOKEN
# =========================
@auth_bp.route("/redefinir-senha/<token>", methods=["GET", "POST"])
def redefinir_senha(token):
    data, erro_token = validar_token_reset(token, max_age=3600)

    if erro_token:
        return render_template(
            "auth/redefinir_senha.html",
            token=token,
            error=erro_token
        )

    usuario = bd.session.get(Usuario, data["uid"])
    if not usuario:
        return render_template(
            "auth/redefinir_senha.html",
            token=token,
            error="Link inválido."
        )

    status = (getattr(usuario, "status", "") or "").strip().lower()
    if status == "apagado":
        return render_template(
            "auth/redefinir_senha.html",
            token=token,
            error="Esta conta não está disponível."
        )

    if not token_confere_com_usuario(usuario, data):
        return render_template(
            "auth/redefinir_senha.html",
            token=token,
            error="Este link não é mais válido. Solicite outro."
        )

    if request.method == "POST":
        senha = request.form.get("senha") or ""
        confirmar_senha = request.form.get("confirmar_senha") or ""

        if len(senha) < 8:
            return render_template(
                "auth/redefinir_senha.html",
                token=token,
                error="A nova senha deve ter pelo menos 8 caracteres."
            )

        if senha != confirmar_senha:
            return render_template(
                "auth/redefinir_senha.html",
                token=token,
                error="As senhas não coincidem."
            )

        usuario.hash_senha = generate_password_hash(senha)
        bd.session.commit()

        flash("Senha redefinida com sucesso. Faça login com a nova senha.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/redefinir_senha.html", token=token, error=None)