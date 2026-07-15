import os
from urllib.parse import quote_plus
from flask import Flask, session, request
from dotenv import load_dotenv
from app.models.notificacao import Notificacao


from .extensions import bd, oauth  # usa SOMENTE estes


def create_app():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    load_dotenv(os.path.join(base_dir, ".env"))

    app = Flask(__name__, template_folder="templates", static_folder="static")

    # =========================
    # CONFIGS BÁSICAS
    # =========================
    app.config["SECRET_KEY"] = os.getenv("CHAVE_SECRETA", "dev-secret")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # 4MB

    # reCAPTCHA
    app.config["RECAPTCHA_SITE_KEY"] = os.getenv("RECAPTCHA_SITE_KEY", "")
    app.config["RECAPTCHA_SECRET_KEY"] = os.getenv("RECAPTCHA_SECRET_KEY", "")

    # E-mail / reset de senha
    app.config["MAIL_HOST"] = os.getenv("MAIL_HOST", "")
    app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 587))
    app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    app.config["MAIL_USE_SSL"] = os.getenv("MAIL_USE_SSL", "false").lower() == "true"
    app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "")
    app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "")
    app.config["MAIL_FROM"] = os.getenv("MAIL_FROM", "")
    app.config["PASSWORD_RESET_SALT"] = os.getenv("PASSWORD_RESET_SALT", "literaweb-reset-senha")


    # =========================
    # MYSQL (UTC NA CONEXÃO)
    # =========================
    bd_usuario = os.getenv("BD_USUARIO")
    bd_senha = os.getenv("BD_SENHA")
    bd_host = os.getenv("BD_HOST", "localhost")
    bd_nome = os.getenv("BD_NOME")
    bd_porta = os.getenv("BD_PORTA")  # opcional

    if not bd_usuario or not bd_senha or not bd_nome:
        raise RuntimeError(
            "Config do BD faltando no .env: BD_USUARIO, BD_SENHA e BD_NOME são obrigatórios."
        )

    usuario_enc = quote_plus(bd_usuario)
    senha_enc = quote_plus(bd_senha)

    if bd_porta:
        app.config["SQLALCHEMY_DATABASE_URI"] = (
            f"mysql+pymysql://{usuario_enc}:{senha_enc}@{bd_host}:{bd_porta}/{bd_nome}?charset=utf8mb4"
        )
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = (
            f"mysql+pymysql://{usuario_enc}:{senha_enc}@{bd_host}/{bd_nome}?charset=utf8mb4"
        )

    # Força sessão MySQL em UTC (importante para TIMESTAMP)
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"init_command": "SET time_zone = '+00:00'"}
    }

    # =========================
    # FILTRO JINJA (UTC -> BR)
    # =========================
    from app.utils.datetime_utils import utc_naive_para_br, agora_utc

    def dtbr_filter(value):
        if not value:
            return "—"
        dt = utc_naive_para_br(value)
        return dt.strftime("%d/%m/%Y %H:%M") if dt else "—"

    app.add_template_filter(dtbr_filter, "dtbr")

    # =========================
    # UPLOADS
    # =========================
    app.config["UPLOAD_FOLDER_PERFIS"] = os.path.join(app.static_folder, "uploads", "perfis")
    os.makedirs(app.config["UPLOAD_FOLDER_PERFIS"], exist_ok=True)

    # =========================
    # INIT EXTENSÕES
    # =========================
    bd.init_app(app)

    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

    # =========================
    # PRESENÇA (ONLINE/OFFLINE)
    # =========================

    @app.before_request
    def ping_usuario_online():
        if request.endpoint and request.endpoint.startswith("static"):
            return

        # se estiver logado como funcionário, não marca usuário comum
        if session.get("funcionario_id"):
            return

        # chave de sessão do usuário
        uid = session.get("usuario_id") or session.get("user_id") or session.get("id_usuario")
        if not uid:
            return

        now = agora_utc()

        # limita escrita no banco: 1 update por minuto por sessão
        now_ts = int(now.timestamp())
        last_ts = session.get("_ping_usuario_ts")
        if last_ts and (now_ts - int(last_ts)) < 60:
            return
        session["_ping_usuario_ts"] = now_ts

        try:
            from app.models.usuario import Usuario

            bd.session.query(Usuario).filter(Usuario.id == int(uid)).update(
                {"ultimo_ativo_em": now},
                synchronize_session=False
            )
            bd.session.commit()
        except Exception:
            # não quebra a navegação se o ping falhar
            bd.session.rollback()

    # =========================
    # NOTIFICAÇÕES NO NAVBAR
    # =========================
    @app.context_processor
    def inject_notificacoes_nav():
        # não mostra sino para funcionário
        if session.get("funcionario_id"):
            return {
                "notificacoes_nav": [],
                "notificacoes_nao_lidas": 0,
            }

        usuario_id = (
            session.get("usuario_id")
            or session.get("user_id")
            or session.get("id_usuario")
        )

        if not usuario_id:
            return {
                "notificacoes_nav": [],
                "notificacoes_nao_lidas": 0,
            }

        try:
            from app.models.notificacao import Notificacao

            notificacoes = (
                Notificacao.query
                .filter_by(usuario_id=int(usuario_id))
                .order_by(Notificacao.criada_em.desc(), Notificacao.id.desc())
                .limit(8)
                .all()
            )

            nao_lidas = (
                Notificacao.query
                .filter_by(usuario_id=int(usuario_id), lida=False)
                .count()
            )

            return {
                "notificacoes_nav": notificacoes,
                "notificacoes_nao_lidas": nao_lidas,
            }

        except Exception:
            # não quebra o site se houver qualquer falha
            bd.session.rollback()
            return {
                "notificacoes_nav": [],
                "notificacoes_nao_lidas": 0,
            }

    # =========================
    # BLUEPRINTS
    # =========================
    from .blueprints.main import main_bp
    from .blueprints.auth import auth_bp
    from .blueprints.funcionario import funcionario_bp
    from app.blueprints.forum import forum_bp
    from app.blueprints.funcionario_rag import funcionario_rag_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(funcionario_bp)
    app.register_blueprint(forum_bp)
    app.register_blueprint(funcionario_rag_bp)


    return app