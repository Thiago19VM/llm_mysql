# app/blueprints/forum/routes.py
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from app.utils.datetime_utils import utc_naive_para_br
from sqlalchemy import case

from flask import (
    render_template, request, redirect, url_for, flash, session, abort, jsonify
)
from sqlalchemy import or_, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.extensions import bd
from app.models.notificacao import Notificacao
from app.models.usuario import Usuario
from app.models.funcionario import Funcionario
from app.models.livro import Livro
from app.models.forum import ForumSeguidor
from app.models.forum import (
    ForumTopico,
    ForumComentario,
    ForumTopicoView,
    ForumPresenca,
    ForumTopicoUpvote,
    ForumComentarioUpvote,
    ForumComentarioReacao,
    slugify,
)
from app.services.notificacao_service import criar_notificacao

from . import forum_bp


# ============================================================
# Helpers de sessão / auth
# ============================================================

def _usuario_logado():
    uid = session.get("usuario_id")
    if not uid:
        return None
    return Usuario.query.get(uid)


def _funcionario_logado():
    fid = session.get("funcionario_id")
    if not fid:
        return None
    return Funcionario.query.get(fid)


def _pode_moderar() -> bool:
    # funcionário autenticado é moderador
    return bool(session.get("funcionario_id"))


def _login_redirect():
    return redirect(url_for("auth.login"))


def forum_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        uid = session.get("usuario_id")

        if not uid:
            flash("Faça login como usuário para usar o fórum.", "warning")
            return _login_redirect()

        usuario = Usuario.query.get(uid)

        if not usuario:
            session.pop("usuario_id", None)
            session.pop("usuario_nome", None)
            flash("Faça login novamente.", "warning")
            return _login_redirect()

        status = (usuario.status or "").strip().lower()

        if status == "apagado":
            session.pop("usuario_id", None)
            session.pop("usuario_nome", None)
            flash("Esta conta não está mais disponível para acesso.", "danger")
            return _login_redirect()

        if status == "bloqueado":
            flash("Sua conta está bloqueada e não pode acessar o fórum.", "danger")
            return redirect(url_for("main.home"))

        return fn(*args, **kwargs)

    return wrapper


# ============================================================
# Helpers de permissão / visibilidade
# ============================================================

def _eh_dono_topico(usuario, topico: ForumTopico | None) -> bool:
    return bool(usuario and topico and usuario.id == topico.autor_id)


def _eh_dono_comentario(usuario, comentario: ForumComentario | None) -> bool:
    return bool(usuario and comentario and usuario.id == comentario.autor_id)


def _pode_gerenciar_topico(usuario, topico: ForumTopico, pode_moderar: bool) -> bool:
    return bool(pode_moderar or _eh_dono_topico(usuario, topico))


def _pode_ver_topico(usuario, topico: ForumTopico, pode_moderar: bool) -> bool:
    # Tópico excluído pela moderação: só a moderação pode ver
    if getattr(topico, "excluido", False):
        return bool(pode_moderar)

    # Tópico oculto: autor e moderação podem ver
    if topico.oculto:
        return _pode_gerenciar_topico(usuario, topico, pode_moderar)

    return True


def _pode_gerenciar_comentario(usuario, comentario: ForumComentario, pode_moderar: bool) -> bool:
    return bool(pode_moderar or _eh_dono_comentario(usuario, comentario))

def forum_user_or_moderador_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # funcionário autenticado pode passar
        fid = session.get("funcionario_id")
        if fid:
            funcionario = Funcionario.query.get(fid)
            if not funcionario:
                session.pop("funcionario_id", None)
                flash("Faça login novamente.", "warning")
                return _login_redirect()
            return fn(*args, **kwargs)

        # usuário comum autenticado
        uid = session.get("usuario_id")
        if not uid:
            flash("Faça login para continuar.", "warning")
            return _login_redirect()

        usuario = Usuario.query.get(uid)
        if not usuario:
            session.pop("usuario_id", None)
            session.pop("usuario_nome", None)
            flash("Faça login novamente.", "warning")
            return _login_redirect()

        status = (usuario.status or "").strip().lower()

        if status == "apagado":
            session.pop("usuario_id", None)
            session.pop("usuario_nome", None)
            flash("Esta conta não está mais disponível para acesso.", "danger")
            return _login_redirect()

        if status == "bloqueado":
            flash("Sua conta está bloqueada e não pode acessar o fórum.", "danger")
            return redirect(url_for("main.home"))

        return fn(*args, **kwargs)

    return wrapper

def _status_usuario_normalizado(usuario: Usuario | None) -> str:
    return ((getattr(usuario, "status", "") or "").strip().lower())


def _usuario_foi_apagado(usuario: Usuario | None) -> bool:
    if not usuario:
        return True
    return _status_usuario_normalizado(usuario) == "apagado"


def _buscar_usuario(usuario_id: int | None) -> Usuario | None:
    if not usuario_id:
        return None
    return Usuario.query.get(usuario_id)


def _topico_bloqueado_por_autor_removido(topico: ForumTopico | None) -> bool:
    if not topico:
        return True
    autor = getattr(topico, "autor", None) or _buscar_usuario(getattr(topico, "autor_id", None))
    return _usuario_foi_apagado(autor)


def _comentario_bloqueado_por_autor_removido(comentario: ForumComentario | None) -> bool:
    if not comentario:
        return True
    autor = getattr(comentario, "autor", None) or _buscar_usuario(getattr(comentario, "autor_id", None))
    return _usuario_foi_apagado(autor)


def _usuario_pode_receber_notificacao(usuario_id: int | None) -> bool:
    usuario = _buscar_usuario(usuario_id)
    return bool(usuario and not _usuario_foi_apagado(usuario))

# ============================================================
# Sidebar
# ============================================================

def _dados_sidebar_forum(limit_recentes=6, limit_comentados=6, excluir_topico_id=None):
    """
    Dados da sidebar do fórum:
    - tópicos mais recentes
    - tópicos mais comentados
    """
    base = (
        ForumTopico.query
        .options(joinedload(ForumTopico.livro))
        .filter(
            ForumTopico.oculto.is_(False),
            ForumTopico.excluido.is_(False),
        )
    )

    if excluir_topico_id:
        base = base.filter(ForumTopico.id != excluir_topico_id)

    topicos_recentes = (
        base.order_by(ForumTopico.criado_em.desc(), ForumTopico.id.desc())
        .limit(limit_recentes)
        .all()
    )

    topicos_mais_comentados = (
        base.order_by(
            ForumTopico.comentarios_count.desc(),
            func.coalesce(ForumTopico.ultimo_comentario_em, ForumTopico.criado_em).desc(),
            ForumTopico.criado_em.desc(),
            ForumTopico.id.desc(),
        )
        .limit(limit_comentados)
        .all()
    )

    return {
        "topicos_recentes_sidebar": topicos_recentes,
        "topicos_comentados_sidebar": topicos_mais_comentados,
    }


# ============================================================
# Helpers utilitários
# ============================================================

ONLINE_WINDOW_SECONDS = 30  # janela pra considerar "online"


def _registrar_presenca_forum_usuario():
    uid = session.get("usuario_id")
    if not uid:
        return

    now = datetime.utcnow()

    stmt = mysql_insert(ForumPresenca).values(
        usuario_id=int(uid),
        last_seen=now,
    ).on_duplicate_key_update(
        last_seen=now
    )

    bd.session.execute(stmt)
    bd.session.commit()


def _contagem_usuarios_online_forum() -> int:
    cutoff = datetime.utcnow() - timedelta(seconds=ONLINE_WINDOW_SECONDS)
    return (
        ForumPresenca.query
        .filter(ForumPresenca.last_seen >= cutoff)
        .count()
    )


def _limpar_texto(s: str | None) -> str:
    s = (s or "").strip()
    linhas = [ln.rstrip() for ln in s.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(linhas).strip()


def _gerar_slug_unico(titulo: str, topico_id_atual: int | None = None) -> str:
    base = slugify(titulo)
    slug = base
    n = 2

    while True:
        q = ForumTopico.query.filter_by(slug=slug)
        if topico_id_atual:
            q = q.filter(ForumTopico.id != topico_id_atual)
        existe = bd.session.query(q.exists()).scalar()
        if not existe:
            return slug
        slug = f"{base}-{n}"
        n += 1


def _recalcular_topico(topico: ForumTopico):
    comentarios_visiveis = (
        ForumComentario.query
        .filter_by(topico_id=topico.id, oculto=False)
        .order_by(ForumComentario.criado_em.desc(), ForumComentario.id.desc())
        .all()
    )
    topico.comentarios_count = len(comentarios_visiveis)
    topico.ultimo_comentario_em = comentarios_visiveis[0].criado_em if comentarios_visiveis else None
    topico.atualizado_em = datetime.utcnow()


def _nome_usuario(u: Usuario | None) -> str:
    if not u:
        return "Usuário"

    status = (getattr(u, "status", "") or "").strip().lower()
    if status == "apagado":
        return "Usuário removido"

    return u.nome_usuario or u.email or "Usuário"


def _titulo_livro(livro: Livro | None) -> str:
    if not livro:
        return "Discussão geral"
    return livro.titulo or f"Livro #{livro.id}"


def _resumo_texto(texto: str, limite: int = 220) -> str:
    texto = (texto or "").strip().replace("\n", " ")
    if len(texto) <= limite:
        return texto
    return texto[:limite].rsplit(" ", 1)[0] + "…"


def _registrar_view_unica(topico_id: int, usuario_id: int | None) -> bool:
    """
    Retorna True se contou view (primeira vez do usuário nesse tópico),
    False se já tinha contado antes (ou se não está logado).
    """
    if not usuario_id:
        return False

    stmt = (
        mysql_insert(ForumTopicoView)
        .values(topico_id=topico_id, usuario_id=usuario_id)
        .prefix_with("IGNORE")
    )
    res = bd.session.execute(stmt)

    # Inseriu agora => 1ª vez => incrementa contador do tópico
    if res.rowcount == 1:
        bd.session.execute(
            update(ForumTopico)
            .where(ForumTopico.id == topico_id)
            .values(visualizacoes=ForumTopico.visualizacoes + 1)
        )
        return True

    return False


def _relacao_seguir(seguidor_id: int, seguido_id: int) -> ForumSeguidor | None:
    return (
        ForumSeguidor.query
        .join(Usuario, Usuario.id == ForumSeguidor.seguidor_id)
        .filter(
            ForumSeguidor.seguidor_id == seguidor_id,
            ForumSeguidor.seguido_id == seguido_id,
            func.lower(Usuario.status) != "apagado",
        )
        .first()
    )


def _status_relacao_seguir(seguidor_id: int, seguido_id: int) -> str | None:
    relacao = _relacao_seguir(seguidor_id, seguido_id)
    if not relacao:
        return None
    return (relacao.status or "").strip().lower() or None


def _usuario_segue(seguidor_id: int, seguido_id: int) -> bool:
    return _status_relacao_seguir(seguidor_id, seguido_id) == "aceito"


def _usuario_tem_solicitacao_pendente(seguidor_id: int, seguido_id: int) -> bool:
    return _status_relacao_seguir(seguidor_id, seguido_id) == "pendente"

def _pode_ver_conteudo_perfil(perfil: Usuario, viewer: Usuario | None) -> bool:
    # dono sempre vê
    if viewer and viewer.id == perfil.id:
        return True

    # público
    priv = (perfil.forum_privacidade or "todos").strip().lower()
    if priv == "todos":
        return True

    # apenas seguidores
    if viewer and _usuario_segue(viewer.id, perfil.id):
        return True

    return False


EMOJI_OPTIONS = ("👍", "❤️", "😂", "😮", "😢", "👏", "🔥")


def _redirect_back(default_endpoint: str, **values):
    destino = request.form.get("next") or request.referrer
    if destino:
        return redirect(destino)
    return redirect(url_for(default_endpoint, **values))

def _usuario_ativo_no_forum_expr():
    return func.lower(Usuario.status) != "apagado"

def _is_ajax_request() -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _coletar_dados_navbar_forum(usuario_id: int) -> dict:
    notificacoes_nav = (
        Notificacao.query
        .options(joinedload(Notificacao.ator))
        .filter(Notificacao.usuario_id == usuario_id)
        .order_by(Notificacao.criada_em.desc(), Notificacao.id.desc())
        .limit(20)
        .all()
    )

    notificacoes_nao_lidas = (
        Notificacao.query
        .filter(
            Notificacao.usuario_id == usuario_id,
            Notificacao.lida.is_(False),
        )
        .count()
    )

    solicitacoes_pendentes = (
        ForumSeguidor.query
        .options(joinedload(ForumSeguidor.seguidor))
        .join(Usuario, Usuario.id == ForumSeguidor.seguidor_id)
        .filter(
            ForumSeguidor.seguido_id == usuario_id,
            func.lower(ForumSeguidor.status) == "pendente",
            func.lower(Usuario.status) != "apagado",
        )
        .order_by(ForumSeguidor.criado_em.desc(), ForumSeguidor.id.desc())
        .limit(20)
        .all()
    )

    solicitacoes_count = (
        bd.session.query(func.count(ForumSeguidor.id))
        .join(Usuario, Usuario.id == ForumSeguidor.seguidor_id)
        .filter(
            ForumSeguidor.seguido_id == usuario_id,
            func.lower(ForumSeguidor.status) == "pendente",
            func.lower(Usuario.status) != "apagado",
        )
        .scalar()
    ) or 0

    total_badge = int(notificacoes_nao_lidas or 0) + int(solicitacoes_count or 0)

    return {
        "notificacoes_nav": notificacoes_nav,
        "notificacoes_nao_lidas": int(notificacoes_nao_lidas or 0),
        "solicitacoes_pendentes": solicitacoes_pendentes,
        "solicitacoes_count": int(solicitacoes_count or 0),
        "total_badge": total_badge,
    }


def _serializar_notificacao(n: Notificacao) -> dict:
    return {
        "id": n.id,
        "titulo": n.titulo,
        "mensagem": n.mensagem,
        "link_abrir": url_for("forum.abrir_notificacao", notificacao_id=n.id),
        "link_destino": n.link,
        "lida": bool(n.lida),
        "tipo": n.tipo,
        "criada_em": n.criada_em.isoformat() if n.criada_em else None,
        "criada_em_formatada": utc_naive_para_br(n.criada_em).strftime("%d/%m/%Y %H:%M") if n.criada_em else "-",
        "ator": {
            "id": getattr(n.ator, "id", None),
            "nome_usuario": _nome_usuario(getattr(n, "ator", None)),
        },
    }


def _serializar_solicitacao(relacao: ForumSeguidor) -> dict:
    seguidor = getattr(relacao, "seguidor", None)
    return {
        "id": relacao.id,
        "status": relacao.status,
        "criada_em": relacao.criado_em.isoformat() if relacao.criado_em else None,
        "seguidor": {
            "id": getattr(seguidor, "id", None),
            "nome_usuario": getattr(seguidor, "nome_usuario", None),
            "nome_exibicao": _nome_usuario(seguidor),
        },
        "aceitar_url": url_for("forum.aceitar_solicitacao_seguidor", relacao_id=relacao.id),
        "recusar_url": url_for("forum.recusar_solicitacao_seguidor", relacao_id=relacao.id),
        "perfil_url": (
            url_for("forum.perfil_usuario", nome_usuario=seguidor.nome_usuario)
            if seguidor and seguidor.nome_usuario else None
        ),
    }


# ============================================================
# Guard global do fórum
# ============================================================

@forum_bp.before_request
def forum_before_request_guard():
    # funcionário pode continuar acessando/moderando
    if session.get("funcionario_id"):
        return None

    uid = session.get("usuario_id")
    if not uid:
        return None  # visitantes podem acessar páginas públicas

    usuario = Usuario.query.get(uid)
    if not usuario:
        session.pop("usuario_id", None)
        session.pop("usuario_nome", None)
        return redirect(url_for("auth.login"))

    status = (usuario.status or "").strip().lower()

    if status == "apagado":
        session.pop("usuario_id", None)
        session.pop("usuario_nome", None)
        flash("Esta conta não está mais disponível para acesso.", "danger")
        return redirect(url_for("auth.login"))

    if status == "bloqueado":
        flash("Sua conta está bloqueada e não pode acessar o fórum.", "danger")
        return redirect(url_for("main.home"))


# ============================================================
# Listagem de tópicos
# ============================================================

@forum_bp.get("/online_count")
def forum_online_count():
    _registrar_presenca_forum_usuario()
    return jsonify({
        "online": _contagem_usuarios_online_forum(),
        "window_seconds": ONLINE_WINDOW_SECONDS
    })


@forum_bp.get("/")
def lista_topicos():
    usuario = _usuario_logado()
    func_logado = _funcionario_logado()
    pode_moderar = _pode_moderar()

    _registrar_presenca_forum_usuario()
    online_count = _contagem_usuarios_online_forum()

    q = (request.args.get("q") or "").strip()
    livro_id = (request.args.get("livro_id") or "").strip()
    ordem = (request.args.get("ordem") or "atividade").strip()
    page = request.args.get("page", 1, type=int)

    # tags (chips)
    tags_raw_in = (request.args.get("tags") or "").strip()
    tags_set = {t.strip().lower() for t in tags_raw_in.split(",") if t.strip()}
    tags_raw = ",".join(sorted(tags_set))

    query = (
        ForumTopico.query
        .options(
            joinedload(ForumTopico.autor),
            joinedload(ForumTopico.livro),
        )
    )

    if not pode_moderar:
        query = query.filter(
            ForumTopico.oculto.is_(False),
            ForumTopico.excluido.is_(False),
        )
    else:
        # na listagem pública normal, até a moderação pode manter excluídos fora daqui
        query = query.filter(ForumTopico.excluido.is_(False))

    if q:
        like = f"%{q}%"
        query = query.outerjoin(Livro, ForumTopico.livro_id == Livro.id).filter(
            or_(
                ForumTopico.titulo.ilike(like),
                ForumTopico.conteudo.ilike(like),
                Livro.titulo.ilike(like),
                Livro.autor.ilike(like),
            )
        )

    livro_id_int = None
    if livro_id.isdigit():
        livro_id_int = int(livro_id)
        query = query.filter(ForumTopico.livro_id == livro_id_int)

    if "pinned" in tags_set:
        query = query.filter(ForumTopico.fixado.is_(True))

    if "locked" in tags_set:
        query = query.filter(ForumTopico.trancado.is_(True))

    if livro_id_int is None:
        book = "book" in tags_set
        general = "general" in tags_set
        if book and not general:
            query = query.filter(ForumTopico.livro_id.isnot(None))
        elif general and not book:
            query = query.filter(ForumTopico.livro_id.is_(None))

    atividade_expr = func.coalesce(ForumTopico.ultimo_comentario_em, ForumTopico.criado_em)

    if ordem == "recentes":
        query = query.order_by(
            ForumTopico.fixado.desc(),
            ForumTopico.criado_em.desc(),
            ForumTopico.id.desc(),
        )
    elif ordem == "views":
        query = query.order_by(
            ForumTopico.fixado.desc(),
            ForumTopico.visualizacoes.desc(),
            atividade_expr.desc(),
            ForumTopico.id.desc(),
        )
    elif ordem == "comentados":
        query = query.order_by(
            ForumTopico.fixado.desc(),
            ForumTopico.comentarios_count.desc(),
            atividade_expr.desc(),
            ForumTopico.id.desc(),
        )
    elif ordem == "antigos":
        query = query.order_by(
            ForumTopico.fixado.desc(),
            ForumTopico.criado_em.asc(),
            ForumTopico.id.asc(),
        )
    else:
        query = query.order_by(
            ForumTopico.fixado.desc(),
            atividade_expr.desc(),
            ForumTopico.id.desc(),
        )

    pag = query.paginate(page=page, per_page=10, error_out=False)

    livros_filtro = Livro.query.order_by(Livro.titulo.asc()).limit(300).all()

    args_no_page = dict(request.args)
    args_no_page.pop("page", None)

    sidebar = _dados_sidebar_forum()
    has_filters = bool(q or livro_id or tags_set or (ordem and ordem != "atividade"))

    return render_template(
        "forum/lista_topicos.html",
        usuario_logado=usuario,
        funcionario_logado=func_logado,
        pode_moderar=pode_moderar,
        topicos=pag.items,
        pag=pag,
        args_no_page=args_no_page,
        q=q,
        livro_id=livro_id,
        ordem=ordem,
        livros_filtro=livros_filtro,
        active_tags=tags_set,
        tags_raw=tags_raw,
        has_filters=has_filters,
        online_count=online_count,
        _nome_usuario=_nome_usuario,
        _titulo_livro=_titulo_livro,
        _resumo_texto=_resumo_texto,
        **sidebar,
    )


# ============================================================
# Criar tópico
# ============================================================

@forum_bp.route("/novo", methods=["GET", "POST"])
@forum_login_required
def novo_topico():
    usuario = _usuario_logado()

    livro_id_get = request.args.get("livro_id", type=int)
    livro_pre = Livro.query.get(livro_id_get) if livro_id_get else None

    if request.method == "POST":
        titulo = _limpar_texto(request.form.get("titulo"))
        conteudo = _limpar_texto(request.form.get("conteudo"))
        livro_id = request.form.get("livro_id", type=int)
        livro = Livro.query.get(livro_id) if livro_id else None

        erros = []
        if len(titulo) < 6:
            erros.append("O título deve ter pelo menos 6 caracteres.")
        if len(conteudo) < 20:
            erros.append("O conteúdo deve ter pelo menos 20 caracteres.")

        if erros:
            for e in erros:
                flash(e, "danger")
            return render_template(
                "forum/form_topico.html",
                modo="novo",
                usuario_logado=usuario,
                topico=None,
                livro_preselecionado=livro,
                livros=Livro.query.order_by(Livro.titulo.asc()).limit(300).all(),
                pode_moderar=_pode_moderar(),
            )

        topico = ForumTopico(
            titulo=titulo,
            slug=_gerar_slug_unico(titulo),
            conteudo=conteudo,
            livro_id=livro.id if livro else None,
            autor_id=usuario.id,
            ultimo_comentario_em=None,
        )

        bd.session.add(topico)

        for _ in range(5):
            try:
                bd.session.commit()
                break
            except IntegrityError:
                bd.session.rollback()
                topico.slug = _gerar_slug_unico(titulo)
                bd.session.add(topico)
        else:
            flash("Não foi possível criar o tópico agora. Tente novamente.", "danger")
            return render_template(
                "forum/form_topico.html",
                modo="novo",
                usuario_logado=usuario,
                topico=None,
                livro_preselecionado=livro,
                livros=Livro.query.order_by(Livro.titulo.asc()).limit(300).all(),
                pode_moderar=_pode_moderar(),
            )

        flash("Tópico criado com sucesso!", "success")
        return redirect(url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug))

    return render_template(
        "forum/form_topico.html",
        modo="novo",
        usuario_logado=usuario,
        topico=None,
        livro_preselecionado=livro_pre,
        livros=Livro.query.order_by(Livro.titulo.asc()).limit(300).all(),
        pode_moderar=_pode_moderar(),
    )


# ============================================================
# Detalhe do tópico
# ============================================================

@forum_bp.get("/topico/<int:topico_id>")
@forum_bp.get("/topico/<int:topico_id>/<slug>")
def topico_detalhe(topico_id: int, slug: str | None = None):
    usuario = _usuario_logado()
    func_logado = _funcionario_logado()
    pode_moderar = _pode_moderar()
    _registrar_presenca_forum_usuario()

    topico = (
        ForumTopico.query
        .options(joinedload(ForumTopico.autor), joinedload(ForumTopico.livro))
        .filter_by(id=topico_id)
        .first_or_404()
    )

    autor_do_topico = _eh_dono_topico(usuario, topico)

    if not _pode_ver_topico(usuario, topico, pode_moderar):
        abort(404)

    if slug != topico.url_slug:
        return redirect(
            url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug),
            code=301,
        )

    sidebar = _dados_sidebar_forum()

    usuario_id = getattr(usuario, "id", None)
    contou = _registrar_view_unica(topico.id, usuario_id)
    if contou:
        bd.session.commit()

    comentarios = (
        ForumComentario.query
        .options(joinedload(ForumComentario.autor))
        .filter(ForumComentario.topico_id == topico.id)
        .order_by(ForumComentario.criado_em.asc(), ForumComentario.id.asc())
        .all()
    )

    comentarios_por_parent = defaultdict(list)
    for c in comentarios:
        comentarios_por_parent[c.parent_id].append(c)

    comentarios_raiz = comentarios_por_parent.get(None, [])

    # Ordena SOMENTE os comentários raiz:
    # 1) não ocultos primeiro
    # 2) mais upvotes primeiro
    # 3) mais recentes primeiro
    # 4) maior id como desempate
    comentarios_raiz.sort(
        key=lambda c: (
            1 if getattr(c, "oculto", False) else 0,
            -(c.upvotes_count or 0),
            -(c.criado_em.timestamp() if c.criado_em else 0),
            -(c.id or 0),
        )
    )

    topico_upvotado = False
    comentarios_upvotados_ids = set()

    # novo formato
    minha_reacao_por_comentario = {}
    reacoes_outros_por_comentario = {}

    comentario_ids = [c.id for c in comentarios]

    if comentario_ids:
        if usuario:
            minhas_reacoes_rows = (
                bd.session.query(
                    ForumComentarioReacao.comentario_id,
                    ForumComentarioReacao.emoji,
                )
                .filter(
                    ForumComentarioReacao.usuario_id == usuario.id,
                    ForumComentarioReacao.comentario_id.in_(comentario_ids),
                )
                .all()
            )

            for comentario_id, emoji in minhas_reacoes_rows:
                minha_reacao_por_comentario[comentario_id] = emoji

            reacoes_rows = (
                bd.session.query(
                    ForumComentarioReacao.comentario_id,
                    ForumComentarioReacao.emoji,
                    func.count(ForumComentarioReacao.id).label("total"),
                )
                .filter(
                    ForumComentarioReacao.comentario_id.in_(comentario_ids),
                    ForumComentarioReacao.usuario_id != usuario.id,
                )
                .group_by(
                    ForumComentarioReacao.comentario_id,
                    ForumComentarioReacao.emoji,
                )
                .all()
            )
        else:
            reacoes_rows = (
                bd.session.query(
                    ForumComentarioReacao.comentario_id,
                    ForumComentarioReacao.emoji,
                    func.count(ForumComentarioReacao.id).label("total"),
                )
                .filter(ForumComentarioReacao.comentario_id.in_(comentario_ids))
                .group_by(
                    ForumComentarioReacao.comentario_id,
                    ForumComentarioReacao.emoji,
                )
                .all()
            )

        for comentario_id, emoji, total in reacoes_rows:
            total = int(total or 0)
            if total > 0:
                reacoes_outros_por_comentario.setdefault(comentario_id, []).append({
                    "emoji": emoji,
                    "count": total,
                })

    topico_interacoes_bloqueadas = _topico_bloqueado_por_autor_removido(topico)
    comentarios_interacoes_bloqueadas_ids = {
        c.id for c in comentarios if _comentario_bloqueado_por_autor_removido(c)
    }

    return render_template(
        "forum/topico_detalhe.html",
        usuario_logado=usuario,
        funcionario_logado=func_logado,
        pode_moderar=pode_moderar,
        autor_do_topico=autor_do_topico,
        topico=topico,
        comentarios_raiz=comentarios_raiz,
        comentarios_por_parent=dict(comentarios_por_parent),
        _nome_usuario=_nome_usuario,
        _titulo_livro=_titulo_livro,
        topico_upvotado=topico_upvotado,
        comentarios_upvotados_ids=comentarios_upvotados_ids,
        minha_reacao_por_comentario=minha_reacao_por_comentario,
        reacoes_outros_por_comentario=reacoes_outros_por_comentario,
        emoji_options=EMOJI_OPTIONS,
        topico_interacoes_bloqueadas=topico_interacoes_bloqueadas,
        comentarios_interacoes_bloqueadas_ids=comentarios_interacoes_bloqueadas_ids,
        **sidebar,
    )


# ============================================================
# Comentários / respostas
# ============================================================

@forum_bp.post("/topico/<int:topico_id>/comentar")
@forum_login_required
def comentar_topico(topico_id: int):
    usuario = _usuario_logado()
    pode_moderar = _pode_moderar()
    topico = ForumTopico.query.get_or_404(topico_id)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not _pode_ver_topico(usuario, topico, pode_moderar):
        abort(404)

    if _topico_bloqueado_por_autor_removido(topico):
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Não é possível comentar em tópico de usuário removido.",
            }), 403

        flash("Não é possível comentar em tópico de usuário removido.", "warning")
        return redirect(url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug))

    if topico.trancado and not pode_moderar:
        flash("Este tópico está trancado e não aceita novos comentários.", "warning")
        return redirect(url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug))

    conteudo = _limpar_texto(request.form.get("conteudo"))
    if len(conteudo) < 2:
        flash("Comentário muito curto.", "danger")
        return redirect(url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug))

    comentario = ForumComentario(
        topico_id=topico.id,
        autor_id=usuario.id,
        conteudo=conteudo,
        parent_id=None,
    )
    bd.session.add(comentario)

    # Gera o ID antes do commit, para usar no link da notificação
    bd.session.flush()

    _recalcular_topico(topico)

    # se outra pessoa comentou em um tópico criado pelo usuário
    if (
            topico.autor_id
            and topico.autor_id != usuario.id
            and _usuario_pode_receber_notificacao(topico.autor_id)
    ):
        link_notificacao = (
            url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)
            + f"#comentario-{comentario.id}"
        )

        criar_notificacao(
            usuario_id=topico.autor_id,
            ator_id=usuario.id,
            tipo="comentario_em_topico",
            titulo="Novo comentário no seu tópico",
            mensagem=f"{usuario.nome_usuario} comentou no seu tópico “{topico.titulo}”.",
            link=link_notificacao,
        )

    bd.session.commit()

    hash_comentario = f"#comentario-{comentario.id}"

    if is_ajax:
        return jsonify({
            "ok": True,
            "comentario_id": comentario.id,
            "hash": hash_comentario,
            "message": "Comentário adicionado.",
        })

    flash("Comentário adicionado.", "success")
    return redirect(
        url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug) + hash_comentario
    )


@forum_bp.post("/comentario/<int:comentario_id>/responder")
@forum_login_required
def responder_comentario(comentario_id: int):
    usuario = _usuario_logado()
    pode_moderar = _pode_moderar()
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    pai = (
        ForumComentario.query
        .options(joinedload(ForumComentario.autor))
        .filter_by(id=comentario_id)
        .first_or_404()
    )

    topico = (
        ForumTopico.query
        .options(joinedload(ForumTopico.autor))
        .filter_by(id=pai.topico_id)
        .first_or_404()
    )

    if not _pode_ver_topico(usuario, topico, pode_moderar):
        abort(404)

    if _topico_bloqueado_por_autor_removido(topico):
        flash("Não é possível responder em tópico de usuário removido.", "warning")
        return redirect(url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug))

    if _comentario_bloqueado_por_autor_removido(pai):
        flash("Não é possível responder comentário de usuário removido.", "warning")
        return redirect(
            url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug) + f"#comentario-{pai.id}"
        )

    if topico.trancado and not pode_moderar:
        flash("Este tópico está trancado e não aceita respostas.", "warning")
        return redirect(url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug))

    conteudo = _limpar_texto(request.form.get("conteudo"))
    if len(conteudo) < 2:
        flash("Resposta muito curta.", "danger")
        return redirect(
            url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug) + f"#comentario-{pai.id}"
        )

    resposta = ForumComentario(
        topico_id=topico.id,
        autor_id=usuario.id,
        parent_id=pai.id,
        conteudo=conteudo,
    )
    bd.session.add(resposta)

    # Gera o ID antes do commit
    bd.session.flush()

    _recalcular_topico(topico)

    link_notificacao = (
        url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)
        + f"#comentario-{resposta.id}"
    )

    # nova resposta dentro de tópico criado pelo usuário
    if (
            topico.autor_id
            and topico.autor_id != usuario.id
            and _usuario_pode_receber_notificacao(topico.autor_id)
    ):
        criar_notificacao(
            usuario_id=topico.autor_id,
            ator_id=usuario.id,
            tipo="resposta_em_topico",
            titulo="Nova resposta no seu tópico",
            mensagem=f"{usuario.nome_usuario} respondeu em seu tópico “{topico.titulo}”.",
            link=link_notificacao,
        )

    # responderam o comentário do usuário em tópico que não é dele
    if (
                pai.autor_id
                and pai.autor_id != usuario.id
                and pai.autor_id != topico.autor_id
                and _usuario_pode_receber_notificacao(pai.autor_id)
        ):
        criar_notificacao(
            usuario_id=pai.autor_id,
            ator_id=usuario.id,
            tipo="resposta_em_comentario",
            titulo="Responderam seu comentário",
            mensagem=f"{usuario.nome_usuario} respondeu seu comentário no tópico “{topico.titulo}”.",
            link=link_notificacao,
        )

    bd.session.commit()

    hash_resposta = f"#comentario-{resposta.id}"

    if is_ajax:
        return jsonify({
            "ok": True,
            "comentario_id": resposta.id,
            "parent_id": pai.id,
            "hash": hash_resposta,
            "message": "Resposta enviada.",
        })

    flash("Resposta enviada.", "success")
    return redirect(
        url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug) + hash_resposta
    )


# ============================================================
# Upvotes
# ============================================================

@forum_bp.post("/topico/<int:topico_id>/upvote")
@forum_login_required
def upvote_topico(topico_id: int):
    usuario = _usuario_logado()
    pode_moderar = _pode_moderar()
    topico = ForumTopico.query.get_or_404(topico_id)

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not _pode_ver_topico(usuario, topico, pode_moderar):
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Tópico não disponível.",
            }), 404
        abort(404)

    if _topico_bloqueado_por_autor_removido(topico):
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Não é possível interagir com tópico de usuário removido.",
            }), 403

        flash("Não é possível interagir com tópico de usuário removido.", "warning")
        return _redirect_back("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)
    voto = ForumTopicoUpvote.query.filter_by(
        topico_id=topico.id,
        usuario_id=usuario.id,
    ).first()

    if voto:
        bd.session.delete(voto)
        topico.upvotes_count = max(0, int(topico.upvotes_count or 0) - 1)
        upvotado_agora = False

        if not is_ajax:
            flash("Upvote removido do tópico.", "info")
    else:
        novo = ForumTopicoUpvote(topico_id=topico.id, usuario_id=usuario.id)
        bd.session.add(novo)
        topico.upvotes_count = int(topico.upvotes_count or 0) + 1
        upvotado_agora = True

        if not is_ajax:
            flash("Upvote adicionado ao tópico.", "success")

    bd.session.commit()

    if is_ajax:
        return jsonify({
            "ok": True,
            "topico_id": topico.id,
            "upvoted": upvotado_agora,
            "count": int(topico.upvotes_count or 0),
        })

    return _redirect_back("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)


@forum_bp.post("/comentario/<int:comentario_id>/upvote")
@forum_login_required
def upvote_comentario(comentario_id: int):
    usuario = _usuario_logado()
    pode_moderar = _pode_moderar()

    comentario = ForumComentario.query.get_or_404(comentario_id)
    topico = ForumTopico.query.get_or_404(comentario.topico_id)

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not _pode_ver_topico(usuario, topico, pode_moderar):
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Tópico não disponível.",
            }), 404
        abort(404)

    if _topico_bloqueado_por_autor_removido(topico):
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Não é possível interagir com tópico de usuário removido.",
            }), 403

        flash("Não é possível interagir com tópico de usuário removido.", "warning")
        return _redirect_back("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)

    if _comentario_bloqueado_por_autor_removido(comentario):
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Não é possível interagir com comentário de usuário removido.",
            }), 403

        flash("Não é possível interagir com comentário de usuário removido.", "warning")
        return _redirect_back("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)

    if comentario.oculto and not pode_moderar:
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Não é possível dar upvote em comentário removido.",
            }), 403

        flash("Não é possível dar upvote em comentário removido.", "warning")
        return _redirect_back("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)

    voto = ForumComentarioUpvote.query.filter_by(
        comentario_id=comentario.id,
        usuario_id=usuario.id,
    ).first()

    if voto:
        bd.session.delete(voto)
        comentario.upvotes_count = max(0, int(comentario.upvotes_count or 0) - 1)
        upvotado_agora = False

        if not is_ajax:
            flash("Upvote removido do comentário.", "info")
    else:
        novo = ForumComentarioUpvote(comentario_id=comentario.id, usuario_id=usuario.id)
        bd.session.add(novo)
        comentario.upvotes_count = int(comentario.upvotes_count or 0) + 1
        upvotado_agora = True

        if not is_ajax:
            flash("Upvote adicionado ao comentário.", "success")

    bd.session.commit()

    if is_ajax:
        return jsonify({
            "ok": True,
            "comentario_id": comentario.id,
            "upvoted": upvotado_agora,
            "count": int(comentario.upvotes_count or 0),
        })

    return _redirect_back("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)


@forum_bp.post("/comentario/<int:comentario_id>/reagir")
@forum_login_required
def reagir_comentario(comentario_id: int):
    usuario = _usuario_logado()
    pode_moderar = _pode_moderar()

    comentario = ForumComentario.query.get_or_404(comentario_id)
    topico = ForumTopico.query.get_or_404(comentario.topico_id)

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not _pode_ver_topico(usuario, topico, pode_moderar):
        if is_ajax:
            return jsonify({"ok": False, "message": "Tópico não disponível."}), 404
        abort(404)

    if _topico_bloqueado_por_autor_removido(topico):
        if is_ajax:
            return jsonify({"ok": False, "message": "Não é possível interagir com tópico de usuário removido."}), 403
        flash("Não é possível interagir com tópico de usuário removido.", "warning")
        return _redirect_back("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)

    if _comentario_bloqueado_por_autor_removido(comentario):
        if is_ajax:
            return jsonify({"ok": False, "message": "Não é possível interagir com comentário de usuário removido."}), 403
        flash("Não é possível interagir com comentário de usuário removido.", "warning")
        return _redirect_back("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)

    if comentario.oculto and not pode_moderar:
        if is_ajax:
            return jsonify({"ok": False, "message": "Não é possível reagir a comentário removido."}), 403
        flash("Não é possível reagir a comentário removido.", "warning")
        return _redirect_back("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)

    emoji = (request.form.get("emoji") or "").strip()
    if emoji not in EMOJI_OPTIONS:
        if is_ajax:
            return jsonify({"ok": False, "message": "Emoji inválido."}), 400
        flash("Emoji inválido.", "danger")
        return _redirect_back("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)

    reacao = (
        ForumComentarioReacao.query
        .filter_by(comentario_id=comentario.id, usuario_id=usuario.id)
        .first()
    )

    emoji_anterior = None
    acao = None
    minha_reacao_ativa = False

    if reacao:
        emoji_anterior = reacao.emoji

        if reacao.emoji == emoji:
            bd.session.delete(reacao)
            minha_reacao_ativa = False
            acao = "removed"

            if not is_ajax:
                flash(f"Reação {emoji} removida.", "info")
        else:
            reacao.emoji = emoji
            minha_reacao_ativa = True
            acao = "changed"

            if not is_ajax:
                flash(f"Reação alterada para {emoji}.", "success")
    else:
        nova = ForumComentarioReacao(
            comentario_id=comentario.id,
            usuario_id=usuario.id,
            emoji=emoji,
        )
        bd.session.add(nova)
        minha_reacao_ativa = True
        acao = "added"

        if not is_ajax:
            flash(f"Reação {emoji} adicionada.", "success")

    bd.session.commit()

    total_emoji = ForumComentarioReacao.query.filter_by(
        comentario_id=comentario.id,
        emoji=emoji,
    ).count()

    count_anterior = None
    if emoji_anterior and emoji_anterior != emoji:
        count_anterior = ForumComentarioReacao.query.filter_by(
            comentario_id=comentario.id,
            emoji=emoji_anterior,
        ).count()
    elif acao == "removed":
        count_anterior = total_emoji

    if is_ajax:
        return jsonify({
            "ok": True,
            "comentario_id": comentario.id,
            "emoji": emoji,
            "mine": minha_reacao_ativa,
            "count": total_emoji,
            "emoji_anterior": emoji_anterior,
            "count_anterior": count_anterior,
            "acao": acao,
        })

    return _redirect_back("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)

# ============================================================
# Editar tópico
# ============================================================

@forum_bp.route("/topico/<int:topico_id>/editar", methods=["GET", "POST"])
@forum_user_or_moderador_required
def editar_topico(topico_id: int):
    usuario = _usuario_logado()
    pode_moderar = _pode_moderar()
    topico = ForumTopico.query.get_or_404(topico_id)

    if not (_eh_dono_topico(usuario, topico) or pode_moderar):
        abort(403)

    if topico.excluido and not pode_moderar:
        flash("Este tópico foi removido pela moderação e não pode ser editado.", "warning")
        return redirect(url_for("forum.lista_topicos"))

    # usuário comum não pode editar tópico trancado
    if topico.trancado and not pode_moderar:
        flash("Este tópico foi trancado pela moderação e não pode mais ser editado.", "warning")
        return redirect(url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug))

    if request.method == "POST":
        titulo = _limpar_texto(request.form.get("titulo"))
        conteudo = _limpar_texto(request.form.get("conteudo"))
        livro_id = request.form.get("livro_id", type=int)
        livro = Livro.query.get(livro_id) if livro_id else None

        erros = []
        if len(titulo) < 6:
            erros.append("O título deve ter pelo menos 6 caracteres.")
        if len(conteudo) < 20:
            erros.append("O conteúdo deve ter pelo menos 20 caracteres.")

        if erros:
            for e in erros:
                flash(e, "danger")
            return render_template(
                "forum/form_topico.html",
                modo="editar",
                usuario_logado=usuario,
                topico=topico,
                livro_preselecionado=livro,
                livros=Livro.query.order_by(Livro.titulo.asc()).limit(300).all(),
                pode_moderar=pode_moderar,
            )

        topico.titulo = titulo
        topico.slug = _gerar_slug_unico(titulo, topico_id_atual=topico.id)
        topico.conteudo = conteudo
        topico.livro_id = livro.id if livro else None
        topico.atualizado_em = datetime.utcnow()

        for _ in range(5):
            try:
                bd.session.commit()
                break
            except IntegrityError:
                bd.session.rollback()
                topico.slug = _gerar_slug_unico(titulo, topico_id_atual=topico.id)
        else:
            flash("Não foi possível salvar porque o link do tópico entrou em conflito. Tente novamente.", "danger")
            return render_template(
                "forum/form_topico.html",
                modo="editar",
                usuario_logado=usuario,
                topico=topico,
                livro_preselecionado=livro,
                livros=Livro.query.order_by(Livro.titulo.asc()).limit(300).all(),
                pode_moderar=pode_moderar,
            )

        flash("Tópico atualizado.", "success")
        return redirect(url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug))

    return render_template(
        "forum/form_topico.html",
        modo="editar",
        usuario_logado=usuario,
        topico=topico,
        livro_preselecionado=topico.livro,
        livros=Livro.query.order_by(Livro.titulo.asc()).limit(300).all(),
        pode_moderar=pode_moderar,
    )


# ============================================================
# Ações do autor sobre o próprio tópico
# ============================================================

@forum_bp.post("/topico/<int:topico_id>/ocultar")
@forum_login_required
def ocultar_meu_topico(topico_id: int):
    usuario = _usuario_logado()
    topico = (
        ForumTopico.query
        .options(joinedload(ForumTopico.autor))
        .filter_by(id=topico_id)
        .first_or_404()
    )

    if not _eh_dono_topico(usuario, topico):
        abort(403)

    if topico.excluido:
        flash("Este tópico foi removido pela moderação e não pode ser alterado.", "warning")
        return redirect(url_for("forum.lista_topicos"))

    if topico.trancado:
        flash("Este tópico foi trancado pela moderação e não pode ser ocultado pelo autor.", "warning")
        return redirect(url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug))

    if not topico.oculto:
        topico.oculto = True
        topico.atualizado_em = datetime.utcnow()
        bd.session.commit()
        flash("Seu tópico foi ocultado com sucesso.", "success")
    else:
        flash("Seu tópico já está oculto.", "info")

    return redirect(url_for("forum.meus_topicos"))


@forum_bp.post("/topico/<int:topico_id>/desocultar")
@forum_login_required
def desocultar_meu_topico(topico_id: int):
    usuario = _usuario_logado()
    topico = (
        ForumTopico.query
        .options(joinedload(ForumTopico.autor))
        .filter_by(id=topico_id)
        .first_or_404()
    )

    if not _eh_dono_topico(usuario, topico):
        abort(403)

    if topico.excluido:
        flash("Este tópico foi removido pela moderação e não pode ser restaurado pelo autor.", "warning")
        return redirect(url_for("forum.lista_topicos"))

    if topico.trancado:
        flash("Este tópico foi trancado pela moderação e não pode ser desocultado pelo autor.", "warning")
        return redirect(url_for("forum.meus_topicos"))

    if topico.oculto:
        topico.oculto = False
        topico.atualizado_em = datetime.utcnow()
        bd.session.commit()
        flash("Seu tópico voltou a ficar visível.", "success")
    else:
        flash("Seu tópico já está visível.", "info")

    return redirect(url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug))


@forum_bp.post("/topico/<int:topico_id>/excluir")
@forum_login_required
def excluir_meu_topico(topico_id: int):
    usuario = _usuario_logado()
    topico = (
        ForumTopico.query
        .options(joinedload(ForumTopico.autor))
        .filter_by(id=topico_id)
        .first_or_404()
    )

    if not _eh_dono_topico(usuario, topico):
        abort(403)

    if topico.excluido:
        flash("Este tópico já foi excluído.", "info")
        return redirect(url_for("forum.meus_topicos"))

    agora = datetime.utcnow()

    topico.excluido = True
    topico.oculto = False
    topico.atualizado_em = agora

    if hasattr(topico, "excluido_em"):
        topico.excluido_em = agora


    bd.session.commit()

    flash("Tópico excluído com sucesso.", "success")
    return redirect(url_for("forum.meus_topicos"))


# ============================================================
# Editar / ocultar comentário
# ============================================================

@forum_bp.post("/comentario/<int:comentario_id>/editar")
@forum_user_or_moderador_required
def editar_comentario(comentario_id: int):
    usuario = _usuario_logado()
    comentario = ForumComentario.query.get_or_404(comentario_id)
    topico = ForumTopico.query.get_or_404(comentario.topico_id)

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not (_eh_dono_comentario(usuario, comentario) or _pode_moderar()):
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Você não tem permissão para editar este comentário.",
            }), 403
        abort(403)

    if comentario.oculto:
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Este comentário foi removido e não pode ser editado.",
            }), 403
        flash("Este comentário foi removido e não pode ser editado.", "warning")
        return redirect(
            url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug) + f"#comentario-{comentario.id}"
        )

    if topico.excluido and not _pode_moderar():
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Este tópico foi removido pela moderação.",
            }), 403
        flash("Este tópico foi removido pela moderação.", "warning")
        return redirect(url_for("forum.lista_topicos"))

    if topico.trancado and not _pode_moderar():
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Tópico trancado. Não é possível editar.",
            }), 403
        flash("Tópico trancado. Não é possível editar.", "warning")
        return redirect(
            url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug) + f"#comentario-{comentario.id}"
        )

    conteudo = _limpar_texto(request.form.get("conteudo"))
    if len(conteudo) < 2:
        if is_ajax:
            return jsonify({
                "ok": False,
                "message": "Comentário muito curto.",
            }), 400
        flash("Comentário muito curto.", "danger")
        return redirect(
            url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug) + f"#comentario-{comentario.id}"
        )

    comentario.conteudo = conteudo
    comentario.atualizado_em = datetime.utcnow()

    _recalcular_topico(topico)
    bd.session.commit()

    hash_comentario = f"#comentario-{comentario.id}"

    if is_ajax:
        return jsonify({
            "ok": True,
            "comentario_id": comentario.id,
            "hash": hash_comentario,
            "message": "Comentário atualizado.",
        })

    flash("Comentário atualizado.", "success")
    return redirect(
        url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug) + hash_comentario
    )


@forum_bp.post("/comentario/<int:comentario_id>/excluir")
@forum_user_or_moderador_required
def excluir_comentario(comentario_id: int):
    usuario = _usuario_logado()
    pode_moderar = _pode_moderar()

    comentario = ForumComentario.query.get_or_404(comentario_id)
    topico = ForumTopico.query.get_or_404(comentario.topico_id)

    if not _pode_gerenciar_comentario(usuario, comentario, pode_moderar):
        abort(403)

    if topico.excluido and not pode_moderar:
        flash("Este tópico foi removido pela moderação.", "warning")
        return redirect(url_for("forum.lista_topicos"))

    if not comentario.oculto:
        comentario.oculto = True
        comentario.atualizado_em = datetime.utcnow()
        _recalcular_topico(topico)
        bd.session.commit()

    flash("Comentário ocultado.", "success")
    return redirect(
        url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug) + f"#comentario-{comentario.id}"
    )


# ============================================================
# Meus tópicos
# ============================================================

@forum_bp.get("/meus-topicos")
@forum_login_required
def meus_topicos():
    usuario = _usuario_logado()
    func_logado = _funcionario_logado()
    pode_moderar = _pode_moderar()

    try:
        _registrar_presenca_forum_usuario()
    except Exception:
        pass

    seguidores_count = (
                           bd.session.query(func.count(Usuario.id))
                           .join(ForumSeguidor, ForumSeguidor.seguidor_id == Usuario.id)
                           .filter(
                               ForumSeguidor.seguido_id == usuario.id,
                               func.lower(ForumSeguidor.status) == "aceito",
                               func.lower(Usuario.status) != "apagado",
                           )
                           .scalar()
                       ) or 0

    seguindo_count = (
                         bd.session.query(func.count(Usuario.id))
                         .join(ForumSeguidor, ForumSeguidor.seguido_id == Usuario.id)
                         .filter(
                             ForumSeguidor.seguidor_id == usuario.id,
                             func.lower(ForumSeguidor.status) == "aceito",
                             func.lower(Usuario.status) != "apagado",
                         )
                         .scalar()
                     ) or 0

    seguidores_preview = (
        bd.session.query(Usuario)
        .join(ForumSeguidor, ForumSeguidor.seguidor_id == Usuario.id)
        .filter(
            ForumSeguidor.seguido_id == usuario.id,
            func.lower(ForumSeguidor.status) == "aceito",
            func.lower(Usuario.status) != "apagado",
        )
        .order_by(ForumSeguidor.criado_em.desc())
        .limit(12)
        .all()
    )

    seguindo_preview = (
        bd.session.query(Usuario)
        .join(ForumSeguidor, ForumSeguidor.seguido_id == Usuario.id)
        .filter(
            ForumSeguidor.seguidor_id == usuario.id,
            func.lower(ForumSeguidor.status) == "aceito",
            func.lower(Usuario.status) != "apagado",
        )
        .order_by(ForumSeguidor.criado_em.desc())
        .limit(12)
        .all()
    )

    atividade_expr = func.coalesce(ForumTopico.ultimo_comentario_em, ForumTopico.criado_em)

    meus_topicos = (
        ForumTopico.query
        .options(joinedload(ForumTopico.livro))
        .filter(
            ForumTopico.autor_id == usuario.id,
            ForumTopico.excluido.is_(False),
        )
        .order_by(atividade_expr.desc(), ForumTopico.id.desc())
        .limit(60)
        .all()
    )

    meus_comentarios = (
        bd.session.query(ForumComentario, ForumTopico)
        .join(ForumTopico, ForumTopico.id == ForumComentario.topico_id)
        .filter(
            ForumComentario.autor_id == usuario.id,
            ForumTopico.excluido.is_(False),
        )
        .order_by(ForumComentario.criado_em.desc(), ForumComentario.id.desc())
        .limit(60)
        .all()
    )

    sidebar = _dados_sidebar_forum()

    return render_template(
        "forum/meus_topicos.html",
        usuario_logado=usuario,
        funcionario_logado=func_logado,
        pode_moderar=pode_moderar,
        seguidores_count=seguidores_count,
        seguindo_count=seguindo_count,
        seguidores_preview=seguidores_preview,
        seguindo_preview=seguindo_preview,
        meus_topicos=meus_topicos,
        meus_comentarios=meus_comentarios,
        _nome_usuario=_nome_usuario,
        _resumo_texto=_resumo_texto,
        _titulo_livro=_titulo_livro,
        **sidebar,
    )


# ============================================================
# Perfil público do fórum
# ============================================================

@forum_bp.app_context_processor
def inject_forum_navbar_data():
    uid = session.get("usuario_id")

    if not uid or session.get("funcionario_id"):
        return {
            "notificacoes_nav": [],
            "notificacoes_nao_lidas": 0,
            "solicitacoes_pendentes": [],
            "solicitacoes_count": 0,
            "total_badge": 0,
            "_nome_usuario": _nome_usuario,
        }

    dados = _coletar_dados_navbar_forum(uid)

    return {
        **dados,
        "_nome_usuario": _nome_usuario,
    }

@forum_bp.get("/api/navbar")
@forum_login_required
def api_navbar_forum():
    usuario = _usuario_logado()
    dados = _coletar_dados_navbar_forum(usuario.id)

    html_solicitacoes = render_template(
        "_includes/forum_solicitacoes_lista.html",
        solicitacoes_pendentes=dados["solicitacoes_pendentes"],
        _nome_usuario=_nome_usuario,
    )

    return {
        "ok": True,
        "total_badge": dados["total_badge"],
        "notificacoes_nao_lidas": dados["notificacoes_nao_lidas"],
        "solicitacoes_count": dados["solicitacoes_count"],
        "notificacoes": [_serializar_notificacao(n) for n in dados["notificacoes_nav"]],
        "html_solicitacoes": html_solicitacoes,
    }

@forum_bp.get("/u/<nome_usuario>")
def perfil_usuario(nome_usuario: str):
    viewer = _usuario_logado()
    func_logado = _funcionario_logado()
    pode_moderar = _pode_moderar()

    perfil = Usuario.query.filter(
        func.lower(Usuario.nome_usuario) == nome_usuario.lower()
    ).first_or_404()

    status_perfil = (perfil.status or "").strip().lower()
    if status_perfil == "apagado" and not pode_moderar:
        abort(404)

    try:
        _registrar_presenca_forum_usuario()
    except Exception:
        pass

    tab = (request.args.get("tab") or "topicos").strip().lower()
    if tab not in ("topicos", "comentarios", "seguidores", "seguindo"):
        tab = "topicos"

    is_self = bool(viewer and viewer.id == perfil.id)

    # Se o usuário logado estiver tentando abrir o próprio perfil público,
    # redireciona para a visão correta dele.
    if is_self:
        return redirect(url_for("forum.meus_topicos"))

    relacao_status = None
    if viewer and not is_self:
        relacao_status = _status_relacao_seguir(viewer.id, perfil.id)

    is_following = relacao_status == "aceito"
    follow_pending = relacao_status == "pendente"

    pode_ver = _pode_ver_conteudo_perfil(perfil, viewer)

    if pode_ver:
        seguidores_count = (
            bd.session.query(func.count(Usuario.id))
            .join(ForumSeguidor, ForumSeguidor.seguidor_id == Usuario.id)
            .filter(
                ForumSeguidor.seguido_id == perfil.id,
                func.lower(ForumSeguidor.status) == "aceito",
                func.lower(Usuario.status) != "apagado",
            )
            .scalar()
        ) or 0

        seguindo_count = (
            bd.session.query(func.count(Usuario.id))
            .join(ForumSeguidor, ForumSeguidor.seguido_id == Usuario.id)
            .filter(
                ForumSeguidor.seguidor_id == perfil.id,
                func.lower(ForumSeguidor.status) == "aceito",
                func.lower(Usuario.status) != "apagado",
            )
            .scalar()
        ) or 0
    else:
        seguidores_count = None
        seguindo_count = None

    meus_topicos = []
    meus_comentarios = []
    seguidores_list = []
    seguindo_list = []

    if pode_ver:
        atividade_expr = func.coalesce(
            ForumTopico.ultimo_comentario_em,
            ForumTopico.criado_em
        )

        # TÓPICOS
        q_topicos = (
            ForumTopico.query
            .options(joinedload(ForumTopico.livro), joinedload(ForumTopico.autor))
            .filter(ForumTopico.autor_id == perfil.id)
        )

        if not (pode_moderar or is_self):
            q_topicos = q_topicos.filter(
                ForumTopico.oculto.is_(False),
                ForumTopico.excluido.is_(False),
            )
        else:
            q_topicos = q_topicos.filter(ForumTopico.excluido.is_(False))

        meus_topicos = (
            q_topicos
            .order_by(atividade_expr.desc(), ForumTopico.id.desc())
            .limit(80)
            .all()
        )

        # COMENTÁRIOS
        q_comentarios = (
            bd.session.query(ForumComentario, ForumTopico)
            .join(ForumTopico, ForumTopico.id == ForumComentario.topico_id)
            .filter(ForumComentario.autor_id == perfil.id)
        )

        if not (pode_moderar or is_self):
            q_comentarios = q_comentarios.filter(
                ForumTopico.oculto.is_(False),
                ForumTopico.excluido.is_(False),
            )
        else:
            q_comentarios = q_comentarios.filter(ForumTopico.excluido.is_(False))

        meus_comentarios = (
            q_comentarios
            .order_by(ForumComentario.criado_em.desc(), ForumComentario.id.desc())
            .limit(80)
            .all()
        )

        # SEGUIDORES
        seguidores_list = (
            bd.session.query(Usuario)
            .join(ForumSeguidor, ForumSeguidor.seguidor_id == Usuario.id)
            .filter(
                ForumSeguidor.seguido_id == perfil.id,
                func.lower(ForumSeguidor.status) == "aceito",
                func.lower(Usuario.status) != "apagado",
            )
            .order_by(ForumSeguidor.criado_em.desc())
            .limit(120)
            .all()
        )

        # SEGUINDO
        seguindo_list = (
            bd.session.query(Usuario)
            .join(ForumSeguidor, ForumSeguidor.seguido_id == Usuario.id)
            .filter(
                ForumSeguidor.seguidor_id == perfil.id,
                func.lower(ForumSeguidor.status) == "aceito",
                func.lower(Usuario.status) != "apagado",
            )
            .order_by(ForumSeguidor.criado_em.desc())
            .limit(120)
            .all()
        )

    sidebar = _dados_sidebar_forum()

    return render_template(
        "forum/perfil_usuario.html",
        usuario_logado=viewer,
        funcionario_logado=func_logado,
        pode_moderar=pode_moderar,
        perfil=perfil,
        tab=tab,
        pode_ver=pode_ver,
        is_self=is_self,
        is_following=is_following,
        seguidores_count=seguidores_count,
        seguindo_count=seguindo_count,
        meus_topicos=meus_topicos,
        meus_comentarios=meus_comentarios,
        seguidores_list=seguidores_list,
        seguindo_list=seguindo_list,
        _nome_usuario=_nome_usuario,
        _resumo_texto=_resumo_texto,
        _titulo_livro=_titulo_livro,
        relacao_status=relacao_status,
        follow_pending=follow_pending,
        **sidebar,
    )

# ============================================================
# Seguir / deixar de seguir / privacidade
# ============================================================

@forum_bp.post("/u/<nome_usuario>/seguir")
@forum_login_required
def seguir_usuario(nome_usuario: str):
    viewer = _usuario_logado()
    perfil = Usuario.query.filter(
        func.lower(Usuario.nome_usuario) == nome_usuario.lower()
    ).first_or_404()

    if (perfil.status or "").strip().lower() == "apagado":
        abort(404)

    if viewer.id == perfil.id:
        flash("Você não pode seguir você mesmo.", "warning")
        return redirect(url_for("forum.perfil_usuario", nome_usuario=perfil.nome_usuario))

    relacao_existente = _relacao_seguir(viewer.id, perfil.id)
    status_existente = (relacao_existente.status or "").strip().lower() if relacao_existente else None

    if status_existente == "aceito":
        flash(f"Você já segue {perfil.nome_usuario}.", "info")
        return redirect(url_for("forum.perfil_usuario", nome_usuario=perfil.nome_usuario))

    if status_existente == "pendente":
        flash(f"Você já enviou uma solicitação para seguir {perfil.nome_usuario}.", "info")
        return redirect(url_for("forum.perfil_usuario", nome_usuario=perfil.nome_usuario))

    privacidade = (perfil.forum_privacidade or "todos").strip().lower()
    novo_status = "pendente" if privacidade == "seguidores" else "aceito"

    nova_relacao = ForumSeguidor(
        seguidor_id=viewer.id,
        seguido_id=perfil.id,
        status=novo_status,
        criado_em=datetime.utcnow(),
        respondido_em=None,
    )
    bd.session.add(nova_relacao)

    if novo_status == "aceito":
        flash(f"Você agora segue {perfil.nome_usuario}.", "success")
    else:
        flash(f"Solicitação enviada para seguir {perfil.nome_usuario}.", "success")

    bd.session.commit()
    return redirect(url_for("forum.perfil_usuario", nome_usuario=perfil.nome_usuario))


@forum_bp.post("/u/<nome_usuario>/deixar-seguir")
@forum_login_required
def deixar_seguir_usuario(nome_usuario: str):
    viewer = _usuario_logado()
    perfil = Usuario.query.filter(
        func.lower(Usuario.nome_usuario) == nome_usuario.lower()
    ).first_or_404()

    if (perfil.status or "").strip().lower() == "apagado":
        abort(404)

    relacao = _relacao_seguir(viewer.id, perfil.id)
    if not relacao:
        flash(f"Você não segue {perfil.nome_usuario}.", "info")
        return redirect(url_for("forum.perfil_usuario", nome_usuario=perfil.nome_usuario))

    status_relacao = (relacao.status or "").strip().lower()
    bd.session.delete(relacao)
    bd.session.commit()

    if status_relacao == "pendente":
        flash(f"Solicitação para seguir {perfil.nome_usuario} cancelada.", "success")
    else:
        flash(f"Você deixou de seguir {perfil.nome_usuario}.", "success")

    return redirect(url_for("forum.perfil_usuario", nome_usuario=perfil.nome_usuario))

@forum_bp.post("/seguidores/<int:relacao_id>/aceitar")
@forum_login_required
def aceitar_solicitacao_seguidor(relacao_id: int):
    usuario = _usuario_logado()

    relacao = (
        ForumSeguidor.query
        .options(
            joinedload(ForumSeguidor.seguidor),
            joinedload(ForumSeguidor.seguido),
        )
        .filter(
            ForumSeguidor.id == relacao_id,
            ForumSeguidor.seguido_id == usuario.id,
        )
        .first_or_404()
    )

    status_atual = (relacao.status or "").strip().lower()

    if status_atual == "aceito":
        flash("Essa solicitação já foi aceita.", "info")
        return _redirect_back("forum.meus_topicos")

    if status_atual != "pendente":
        flash("Essa solicitação não está mais pendente.", "warning")
        return _redirect_back("forum.meus_topicos")

    relacao.status = "aceito"
    relacao.respondido_em = datetime.utcnow()

    criar_notificacao(
        usuario_id=relacao.seguidor_id,
        ator_id=usuario.id,
        tipo="solicitacao_seguidor_aceita",
        titulo="Sua solicitação foi aceita",
        mensagem=f"{usuario.nome_usuario} aceitou sua solicitação para seguir o perfil.",
        link=url_for("forum.perfil_usuario", nome_usuario=usuario.nome_usuario),
    )

    bd.session.commit()

    flash("Solicitação aceita.", "success")
    return _redirect_back("forum.meus_topicos")


@forum_bp.post("/seguidores/<int:relacao_id>/recusar")
@forum_login_required
def recusar_solicitacao_seguidor(relacao_id: int):
    usuario = _usuario_logado()

    relacao = (
        ForumSeguidor.query
        .options(joinedload(ForumSeguidor.seguidor))
        .filter(
            ForumSeguidor.id == relacao_id,
            ForumSeguidor.seguido_id == usuario.id,
        )
        .first_or_404()
    )

    status_atual = (relacao.status or "").strip().lower()

    if status_atual != "pendente":
        if _is_ajax_request():
            return {"ok": False, "message": "Essa solicitação não está mais pendente."}, 400

        flash("Essa solicitação não está mais pendente.", "warning")
        return _redirect_back("forum.meus_topicos")

    bd.session.delete(relacao)
    bd.session.commit()

    if _is_ajax_request():
        return {"ok": True, "message": "Solicitação recusada."}

    flash("Solicitação recusada.", "success")
    return _redirect_back("forum.meus_topicos")


@forum_bp.post("/u/<nome_usuario>/privacidade")
@forum_login_required
def alterar_privacidade_forum(nome_usuario: str):
    viewer = _usuario_logado()
    perfil = Usuario.query.filter(func.lower(Usuario.nome_usuario) == nome_usuario.lower()).first_or_404()

    if (perfil.status or "").strip().lower() == "apagado":
        abort(404)

    if viewer.id != perfil.id:
        abort(403)

    priv = (request.form.get("forum_privacidade") or "todos").strip().lower()
    if priv not in ("todos", "seguidores"):
        priv = "todos"

    perfil.forum_privacidade = priv
    bd.session.commit()

    flash("Privacidade do fórum atualizada.", "success")

    destino = request.form.get("next") or request.referrer
    if destino:
        return redirect(destino)

    return redirect(url_for("forum.perfil_usuario", nome_usuario=perfil.nome_usuario))


# ============================================================
# Sino de notificação
# ============================================================

@forum_bp.get("/notificacoes/<int:notificacao_id>/abrir")
@forum_login_required
def abrir_notificacao(notificacao_id):
    usuario_id = session.get("usuario_id")

    notificacao = Notificacao.query.filter_by(
        id=notificacao_id,
        usuario_id=usuario_id,
    ).first_or_404()

    if not notificacao.lida:
        notificacao.lida = True
        bd.session.commit()

    return redirect(notificacao.link or url_for("forum.lista_topicos"))


@forum_bp.route("/notificacoes/marcar-todas-lidas", methods=["POST"])
@forum_login_required
def marcar_todas_notificacoes_lidas():
    usuario = _usuario_logado()

    (
        Notificacao.query
        .filter_by(usuario_id=usuario.id, lida=False)
        .update({"lida": True}, synchronize_session=False)
    )
    bd.session.commit()

    if _is_ajax_request():
        return {"ok": True, "message": "Todas as notificações foram marcadas como lidas."}

    return redirect(request.referrer or url_for("forum.lista_topicos"))