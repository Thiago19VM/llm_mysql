# app/blueprints/forum/moderacao_routes.py
from __future__ import annotations

from functools import wraps

from flask import (
    abort, flash, g, redirect, render_template, request, session, url_for
)
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from . import forum_bp
from app.extensions import bd
from app.utils.datetime_utils import agora_utc
from app.models.funcionario import Funcionario
from app.models.forum import ForumTopico, ForumModeracaoLog
from app.models.notificacao import Notificacao


# ==========================================================
# Helpers de autenticação / permissão
# ==========================================================

def _funcionario_logado_obj():
    funcionario_id = session.get("funcionario_id")
    if not funcionario_id:
        return None
    return Funcionario.query.get(funcionario_id)


def moderacao_forum_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        funcionario = _funcionario_logado_obj()
        if not funcionario:
            flash("Faça login como funcionário para acessar a moderação do fórum.", "warning")
            return redirect(url_for("auth.login"))

        g.funcionario_moderador = funcionario
        return fn(*args, **kwargs)

    return wrapper


# ==========================================================
# Helpers de moderação
# ==========================================================

def _buscar_topico_para_moderacao(topico_id: int) -> ForumTopico:
    topico = (
        ForumTopico.query
        .options(
            selectinload(ForumTopico.autor),
            selectinload(ForumTopico.livro),
        )
        .filter(ForumTopico.id == topico_id)
        .first()
    )

    if not topico:
        abort(404)

    return topico


def _motivo_form():
    motivo = (request.form.get("motivo") or "").strip()
    if not motivo:
        flash("Informe o motivo da ação de moderação.", "warning")
        return None
    return motivo


def _nome_funcionario(funcionario: Funcionario | None) -> str:
    if not funcionario:
        return "Equipe de moderação"

    for attr in ("nome", "nome_completo", "usuario", "email"):
        valor = getattr(funcionario, attr, None)
        if valor:
            return str(valor).strip()

    return f"Funcionário #{funcionario.id}"


def _registrar_log(
    topico: ForumTopico,
    moderador: Funcionario,
    acao: str,
    motivo: str,
    detalhes: str | None = None,
):
    log = ForumModeracaoLog(
        topico_id=topico.id,
        moderador_id=moderador.id,
        autor_id=topico.autor_id,
        acao=acao,
        motivo=motivo,
        detalhes=(detalhes or "").strip(),
        criado_em=agora_utc(),
    )
    bd.session.add(log)


def _criar_notificacao(usuario_id, tipo, titulo, mensagem, link=None):
    notif = Notificacao(
        usuario_id=usuario_id,
        ator_id=None,  # moderação feita por funcionário, não por usuário comum
        tipo=tipo,
        titulo=(titulo or "")[:160],
        mensagem=(mensagem or "")[:255],
        link=link,
    )
    bd.session.add(notif)


def _link_topico(topico: ForumTopico) -> str:
    return url_for("forum.topico_detalhe", topico_id=topico.id, slug=topico.url_slug)


def _enviar_notificacao_autor(topico: ForumTopico, moderador: Funcionario, acao: str, motivo: str):
    if not topico.autor_id:
        return

    motivo_curto = (motivo or "").strip()[:140]
    link_topico = _link_topico(topico)

    if acao == "ocultar":
        titulo = "Seu tópico foi ocultado"
        mensagem = f"A moderação ocultou seu tópico. Motivo: {motivo_curto}"
        link = link_topico

    elif acao == "desocultar":
        titulo = "Seu tópico foi restaurado"
        mensagem = f"A moderação restaurou a visibilidade do seu tópico. Motivo: {motivo_curto}"
        link = link_topico

    elif acao == "excluir":
        titulo = "Seu tópico foi removido"
        mensagem = f"A moderação removeu seu tópico. Motivo: {motivo_curto}"
        link = url_for("forum.lista_topicos")

    elif acao == "restaurar":
        titulo = "Seu tópico foi restaurado"
        mensagem = f"A moderação restaurou seu tópico. Motivo: {motivo_curto}"
        link = link_topico

    elif acao == "trancar":
        titulo = "Seu tópico foi trancado"
        mensagem = f"A moderação trancou seu tópico. Motivo: {motivo_curto}"
        link = link_topico

    elif acao == "destrancar":
        titulo = "Seu tópico foi destrancado"
        mensagem = f"A moderação destrancou seu tópico. Motivo: {motivo_curto}"
        link = link_topico

    elif acao == "fixar":
        titulo = "Seu tópico foi fixado"
        mensagem = f"A moderação fixou seu tópico. Motivo: {motivo_curto}"
        link = link_topico

    elif acao == "desafixar":
        titulo = "Seu tópico foi desafixado"
        mensagem = f"A moderação desafixou seu tópico. Motivo: {motivo_curto}"
        link = link_topico

    else:
        titulo = "Ação de moderação no fórum"
        mensagem = f"A moderação realizou uma ação no seu tópico. Motivo: {motivo_curto}"
        link = link_topico

    _criar_notificacao(
        usuario_id=topico.autor_id,
        tipo="forum_moderacao",
        titulo=titulo,
        mensagem=mensagem,
        link=link,
    )


def _redirect_voltar():
    aba = (request.form.get("aba") or request.args.get("aba") or "ativos").strip()
    q = (request.form.get("q") or request.args.get("q") or "").strip()
    page = request.form.get("page") or request.args.get("page")

    if page and str(page).isdigit():
        return redirect(url_for("forum.painel_moderacao_forum", aba=aba, q=q, page=int(page)))

    return redirect(url_for("forum.painel_moderacao_forum", aba=aba, q=q))


# ==========================================================
# Painel principal
# ==========================================================

@forum_bp.get("/moderacao")
@moderacao_forum_required
def painel_moderacao_forum():
    aba = (request.args.get("aba") or "ativos").strip()
    q = (request.args.get("q") or "").strip()
    page = request.args.get("page", 1, type=int)

    per_page = 12

    query = (
        ForumTopico.query
        .options(
            selectinload(ForumTopico.autor),
            selectinload(ForumTopico.livro),
        )
    )

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                ForumTopico.titulo.ilike(like),
                ForumTopico.conteudo.ilike(like),
            )
        )

    if aba == "ativos":
        query = query.filter(
            ForumTopico.excluido.is_(False),
            ForumTopico.oculto.is_(False),
        )
    elif aba == "ocultos":
        query = query.filter(
            ForumTopico.excluido.is_(False),
            ForumTopico.oculto.is_(True),
        )
    elif aba == "excluidos":
        query = query.filter(ForumTopico.excluido.is_(True))
    elif aba == "trancados":
        query = query.filter(
            ForumTopico.excluido.is_(False),
            ForumTopico.trancado.is_(True),
        )
    elif aba == "todos":
        pass
    else:
        aba = "ativos"
        query = query.filter(
            ForumTopico.excluido.is_(False),
            ForumTopico.oculto.is_(False),
        )

    topicos = (
        query
        .order_by(ForumTopico.criado_em.desc(), ForumTopico.id.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    logs = (
        ForumModeracaoLog.query
        .options(
            selectinload(ForumModeracaoLog.topico),
            selectinload(ForumModeracaoLog.moderador),
            selectinload(ForumModeracaoLog.autor),
        )
        .order_by(ForumModeracaoLog.criado_em.desc(), ForumModeracaoLog.id.desc())
        .limit(30)
        .all()
    )

    stats = {
        "ativos": ForumTopico.query.filter(
            ForumTopico.excluido.is_(False),
            ForumTopico.oculto.is_(False),
        ).count(),
        "ocultos": ForumTopico.query.filter(
            ForumTopico.excluido.is_(False),
            ForumTopico.oculto.is_(True),
        ).count(),
        "excluidos": ForumTopico.query.filter(
            ForumTopico.excluido.is_(True),
        ).count(),
        "trancados": ForumTopico.query.filter(
            ForumTopico.excluido.is_(False),
            ForumTopico.trancado.is_(True),
        ).count(),
    }

    return render_template(
        "forum/moderacao_painel.html",
        aba=aba,
        q=q,
        topicos=topicos,
        logs=logs,
        stats=stats,
        funcionario_moderador=g.funcionario_moderador,
    )


# ==========================================================
# Ações de moderação
# ==========================================================

@forum_bp.post("/moderacao/topico/<int:topico_id>/ocultar")
@moderacao_forum_required
def moderacao_ocultar_topico(topico_id):
    topico = _buscar_topico_para_moderacao(topico_id)
    motivo = _motivo_form()
    if motivo is None:
        return _redirect_voltar()

    if topico.excluido:
        flash("Não é possível ocultar um tópico já excluído.", "warning")
        return _redirect_voltar()

    topico.oculto = True
    topico.ocultado_em = agora_utc()
    topico.ocultado_por_id = g.funcionario_moderador.id
    topico.motivo_ocultacao = motivo
    topico.atualizado_em = agora_utc()

    _registrar_log(topico, g.funcionario_moderador, "ocultar", motivo)
    _enviar_notificacao_autor(topico, g.funcionario_moderador, "ocultar", motivo)

    bd.session.commit()
    flash("Tópico ocultado com sucesso.", "success")
    return _redirect_voltar()


@forum_bp.post("/moderacao/topico/<int:topico_id>/desocultar")
@moderacao_forum_required
def moderacao_desocultar_topico(topico_id):
    topico = _buscar_topico_para_moderacao(topico_id)
    motivo = _motivo_form()
    if motivo is None:
        return _redirect_voltar()

    if topico.excluido:
        flash("Não é possível desocultar um tópico excluído.", "warning")
        return _redirect_voltar()

    topico.oculto = False
    topico.atualizado_em = agora_utc()

    _registrar_log(topico, g.funcionario_moderador, "desocultar", motivo)
    _enviar_notificacao_autor(topico, g.funcionario_moderador, "desocultar", motivo)

    bd.session.commit()
    flash("Tópico desocultado com sucesso.", "success")
    return _redirect_voltar()


@forum_bp.post("/moderacao/topico/<int:topico_id>/excluir")
@moderacao_forum_required
def moderacao_excluir_topico(topico_id):
    topico = _buscar_topico_para_moderacao(topico_id)
    motivo = _motivo_form()
    if motivo is None:
        return _redirect_voltar()

    topico.excluido = True
    topico.excluido_em = agora_utc()
    topico.excluido_por_id = g.funcionario_moderador.id
    topico.motivo_exclusao = motivo

    # Ao excluir pela moderação, o tópico também sai da área pública.
    topico.oculto = True
    topico.atualizado_em = agora_utc()

    _registrar_log(topico, g.funcionario_moderador, "excluir", motivo)
    _enviar_notificacao_autor(topico, g.funcionario_moderador, "excluir", motivo)

    bd.session.commit()
    flash("Tópico excluído logicamente com sucesso.", "success")
    return _redirect_voltar()


@forum_bp.post("/moderacao/topico/<int:topico_id>/restaurar")
@moderacao_forum_required
def moderacao_restaurar_topico(topico_id):
    topico = _buscar_topico_para_moderacao(topico_id)
    motivo = _motivo_form()
    if motivo is None:
        return _redirect_voltar()

    topico.excluido = False
    topico.excluido_em = None
    topico.excluido_por_id = None
    topico.motivo_exclusao = None

    # Ao restaurar, o tópico volta ao estado visível.
    topico.oculto = False
    topico.atualizado_em = agora_utc()

    _registrar_log(topico, g.funcionario_moderador, "restaurar", motivo)
    _enviar_notificacao_autor(topico, g.funcionario_moderador, "restaurar", motivo)

    bd.session.commit()
    flash("Tópico restaurado com sucesso.", "success")
    return _redirect_voltar()


@forum_bp.post("/moderacao/topico/<int:topico_id>/trancar")
@moderacao_forum_required
def moderacao_trancar_topico(topico_id):
    topico = _buscar_topico_para_moderacao(topico_id)
    motivo = _motivo_form()
    if motivo is None:
        return _redirect_voltar()

    if topico.excluido:
        flash("Não é possível trancar um tópico excluído.", "warning")
        return _redirect_voltar()

    topico.trancado = True
    topico.trancado_em = agora_utc()
    topico.trancado_por_id = g.funcionario_moderador.id
    topico.motivo_trancamento = motivo
    topico.atualizado_em = agora_utc()

    _registrar_log(topico, g.funcionario_moderador, "trancar", motivo)
    _enviar_notificacao_autor(topico, g.funcionario_moderador, "trancar", motivo)

    bd.session.commit()
    flash("Tópico trancado com sucesso.", "success")
    return _redirect_voltar()


@forum_bp.post("/moderacao/topico/<int:topico_id>/destrancar")
@moderacao_forum_required
def moderacao_destrancar_topico(topico_id):
    topico = _buscar_topico_para_moderacao(topico_id)
    motivo = _motivo_form()
    if motivo is None:
        return _redirect_voltar()

    if topico.excluido:
        flash("Não é possível destrancar um tópico excluído.", "warning")
        return _redirect_voltar()

    topico.trancado = False
    topico.trancado_em = None
    topico.trancado_por_id = None
    topico.motivo_trancamento = None
    topico.atualizado_em = agora_utc()

    _registrar_log(topico, g.funcionario_moderador, "destrancar", motivo)
    _enviar_notificacao_autor(topico, g.funcionario_moderador, "destrancar", motivo)

    bd.session.commit()
    flash("Tópico destrancado com sucesso.", "success")
    return _redirect_voltar()


@forum_bp.post("/moderacao/topico/<int:topico_id>/toggle-fixar")
@moderacao_forum_required
def moderacao_toggle_fixar(topico_id):
    topico = _buscar_topico_para_moderacao(topico_id)
    motivo = _motivo_form()
    if motivo is None:
        return _redirect_voltar()

    if topico.excluido:
        flash("Não é possível fixar ou desafixar um tópico excluído.", "warning")
        return _redirect_voltar()

    topico.fixado = not bool(topico.fixado)
    topico.atualizado_em = agora_utc()

    acao = "fixar" if topico.fixado else "desafixar"

    _registrar_log(topico, g.funcionario_moderador, acao, motivo)
    _enviar_notificacao_autor(topico, g.funcionario_moderador, acao, motivo)

    bd.session.commit()
    flash(
        "Tópico fixado com sucesso." if topico.fixado else "Tópico desafixado com sucesso.",
        "success",
    )
    return _redirect_voltar()