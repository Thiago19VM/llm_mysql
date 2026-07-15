# app/blueprints/funcionario/routes.py
import os
import csv
import io
import uuid
from datetime import datetime, timedelta, time
from functools import wraps
from sqlalchemy import desc
from sqlalchemy import or_, and_, func, text
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value
import hashlib
from flask import (
    render_template, request, redirect, url_for, flash, session, jsonify
)
from werkzeug.utils import secure_filename

from . import funcionario_bp
from app.utils.datetime_utils import agora_utc, agora_br, utc_naive_para_br
from app.extensions import bd
from app.services.politicas_emprestimo import (
    garantir_snapshot_politica_reserva,
    aplicar_bloqueio_por_atraso_configuravel,
)

from app.models.livro import Livro
from app.models.exemplar import Exemplar
from app.models.usuario import Usuario
from app.models.reserva import Reserva
from app.models.forum import ForumTopico, ForumComentario, ForumSeguidor

PRAZO_DEVOLUCAO_DIAS = 14
PRAZO_RETIRADA_HORAS = 48
ONLINE_WINDOW_MIN = 1


# =========================
# HELPERS (DATA/HORA)
# =========================
def br_naive_para_utc_naive(dt_br_naive: datetime | None):
    """
    Recebe datetime sem timezone (interpretado como horário BR)
    e devolve datetime sem timezone em UTC (para comparar com DATETIME salvo em UTC).
    """
    if dt_br_naive is None:
        return None
    # BR (UTC-3) -> UTC
    return dt_br_naive + timedelta(hours=3)


def aplicar_bloqueio_por_atraso(usuario: Usuario, dias_atraso: int):
    """Bloqueia usuário por X dias (sem multa). Mantém o maior bloqueio caso já exista."""
    if dias_atraso <= 0:
        return
    now = agora_utc()
    novo_bloqueio = now + timedelta(days=dias_atraso)

    if getattr(usuario, "bloqueado_ate", None) and usuario.bloqueado_ate > now:
        usuario.bloqueado_ate = max(usuario.bloqueado_ate, novo_bloqueio)
    else:
        usuario.bloqueado_ate = novo_bloqueio


def cancelar_retiradas_expiradas():
    """
    Cancela reservas de retirada que passaram do prazo de retirada (retirar_ate)
    e libera o exemplar.
    """
    now = agora_utc()

    expiradas = (
        Reserva.query
        .filter(
            Reserva.tipo == "retirada",
            Reserva.status == "separado",
            Reserva.retirar_ate.isnot(None),
            Reserva.retirar_ate < now
        )
        .all()
    )

    if not expiradas:
        return 0

    for r in expiradas:
        r.status = "cancelado"

        ex = Exemplar.query.get(r.exemplar_id)
        if ex and (ex.status or "").strip().lower() == "reservado":
            ex.status = "disponivel"

    bd.session.commit()
    return len(expiradas)


# =========================
# HELPERS (AUTH)
# =========================
def funcionario_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("funcionario_id"):
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


# =========================
# HELPERS (UPLOAD / CAPA)
# =========================
def _gerar_codigo_exemplar(prefixo="TMB"):
    # Ex: TMB-8f3a2c1d
    return f"{prefixo}-{uuid.uuid4().hex[:8]}"


def _static_dir_abs():
    # routes.py está em app/blueprints/funcionario/
    # ../../ => app/
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(base_dir, "static")


def _salvar_capa(file_storage):
    """
    Salva a capa em app/static/uploads/capas e retorna o caminho relativo
    para usar com url_for('static', filename=...).
    Ex: uploads/capas/abc123.png
    """
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    ext = os.path.splitext(filename)[1].lower()

    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        return None  # inválido

    new_name = f"{uuid.uuid4().hex}{ext}"

    static_dir = _static_dir_abs()
    upload_dir = os.path.join(static_dir, "uploads", "capas")
    os.makedirs(upload_dir, exist_ok=True)

    abs_path = os.path.join(upload_dir, new_name)
    file_storage.save(abs_path)

    return f"uploads/capas/{new_name}"


def _remover_capa(capa_relativa):
    """
    Remove arquivo antigo de capa (se existir) com segurança.
    capa_relativa ex: uploads/capas/abc.png
    """
    if not capa_relativa:
        return

    static_dir = os.path.abspath(_static_dir_abs())
    abs_path = os.path.abspath(os.path.join(static_dir, capa_relativa))

    # segurança: só apaga dentro de /static
    if not abs_path.startswith(static_dir + os.sep):
        return

    if os.path.isfile(abs_path):
        try:
            os.remove(abs_path)
        except Exception:
            pass


def _voltar_para_listagem():
    # volta para a mesma página (mantém filtros/paginação).
    return redirect(request.referrer or url_for("funcionario.livros"))


# =========================
# HELPERS (EXEMPLARES STATS)
# =========================
def _calc_stats_exemplares(exemplares):
    total = 0
    disp = 0
    res = 0
    emp = 0

    for ex in (exemplares or []):
        if not getattr(ex, "ativo", True):
            continue

        total += 1
        st = (ex.status or "").lower()

        if st == "disponivel":
            disp += 1
        elif st == "reservado":
            res += 1
        elif st == "emprestado":
            emp += 1

    return {
        "total": total,
        "disponivel": disp,
        "reservado": res,
        "emprestado": emp,
    }


# =========================
# HELPERS (USUÁRIOS / STATUS / AJAX)
# =========================
def _is_xhr():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _status_cols_usuario():
    """Retorna a coluna de status (se existir) e a coluna bool ativo (se existir)."""
    status_col = getattr(Usuario, "status", None) or getattr(Usuario, "situacao", None)
    ativo_col = getattr(Usuario, "ativo", None) or getattr(Usuario, "is_ativo", None)
    return status_col, ativo_col


def _cond_bloqueado(now_utc):
    """
    Condição SQL (bloqueado) considerando:
    - bloqueado_ate (se existir)
    - status/situacao == 'bloqueado' (se existir)
    OBS: aqui NÃO exclui "inativo"; a exclusão é feita em stats/filtros para não contar dobrado.
    """
    status_col, _ = _status_cols_usuario()
    cond = None

    if hasattr(Usuario, "bloqueado_ate"):
        cond = and_(Usuario.bloqueado_ate.isnot(None), Usuario.bloqueado_ate > now_utc)

    if status_col is not None:
        st = func.lower(status_col)
        cond_st = (st == "bloqueado")
        cond = or_(cond, cond_st) if cond is not None else cond_st

    return cond


def _cond_inativo():
    """
    Condição SQL (inativo) via:
    - status/situacao == 'inativo' (se existir)
    - ativo == False (se existir)
    """
    status_col, ativo_col = _status_cols_usuario()

    conds = []

    if status_col is not None:
        conds.append(func.lower(status_col) == "inativo")

    if ativo_col is not None:
        conds.append(ativo_col.is_(False))

    if not conds:
        return None

    if len(conds) == 1:
        return conds[0]
    return or_(*conds)


def _status_key_usuario_obj(u: Usuario, now_utc):
    """
    Status efetivo para UI (mutuamente exclusivo na prática):
    1) INATIVO (se status/situacao diz inativo, ou ativo==False)
    2) BLOQUEADO (status==bloqueado OU bloqueado_ate no futuro)
    3) ATIVO (default)
    """
    # 1) inativo primeiro
    raw = getattr(u, "status", None) or getattr(u, "situacao", None)
    if raw:
        s = str(raw).strip().lower()
        if s in ("inativo", "inactive"):
            return "inativo"

    if hasattr(u, "ativo") and u.ativo is False:
        return "inativo"

    # 2) bloqueado (manual ou por atraso)
    if raw:
        s = str(raw).strip().lower()
        if s in ("bloqueado", "blocked"):
            return "bloqueado"

    if hasattr(u, "bloqueado_ate") and u.bloqueado_ate and u.bloqueado_ate > now_utc:
        return "bloqueado"

    # 3) ativo
    if raw:
        s = str(raw).strip().lower()
        if s in ("ativo", "active"):
            return "ativo"

    return "ativo"


def _set_status_usuario(u: Usuario, novo: str, now_utc):
    """
    Altera status do usuário de forma compatível.
    - Se existir status/situacao: grava string
    - Se existir ativo bool: ajusta
    - Bloqueio manual: usa bloqueado_ate (~10 anos) para "bloqueado"
    - Ao ativar: opcionalmente limpa bloqueado_ate (desbloqueia, inclusive atraso) — ação de admin.
    """
    novo = (novo or "").strip().lower()
    if novo not in ("ativo", "inativo", "bloqueado"):
        raise ValueError("Status inválido")

    status_col, ativo_col = _status_cols_usuario()

    # grava status persistente se existir
    if status_col is not None:
        setattr(u, status_col.key, novo)
    elif ativo_col is not None:
        setattr(u, ativo_col.key, (novo == "ativo"))
    elif novo in ("ativo", "inativo"):
        raise ValueError("Usuário sem campo status/situacao/ativo para ativar/desativar.")

    # bloqueio via bloqueado_ate (manual / atraso)
    if hasattr(u, "bloqueado_ate"):
        if novo == "bloqueado":
            # bloqueio manual longo (não sobrescreve bloqueio por atraso, só garante que exista)
            if not u.bloqueado_ate or u.bloqueado_ate <= now_utc:
                u.bloqueado_ate = now_utc + timedelta(days=3650)
        elif novo == "ativo":
            # admin desbloqueia (remove qualquer bloqueio vigente)
            if u.bloqueado_ate and u.bloqueado_ate > now_utc:
                u.bloqueado_ate = None
        # inativo: não mexe em bloqueado_ate


def _usuarios_stats(now_utc):
    """
    Cards:
    - total = total de usuários
    - ativos = ONLINE
    - inativos = OFFLINE
    - bloqueados = bloqueio manual OU por atraso
    """
    total = Usuario.query.filter(func.lower(Usuario.status) != "apagado").count()

    # bloqueado = status == bloqueado OU bloqueado_ate no futuro
    cond_bloq = _cond_bloqueado(now_utc)

    # online = ultimo_ativo_em dentro da janela
    if not hasattr(Usuario, "ultimo_ativo_em"):
        cond_on = None
    else:
        limite = now_utc - timedelta(minutes=ONLINE_WINDOW_MIN)
        cond_on = and_(Usuario.ultimo_ativo_em.isnot(None), Usuario.ultimo_ativo_em >= limite)

    bloqueados = Usuario.query.filter(cond_bloq).count() if cond_bloq is not None else 0

    # online sempre EXCLUI bloqueados (bloqueado aparece no card "bloqueados")
    if cond_on is None:
        online = 0
    else:
        if cond_bloq is not None:
            online = Usuario.query.filter(and_(~cond_bloq, cond_on)).count()
        else:
            online = Usuario.query.filter(cond_on).count()

    # offline = total - bloqueados - online (pra não contar dobrado)
    offline = total - bloqueados - online
    if offline < 0:
        offline = 0

    return {"total": total, "ativos": online, "inativos": offline, "bloqueados": bloqueados}


def _soft_delete_usuario(u: Usuario, funcionario_id: int | None = None):
    """
    Soft delete seguro:
    - marca status como 'apagado'
    - anonimiza nome/email (libera UNIQUE)
    - remove credenciais (senha/google)
    - limpa PII (telefone/endereço/bio/foto)
    - remove vínculos sociais do fórum (seguidores/seguindo)
    """
    uid = u.id

    # remove relações de seguidores/seguindo no fórum
    ForumSeguidor.query.filter(
        or_(
            ForumSeguidor.seguidor_id == uid,
            ForumSeguidor.seguido_id == uid,
        )
    ).delete(synchronize_session=False)

    # marca status
    u.status = "apagado"

    # timestamps (se adicionar as colunas)
    if hasattr(u, "apagado_em"):
        u.apagado_em = agora_utc()
    if hasattr(u, "apagado_por"):
        u.apagado_por = funcionario_id

    # remove credenciais para impedir login
    u.hash_senha = None
    u.google_id = None

    # anonimiza (UNIQUE!)
    u.nome_usuario = f"usuario_removido_{uid}"
    u.email = f"removido_{uid}@literaweb.local"

    # limpa dados pessoais
    u.telefone = None
    u.cep = None
    u.rua = None
    u.numero = None
    u.bairro = None
    u.cidade = None
    u.uf = None
    u.complemento = None
    u.bio = None

    # foto
    u.foto = None

    # presença e bloqueio não importam mais
    u.ultimo_ativo_em = None
    u.bloqueado_ate = None

# =========================
# HELPERS (USUÁRIOS / DETALHES)
# =========================
def utc_naive_para_br_naive(dt_utc_naive: datetime | None):
    """
    Recebe datetime sem timezone (interpretado como UTC)
    e devolve datetime sem timezone em horário BR (UTC-3).
    """
    if dt_utc_naive is None:
        return None
    return dt_utc_naive - timedelta(hours=3)


def _dt_br_str(dt_utc_naive: datetime | None):
    """Formata datetime (UTC naive) em string BR."""
    dt_br = utc_naive_para_br_naive(dt_utc_naive)
    if not dt_br:
        return None
    return dt_br.strftime("%d/%m/%Y %H:%M")


def _usuario_online(u: Usuario, now_utc: datetime | None = None) -> bool:
    """Online se ultimo_ativo_em estiver dentro da janela ONLINE_WINDOW_MIN."""
    now_utc = now_utc or agora_utc()
    dt = getattr(u, "ultimo_ativo_em", None)
    if not dt:
        return False
    limite = now_utc - timedelta(minutes=ONLINE_WINDOW_MIN)
    return dt >= limite


# =========================
# HELPERS (USUÁRIOS / DETALHES)
# =========================
from datetime import date as _date


def utc_naive_para_br_naive(dt_utc_naive: datetime | None):
    """
    Recebe datetime sem timezone (interpretado como UTC)
    e devolve datetime sem timezone em horário BR (UTC-3).
    """
    if dt_utc_naive is None:
        return None
    return dt_utc_naive - timedelta(hours=3)


def _dt_br_str(value):
    """
    Formata datetime/date em string BR.
    - datetime UTC naive -> converte para BR e formata
    - date -> formata só data
    """
    if value is None:
        return None

    if isinstance(value, _date) and not isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")

    if isinstance(value, datetime):
        dt_br = utc_naive_para_br_naive(value)
        if not dt_br:
            return None
        return dt_br.strftime("%d/%m/%Y %H:%M")

    try:
        return str(value)
    except Exception:
        return None


def _usuario_online(u: Usuario, now_utc: datetime | None = None) -> bool:
    now_utc = now_utc or agora_utc()
    dt = getattr(u, "ultimo_ativo_em", None)
    if not dt:
        return False
    limite = now_utc - timedelta(minutes=ONLINE_WINDOW_MIN)
    return dt >= limite


def _nome_exibicao(u):
    return (
        getattr(u, "nome_usuario", None)
        or getattr(u, "username", None)
        or getattr(u, "nome", None)
        or getattr(u, "nome_completo", None)
        or getattr(u, "name", None)
        or (getattr(u, "email", None).split("@")[0] if getattr(u, "email", None) else None)
        or f"Usuário #{u.id}"
    )


def _gravatar_url(email: str, size: int = 160) -> str:
    email = (email or "").strip().lower()
    h = hashlib.md5(email.encode("utf-8")).hexdigest()
    return f"https://www.gravatar.com/avatar/{h}?s={size}&d=mp"


def _static_url_from_path(path: str | None):
    """
    Converte um valor salvo no banco em URL utilizável.
    Exemplos aceitos:
    - uploads/avatars/x.png
    - uploads/capas/y.webp
    - /static/uploads/avatars/x.png
    - http://...
    - https://...
    """
    if not path:
        return None

    p = str(path).strip()
    if not p:
        return None

    if p.startswith("http://") or p.startswith("https://") or p.startswith("/"):
        return p

    try:
        return url_for("static", filename=p)
    except Exception:
        return None


def _get_avatar_url(u: Usuario):
    """
    Prioridade:
    1) Usuario.foto (se existir)
    2) outros nomes possíveis
    3) gravatar
    4) placeholder
    """
    raw = (
        getattr(u, "foto", None)
        or getattr(u, "avatar_url", None)
        or getattr(u, "foto_url", None)
        or getattr(u, "foto_perfil", None)
        or getattr(u, "imagem_perfil", None)
        or getattr(u, "avatar_path", None)
        or getattr(u, "foto_path", None)
        or getattr(u, "avatar", None)
    )

    if raw:
        raw = str(raw).strip()

        # URL pronta ou caminho absoluto
        if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("/"):
            return raw

        # se no banco já estiver salvo "uploads/avatars/arquivo.png"
        if raw.startswith("uploads/"):
            try:
                return url_for("static", filename=raw)
            except Exception:
                pass

        # se no banco estiver salvo apenas o nome do arquivo
        try:
            return url_for("static", filename=f"uploads/avatars/{raw}")
        except Exception:
            pass

    email = getattr(u, "email", "") or ""
    if email.strip():
        return _gravatar_url(email, size=160)

    try:
        return url_for("static", filename="img/avatar-placeholder.png")
    except Exception:
        return None


def _get_livro_capa_url(livro: Livro | None):
    """
    A capa está em livro.capa_arquivo
    Ex: uploads/capas/abc123.webp
    """
    if not livro:
        return None

    raw = (
        getattr(livro, "capa_arquivo", None)
        or getattr(livro, "capa_url", None)
        or getattr(livro, "capa", None)
    )

    return _static_url_from_path(raw)

# =========================
# Helpers do Painel
# =========================

def _mk_status(ok: bool, detalhe_ok: str = "", detalhe_fail: str = ""):
    return {
        "ok": bool(ok),
        "label": "ATIVO" if ok else "INATIVO",
        "pill_class": "is-ok" if ok else "is-off",
        "badge_class": "panel-badge--ok" if ok else "panel-badge--danger",
        "detalhe": detalhe_ok if ok else detalhe_fail,
    }


def _check_banco_dados():
    """
    Verifica se a conexão com o banco responde.
    O SELECT 1 teste simples.
    """
    try:
        bd.session.execute(text("SELECT 1"))
        return _mk_status(
            True,
            detalhe_ok="Conexão com o banco respondendo normalmente.",
            detalhe_fail="Banco de dados indisponível."
        )
    except Exception:
        bd.session.rollback()
        return _mk_status(
            False,
            detalhe_ok="Conexão com o banco respondendo normalmente.",
            detalhe_fail="Banco de dados indisponível."
        )


def _check_sistema():
    """
    Verifica o núcleo da aplicação:
    - a rota está sendo executada
    - diretório static existe
    - diretório uploads existe ou pode ser criado
    """
    try:
        static_dir = _static_dir_abs()
        uploads_dir = os.path.join(static_dir, "uploads")

        if not os.path.isdir(static_dir):
            return _mk_status(
                False,
                detalhe_ok="Aplicação operando normalmente.",
                detalhe_fail="Diretório static não encontrado."
            )

        os.makedirs(uploads_dir, exist_ok=True)

        return _mk_status(
            True,
            detalhe_ok="Aplicação web operando normalmente.",
            detalhe_fail="Falha interna no sistema."
        )
    except Exception:
        return _mk_status(
            False,
            detalhe_ok="Aplicação web operando normalmente.",
            detalhe_fail="Falha interna no sistema."
        )


def _check_forum():
    """
    Verifica se o módulo fórum está acessível.
    Se conseguir consultar as tabelas do fórum, considera ativo.
    """
    try:
        total_topicos = bd.session.query(func.count(ForumTopico.id)).scalar() or 0
        total_comentarios = bd.session.query(func.count(ForumComentario.id)).scalar() or 0

        return _mk_status(
            True,
            detalhe_ok=f"{total_topicos} tópico(s) • {total_comentarios} comentário(s).",
            detalhe_fail="Módulo fórum indisponível."
        )
    except Exception:
        bd.session.rollback()
        return _mk_status(
            False,
            detalhe_ok="Fórum operando normalmente.",
            detalhe_fail="Módulo fórum indisponível."
        )

def _restaurar_livro(livro: Livro):
    livro.ativo = True

    if hasattr(livro, "excluido_em"):
        livro.excluido_em = None

    for ex in livro.exemplares:
        ex.ativo = True

        if hasattr(ex, "excluido_em"):
            ex.excluido_em = None

        status_atual = (getattr(ex, "status", "") or "").strip().lower()
        if status_atual not in {"disponivel", "reservado", "emprestado"}:
            ex.status = "disponivel"

# =========================
# PAINEL
# =========================
@funcionario_bp.get("/painel")
@funcionario_required
def painel():
    now_br = agora_br()
    now_utc = agora_utc()

    # Status
    status_sistema = _check_sistema()
    status_bd = _check_banco_dados()

    # Defaults seguros
    total_livros = 0
    usuarios_online = 0
    reservas_ativas = 0
    livros_disponiveis = 0
    top_emprestados = []
    top_comentados = []

    # Só tenta buscar métricas se o banco estiver OK
    if status_bd["ok"]:
        try:
            total_livros = Livro.query.filter(Livro.ativo.is_(True)).count()
        except Exception:
            bd.session.rollback()
            total_livros = 0

        try:
            stats_usuarios = _usuarios_stats(now_utc)
            usuarios_online = stats_usuarios.get("ativos", 0)
        except Exception:
            bd.session.rollback()
            usuarios_online = 0

        try:
            reservas_ativas = (
                Reserva.query
                .filter(Reserva.status.in_(["pendente", "aprovada", "separado", "enviado", "retirado"]))
                .count()
            )
        except Exception:
            bd.session.rollback()
            reservas_ativas = 0

        try:
            # Conta títulos com pelo menos 1 exemplar disponível
            livros_disponiveis = (
                bd.session.query(func.count(func.distinct(Livro.id)))
                .select_from(Livro)
                .join(Exemplar, Exemplar.livro_id == Livro.id)
                .filter(
                    Livro.ativo.is_(True),
                    Exemplar.ativo.is_(True),
                    Exemplar.status == "disponivel"
                )
                .scalar()
            ) or 0
        except Exception:
            bd.session.rollback()
            livros_disponiveis = 0

        try:
            top_emprestados = (
                bd.session.query(
                    Livro.id.label("id"),
                    Livro.titulo.label("titulo"),
                    func.count(Reserva.id).label("total")
                )
                .select_from(Livro)
                .join(Exemplar, Exemplar.livro_id == Livro.id)
                .join(Reserva, Reserva.exemplar_id == Exemplar.id)
                .filter(
                    Livro.ativo.is_(True),
                    Reserva.emprestado_em.isnot(None)
                )
                .group_by(Livro.id, Livro.titulo)
                .order_by(func.count(Reserva.id).desc(), Livro.titulo.asc())
                .limit(3)
                .all()
            )
        except Exception:
            bd.session.rollback()
            top_emprestados = []

        try:
            top_comentados = (
                bd.session.query(
                    Livro.id.label("id"),
                    Livro.titulo.label("titulo"),
                    func.count(ForumComentario.id).label("total")
                )
                .select_from(Livro)
                .join(ForumTopico, ForumTopico.livro_id == Livro.id)
                .join(ForumComentario, ForumComentario.topico_id == ForumTopico.id)
                .filter(Livro.ativo.is_(True))
                .group_by(Livro.id, Livro.titulo)
                .order_by(func.count(ForumComentario.id).desc(), Livro.titulo.asc())
                .limit(3)
                .all()
            )
        except Exception:
            bd.session.rollback()
            top_comentados = []

        status_forum = _check_forum()
    else:
        status_forum = _mk_status(
            False,
            detalhe_ok="Fórum operando normalmente.",
            detalhe_fail="Dependente do banco de dados."
        )

    # Status geral
    tudo_ok = status_sistema["ok"] and status_bd["ok"] and status_forum["ok"]
    status_geral_label = "ATIVO" if tudo_ok else "INSTÁVEL"
    status_geral_class = "panel-badge--ok" if tudo_ok else "panel-badge--danger"

    return render_template(
        "funcionario/painel.html",
        data_hoje=now_br.strftime("%d/%m/%Y"),
        hora_agora=now_br.strftime("%H:%M"),

        total_livros=total_livros,
        usuarios_online=usuarios_online,
        reservas_ativas=reservas_ativas,
        livros_disponiveis=livros_disponiveis,
        top_emprestados=top_emprestados,
        top_comentados=top_comentados,

        status_bd=status_bd,
        status_sistema=status_sistema,
        status_forum=status_forum,
        status_geral_label=status_geral_label,
        status_geral_class=status_geral_class,
    )

@funcionario_bp.get("/painel/atualizar")
@funcionario_required
def painel_atualizar():
    now_br = agora_br()
    now_utc = agora_utc()

    status_sistema = _check_sistema()
    status_bd = _check_banco_dados()

    total_livros = 0
    usuarios_online = 0
    reservas_ativas = 0
    livros_disponiveis = 0

    if status_bd["ok"]:
        try:
            total_livros = Livro.query.filter(Livro.ativo.is_(True)).count()
        except Exception:
            bd.session.rollback()
            total_livros = 0

        try:
            stats_usuarios = _usuarios_stats(now_utc)
            usuarios_online = stats_usuarios.get("ativos", 0)
        except Exception:
            bd.session.rollback()
            usuarios_online = 0

        try:
            reservas_ativas = (
                Reserva.query
                .filter(Reserva.status.in_(["pendente", "aprovada", "separado", "enviado", "retirado"]))
                .count()
            )
        except Exception:
            bd.session.rollback()
            reservas_ativas = 0

        try:
            livros_disponiveis = (
                bd.session.query(func.count(func.distinct(Livro.id)))
                .select_from(Livro)
                .join(Exemplar, Exemplar.livro_id == Livro.id)
                .filter(
                    Livro.ativo.is_(True),
                    Exemplar.ativo.is_(True),
                    Exemplar.status == "disponivel"
                )
                .scalar()
            ) or 0
        except Exception:
            bd.session.rollback()
            livros_disponiveis = 0

        status_forum = _check_forum()
    else:
        status_forum = _mk_status(
            False,
            detalhe_ok="Fórum operando normalmente.",
            detalhe_fail="Dependente do banco de dados."
        )

    tudo_ok = status_sistema["ok"] and status_bd["ok"] and status_forum["ok"]

    return jsonify({
        "ok": True,
        "data_hoje": now_br.strftime("%d/%m/%Y"),
        "hora_agora": now_br.strftime("%H:%M"),

        "metricas": {
            "total_livros": total_livros,
            "usuarios_online": usuarios_online,
            "reservas_ativas": reservas_ativas,
            "livros_disponiveis": livros_disponiveis,
        },

        "status_geral": {
            "label": "ATIVO" if tudo_ok else "INSTÁVEL",
            "class": "panel-badge--ok" if tudo_ok else "panel-badge--danger",
        },

        "status_bd": status_bd,
        "status_sistema": status_sistema,
        "status_forum": status_forum,
    })

# =========================
# LIVROS - LISTAGEM
# =========================
GENERO_LABELS = {
    "acao": "Ação",
    "aventura": "Aventura",
    "autoajuda": "Autoajuda / Desenvolvimento pessoal",
    "biografia": "Biografia / Autobiografia",
    "ciencia": "Ciência / Divulgação científica",
    "classicos": "Clássicos",
    "contos": "Contos",
    "culinaria": "Culinária",
    "direito": "Direito",
    "drama": "Drama",
    "educacao": "Educação",
    "fantasia": "Fantasia",
    "ficcao": "Ficção",
    "ficcao_cientifica": "Ficção Científica",
    "filosofia": "Filosofia",
    "historia": "História",
    "hq": "HQ",
    "infantil": "Infantil",
    "juvenil": "Juvenil (YA)",
    "misterio": "Mistério",
    "nao_ficcao": "Não ficção",
    "poesia": "Poesia",
    "romance": "Romance",
    "suspense": "Suspense",
    "tecnologia": "Tecnologia",
    "terror": "Terror",
}

def _label_genero(valor):
    if not valor:
        return None

    chave = str(valor).strip().lower()
    return GENERO_LABELS.get(
        chave,
        str(valor).replace("_", " ").strip().title()
    )

@funcionario_bp.app_template_filter("genero_label")
def genero_label_filter(valor):
    return _label_genero(valor)

@funcionario_bp.get("/livros")
@funcionario_required
def livros():
    # aba atual (usada só para manter compatibilidade com filtros/querystring)
    aba = (request.args.get("aba") or "cadastrados").strip().lower()
    if aba not in ("cadastrados", "excluidos"):
        aba = "cadastrados"

    # filtros
    q = (request.args.get("q") or "").strip()
    genero = (request.args.get("genero") or "").strip()
    disp = (request.args.get("disp") or "").strip().lower()  # disponivel | indisponivel | ""
    ano_raw = (request.args.get("ano") or "").strip()
    edicao = (request.args.get("edicao") or "").strip()

    # paginação separada
    page = request.args.get("page", 1, type=int)
    page_excluidos = request.args.get("page_excluidos", 1, type=int)
    per_page = 10

    def aplicar_filtros_base(query):
        if q:
            like = f"%{q}%"
            query = query.filter(
                (Livro.titulo.ilike(like)) |
                (Livro.autor.ilike(like))
            )

        if genero:
            query = query.filter(Livro.genero == genero)

        if ano_raw.isdigit():
            query = query.filter(Livro.ano == int(ano_raw))

        if edicao:
            query = query.filter(Livro.edicao.ilike(f"%{edicao}%"))

        return query

    def preparar_livros(lista_livros):
        for livro in lista_livros:
            exemplares_ativos = [ex for ex in livro.exemplares if getattr(ex, "ativo", True)]
            stats = _calc_stats_exemplares(exemplares_ativos)

            livro.stats_total = stats["total"]
            livro.stats_disponivel = stats["disponivel"]
            livro.stats_reservado = stats["reservado"]
            livro.stats_emprestado = stats["emprestado"]

            livro.genero_label = _label_genero(livro.genero)

            loc_padrao = None
            for ex in livro.exemplares:
                if getattr(ex, "ativo", True):
                    loc_padrao = ex.localizacao
                    break

            livro.localizacao_padrao = loc_padrao
            livro.localizacao = loc_padrao

    # =========================
    # LIVROS CADASTRADOS
    # =========================
    query_cadastrados = (
        Livro.query
        .options(selectinload(Livro.exemplares))
        .filter(Livro.ativo.is_(True))
    )

    query_cadastrados = aplicar_filtros_base(query_cadastrados)

    # disponibilidade só para cadastrados
    if disp in ("disponivel", "indisponivel"):
        available_exists = bd.session.query(Exemplar.id).filter(
            Exemplar.livro_id == Livro.id,
            Exemplar.ativo.is_(True),
            Exemplar.status == "disponivel"
        ).exists()

        if disp == "disponivel":
            query_cadastrados = query_cadastrados.filter(available_exists)
        else:
            query_cadastrados = query_cadastrados.filter(~available_exists)

    pag = query_cadastrados.order_by(Livro.id.asc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    preparar_livros(pag.items)

    # =========================
    # LIVROS EXCLUÍDOS
    # =========================
    query_excluidos = (
        Livro.query
        .options(selectinload(Livro.exemplares))
        .filter(Livro.ativo.is_(False))
    )

    query_excluidos = aplicar_filtros_base(query_excluidos)

    pag_excluidos = query_excluidos.order_by(Livro.id.asc()).paginate(
        page=page_excluidos,
        per_page=per_page,
        error_out=False
    )

    preparar_livros(pag_excluidos.items)

    # querystring sem páginas
    args_no_page = request.args.to_dict(flat=True)
    args_no_page.pop("page", None)
    args_no_page.pop("page_excluidos", None)

    pages = list(range(max(1, pag.page - 2), min(pag.pages, pag.page + 2) + 1))
    pages_excluidos = list(
        range(
            max(1, pag_excluidos.page - 2),
            min(pag_excluidos.pages, pag_excluidos.page + 2) + 1
        )
    )

    now = agora_br()
    return render_template(
        "funcionario/livros.html",
        livros=pag.items,
        pag=pag,
        pages=pages,

        livros_excluidos=pag_excluidos.items,
        pag_excluidos=pag_excluidos,
        pages_excluidos=pages_excluidos,

        args_no_page=args_no_page,
        aba=aba,
        data_hoje=now.strftime("%d/%m/%Y"),
        hora_agora=now.strftime("%H:%M"),
    )


@funcionario_bp.get("/livros/novo")
@funcionario_required
def livros_novo_redirect():
    return redirect(url_for("funcionario.adicionar_livro"))


# =========================
# LIVROS - ADICIONAR (GET + POST)
# =========================
@funcionario_bp.get("/livros/adicionar")
@funcionario_required
def adicionar_livro():
    now = agora_br()
    return render_template(
        "funcionario/adicionar_livro.html",
        data_hoje=now.strftime("%d/%m/%Y"),
        hora_agora=now.strftime("%H:%M"),
    )


@funcionario_bp.post("/livros/adicionar")
@funcionario_required
def adicionar_livro_post():
    titulo = (request.form.get("titulo") or "").strip()
    autor = (request.form.get("autor") or "").strip()
    genero = (request.form.get("genero") or "").strip() or None
    editora = (request.form.get("editora") or "").strip() or None

    ano_raw = (request.form.get("ano") or "").strip()
    ano = int(ano_raw) if ano_raw.isdigit() else None

    isbn = (request.form.get("isbn") or "").strip() or None
    sinopse = (request.form.get("sinopse") or "").strip() or None
    edicao = (request.form.get("edicao") or "").strip() or None

    formato = (request.form.get("formato") or request.form.get("tipo_capa") or "").strip()


    if formato not in ("Capa Comum", "Livro de Bolso"):
        formato = "Livro de Bolso"

    qtd_raw = (request.form.get("quantidade") or "0").strip()
    quantidade = int(qtd_raw) if qtd_raw.isdigit() else 0
    if quantidade < 0:
        quantidade = 0

    localizacao = (request.form.get("localizacao") or "").strip() or None

    if not titulo or not autor:
        flash("Preencha pelo menos Título e Autor.", "error")
        return render_template("funcionario/adicionar_livro.html")

    capa_file = request.files.get("capa")
    capa_arquivo = None

    if capa_file and capa_file.filename:
        capa_arquivo = _salvar_capa(capa_file)
        if not capa_arquivo:
            flash("Arquivo de capa inválido. Use JPG, PNG ou WEBP.", "error")
            return render_template("funcionario/adicionar_livro.html")

    livro = Livro(
        titulo=titulo,
        autor=autor,
        genero=genero,
        editora=editora,
        ano=ano,
        isbn=isbn,
        edicao=edicao,
        tipo_capa=formato,
        sinopse=sinopse,
        capa_arquivo=capa_arquivo,
    )
    bd.session.add(livro)
    bd.session.flush()

    for _ in range(quantidade):
        ex = Exemplar(
            livro_id=livro.id,
            codigo_exemplar=_gerar_codigo_exemplar(),
            status="disponivel",
            localizacao=localizacao,
            ativo=True,
        )
        bd.session.add(ex)

    bd.session.commit()
    flash("Livro cadastrado com sucesso!", "success")
    return redirect(url_for("funcionario.livros"))


# =========================
# LIVROS - EDITAR (POST do MODAL)
# =========================
@funcionario_bp.post("/livros/<int:livro_id>/editar")
@funcionario_required
def editar_livro(livro_id):
    livro = Livro.query.filter(
        Livro.id == livro_id,
        Livro.ativo.is_(True)
    ).first_or_404()

    titulo = (request.form.get("titulo") or "").strip()
    autor = (request.form.get("autor") or "").strip()
    genero = (request.form.get("genero") or "").strip() or None
    editora = (request.form.get("editora") or "").strip() or None

    ano_raw = (request.form.get("ano") or "").strip()
    ano = int(ano_raw) if ano_raw.isdigit() else None

    isbn = (request.form.get("isbn") or "").strip() or None
    sinopse = (request.form.get("sinopse") or "").strip() or None
    edicao = (request.form.get("edicao") or "").strip() or None

    tipo = (request.form.get("tipo_capa") or request.form.get("formato") or "").strip()


    if tipo and tipo not in ("Capa Comum", "Livro de Bolso"):
        flash("Formato inválido. Use apenas Capa Comum ou Livro de Bolso.", "error")
        return _voltar_para_listagem()

    if not titulo or not autor:
        flash("Título e Autor são obrigatórios.", "error")
        return _voltar_para_listagem()

    livro.titulo = titulo
    livro.autor = autor
    livro.genero = genero
    livro.editora = editora
    livro.ano = ano
    livro.isbn = isbn
    livro.sinopse = sinopse
    livro.edicao = edicao

    if tipo:
        livro.tipo_capa = tipo

    capa_file = request.files.get("capa")
    if capa_file and capa_file.filename:
        nova_capa = _salvar_capa(capa_file)
        if not nova_capa:
            flash("Arquivo de capa inválido. Use JPG, PNG ou WEBP.", "error")
            return _voltar_para_listagem()

        if livro.capa_arquivo:
            _remover_capa(livro.capa_arquivo)

        livro.capa_arquivo = nova_capa

    localizacao = (request.form.get("localizacao") or "").strip()
    aplicar_todos = request.form.get("aplicar_todos_exemplares") == "1"

    if aplicar_todos:
        loc_value = localizacao if localizacao else None
        Exemplar.query.filter(
            Exemplar.livro_id == livro.id,
            Exemplar.ativo.is_(True)
        ).update(
            {"localizacao": loc_value},
            synchronize_session=False
        )
    else:
        if localizacao != "":
            ex = (
                Exemplar.query
                .filter(
                    Exemplar.livro_id == livro.id,
                    Exemplar.ativo.is_(True)
                )
                .order_by(Exemplar.id.asc())
                .first()
            )
            if ex:
                ex.localizacao = localizacao

    bd.session.commit()
    flash("Livro atualizado com sucesso!", "success")
    return _voltar_para_listagem()

STATUS_RESERVA_BLOQUEIA_EXCLUSAO_LIVRO = {
    "pendente",
    "aprovada",
    "separado",
    "enviado",
    "retirado",
}

STATUS_RESERVA_BLOQUEIA_EXCLUSAO_USUARIO = {
    "pendente",
    "aprovada",
    "separado",
    "enviado",
    "retirado",
}

def _usuario_tem_reserva_bloqueante(usuario_id: int) -> bool:
    return (
        bd.session.query(Reserva.id)
        .filter(
            Reserva.usuario_id == usuario_id,
            Reserva.status.in_(STATUS_RESERVA_BLOQUEIA_EXCLUSAO_USUARIO)
        )
        .first()
        is not None
    )


def _livro_tem_reserva_bloqueante(livro_id: int) -> bool:
    return bd.session.query(Reserva.id)\
        .join(Exemplar, Exemplar.id == Reserva.exemplar_id)\
        .filter(
            Exemplar.livro_id == livro_id,
            Exemplar.ativo.is_(True),
            Reserva.status.in_(STATUS_RESERVA_BLOQUEIA_EXCLUSAO_LIVRO)
        )\
        .first() is not None

def _soft_delete_livro(livro: Livro):
    now = agora_utc()

    livro.ativo = False
    if hasattr(livro, "excluido_em"):
        livro.excluido_em = now

    for ex in livro.exemplares:
        ex.ativo = False
        if hasattr(ex, "excluido_em"):
            ex.excluido_em = now

# =========================
#   Excluir livro
# =========================
@funcionario_bp.post("/livros/<int:livro_id>/excluir")
@funcionario_required
def excluir_livro(livro_id):
    livro = (
        Livro.query
        .options(selectinload(Livro.exemplares))
        .filter(Livro.id == livro_id, Livro.ativo.is_(True))
        .first_or_404()
    )

    if _livro_tem_reserva_bloqueante(livro.id):
        flash(
            "Não é possível excluir este livro porque há exemplar com reserva/empréstimo ativo.",
            "error"
        )
        return _voltar_para_listagem()

    _soft_delete_livro(livro)
    bd.session.commit()

    flash("Livro excluído com sucesso. O histórico foi preservado.", "success")
    return _voltar_para_listagem()


# =========================
#   Restaurar livro
# =========================

@funcionario_bp.post("/livros/<int:livro_id>/restaurar")
@funcionario_required
def restaurar_livro(livro_id):
    livro = (
        Livro.query
        .options(selectinload(Livro.exemplares))
        .filter(Livro.id == livro_id, Livro.ativo.is_(False))
        .first_or_404()
    )

    _restaurar_livro(livro)
    bd.session.commit()

    flash("Livro restaurado com sucesso.", "success")
    return redirect(url_for("funcionario.livros", aba="cadastrados"))

# =========================
# LIVROS - IMPORTAR CSV
# =========================
@funcionario_bp.post("/livros/importar-csv")
@funcionario_required
def importar_livros_csv():
    file = request.files.get("csv_file")
    has_header = request.form.get("csv_header") == "1"

    if not file or not file.filename:
        flash("Selecione um arquivo CSV.", "error")
        return redirect(url_for("funcionario.adicionar_livro"))

    raw = file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    sample = text[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","

    f = io.StringIO(text)
    reader = csv.reader(f, delimiter=delimiter)

    rows = list(reader)
    if not rows:
        flash("CSV vazio.", "error")
        return redirect(url_for("funcionario.adicionar_livro"))

    if has_header:
        header = [h.strip().lower() for h in rows[0]]
        data_rows = rows[1:]
        col_index = {name: idx for idx, name in enumerate(header)}
    else:
        col_index = {
            "titulo": 0,
            "autor": 1,
            "genero": 2,
            "editora": 3,
            "ano": 4,
            "isbn": 5,
            "edicao": 6,
            "tipo_capa": 7,
            "sinopse": 8,
            "quantidade": 9,
            "localizacao": 10,
        }
        data_rows = rows

    def get(row, key, default=""):
        idx = col_index.get(key)
        if idx is None or idx >= len(row):
            return default
        return (row[idx] or "").strip()

    if has_header:
        if "titulo" not in col_index or "autor" not in col_index:
            flash("CSV inválido: precisa ter colunas 'titulo' e 'autor'.", "error")
            return redirect(url_for("funcionario.adicionar_livro"))

    criados = 0
    erros = 0

    for _, row in enumerate(data_rows, start=2 if has_header else 1):
        titulo = get(row, "titulo").strip()
        autor = get(row, "autor").strip()

        if not titulo or not autor:
            erros += 1
            continue

        genero = get(row, "genero") or None
        editora = get(row, "editora") or None

        ano_raw = get(row, "ano")
        ano = int(ano_raw) if ano_raw.isdigit() else None

        isbn = get(row, "isbn") or None
        edicao = get(row, "edicao") or None
        sinopse = get(row, "sinopse") or None

        tipo_capa = (get(row, "tipo_capa") or "").strip()
        if tipo_capa not in ("Capa Comum", "Livro de Bolso"):
            tipo_capa = "Livro de Bolso"

        qtd_raw = get(row, "quantidade") or "1"
        quantidade = int(qtd_raw) if qtd_raw.isdigit() else 1
        if quantidade < 1:
            quantidade = 1

        localizacao = get(row, "localizacao") or None

        try:
            livro = Livro(
                titulo=titulo,
                autor=autor,
                genero=genero,
                editora=editora,
                ano=ano,
                isbn=isbn,
                edicao=edicao,
                tipo_capa=tipo_capa,
                sinopse=sinopse,
                ativo=True
            )
            bd.session.add(livro)
            bd.session.flush()

            for _ in range(quantidade):
                ex = Exemplar(
                    livro_id=livro.id,
                    codigo_exemplar=_gerar_codigo_exemplar(),
                    status="disponivel",
                    localizacao=localizacao,
                    ativo = True
                )
                bd.session.add(ex)

            criados += 1
        except Exception:
            bd.session.rollback()
            erros += 1

    try:
        bd.session.commit()
    except Exception:
        bd.session.rollback()
        flash("Falha ao salvar no banco (commit). Verifique o CSV.", "error")
        return redirect(url_for("funcionario.adicionar_livro"))

    if criados > 0:
        msg = f"Importação concluída: {criados} livro(s) criados."
        if erros:
            msg += f" {erros} linha(s) foram ignoradas por erro/validação."
        flash(msg, "success")
    else:
        flash("Nenhum livro foi importado. Verifique se o CSV tem dados válidos.", "error")

    return redirect(url_for("funcionario.livros"))


# =========================
# USUÁRIOS - LISTAGEM + STATS + RESERVAS_COUNT + FILTRO STATUS
# =========================

@funcionario_bp.get("/usuarios/stats")
@funcionario_required
def usuarios_stats():
    now_utc = agora_utc()
    return jsonify({"ok": True, "stats": _usuarios_stats(now_utc)})

@funcionario_bp.get("/usuarios/presenca")
@funcionario_required
def usuarios_presenca():
    ids_raw = (request.args.get("ids") or "").strip()
    ids = []
    for part in ids_raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))

    if not ids:
        return jsonify({"ok": True, "online": {}})

    now_utc = agora_utc()
    limite = now_utc - timedelta(minutes=ONLINE_WINDOW_MIN)

    rows = (
        bd.session.query(Usuario.id, Usuario.ultimo_ativo_em)
        .filter(Usuario.id.in_(ids))
        .all()
    )

    online_map = {}
    for uid, ultimo in rows:
        online_map[str(uid)] = bool(ultimo and ultimo >= limite)

    return jsonify({"ok": True, "online": online_map})

@funcionario_bp.get("/usuarios")
@funcionario_required
def usuarios():
    q = (request.args.get("q") or "").strip()
    email = (request.args.get("email") or "").strip()
    status = (request.args.get("status") or "").strip().lower()

    page = request.args.get("page", 1, type=int)
    per_page = 10

    now_utc = agora_utc()

    # stats globais (calculado ANTES para evitar autoflush caso altere attrs no loop)
    stats = _usuarios_stats(now_utc)

    # colunas tolerantes
    nome_cols = [
        getattr(Usuario, "nome_usuario", None),
        getattr(Usuario, "nome", None),
        getattr(Usuario, "nome_completo", None),
        getattr(Usuario, "username", None),
    ]
    nome_cols = [c for c in nome_cols if c is not None]
    email_col = getattr(Usuario, "email", None)

    # Subquery: count de reservas por usuário
    reservas_subq = (
        bd.session.query(
            Reserva.usuario_id.label("usuario_id"),
            func.count(Reserva.id).label("reservas_count")
        )
        .group_by(Reserva.usuario_id)
        .subquery()
    )

    # Query com reservas_count
    query = (
        Usuario.query
        .outerjoin(reservas_subq, reservas_subq.c.usuario_id == Usuario.id)
        .add_columns(reservas_subq.c.reservas_count)
    )

    # busca por nome/email
    if q:
        like = f"%{q}%"
        conds = []
        for c in nome_cols:
            conds.append(c.ilike(like))
        if email_col is not None:
            conds.append(email_col.ilike(like))
        if conds:
            query = query.filter(or_(*conds))

    if email and email_col is not None:
        query = query.filter(email_col.ilike(f"%{email}%"))

    # filtro por status (EFETIVO, sem contar dobrado)
    cond_bloq = _cond_bloqueado(now_utc)
    cond_inat = _cond_inativo()

    if status == "bloqueado" and cond_bloq is not None:
        if cond_inat is not None:
            query = query.filter(and_(~cond_inat, cond_bloq))
        else:
            query = query.filter(cond_bloq)

    elif status == "inativo" and cond_inat is not None:
        query = query.filter(cond_inat)

    elif status == "ativo":
        if cond_bloq is not None and cond_inat is not None:
            query = query.filter(and_(~cond_inat, ~cond_bloq))
        elif cond_inat is not None:
            query = query.filter(~cond_inat)
        elif cond_bloq is not None:
            query = query.filter(~cond_bloq)

    query = query.filter(func.lower(Usuario.status) != "apagado")
    query = query.order_by(Usuario.id.desc())

    pag = query.paginate(page=page, per_page=per_page, error_out=False)

    usuarios_list = []
    for u, reservas_count in pag.items:
        setattr(u, "reservas_count", int(reservas_count or 0))

        # status efetivo pra UI (inativo > bloqueado > ativo)
        st_key = _status_key_usuario_obj(u, now_utc)

        # MUITO IMPORTANTE:
        # Não pode fazer "u.status = st_key" normal quando status é coluna do banco,
        # porque isso pode sujar a sessão e atualizar sem querer (autoflush).
        # set_committed_value altera só em memória, sem marcar dirty.
        try:
            set_committed_value(u, "status", st_key)
        except Exception:
            # se não houver coluna status no model, cria atributo dinâmico para o template
            setattr(u, "status", st_key)

        usuarios_list.append(u)

    args_no_page = request.args.to_dict(flat=True)
    args_no_page.pop("page", None)

    pages = list(range(max(1, pag.page - 2), min(pag.pages, pag.page + 2) + 1))

    now = agora_br()
    return render_template(
        "funcionario/usuarios.html",
        usuarios=usuarios_list,
        pag=pag,
        pages=pages,
        args_no_page=args_no_page,
        stats=stats,
        data_hoje=now.strftime("%d/%m/%Y"),
        hora_agora=now.strftime("%H:%M"),
    )


# =========================
# USUÁRIOS - AÇÕES (modal)
# =========================
@funcionario_bp.get("/usuarios/<int:usuario_id>")
@funcionario_required
def usuario_detalhe(usuario_id):
    # detalhe simples: manda direto para reservas filtradas do usuário
    return redirect(url_for("funcionario.reservas", usuario_id=usuario_id))


@funcionario_bp.post("/usuarios/<int:usuario_id>/toggle-bloqueio")
@funcionario_required
def usuario_toggle_bloqueio(usuario_id):
    u = Usuario.query.get_or_404(usuario_id)
    now_utc = agora_utc()

    atual = _status_key_usuario_obj(u, now_utc)
    novo = "ativo" if atual == "bloqueado" else "bloqueado"

    try:
        _set_status_usuario(u, novo, now_utc)
        bd.session.commit()
    except Exception as e:
        bd.session.rollback()
        if _is_xhr():
            return jsonify({"ok": False, "msg": str(e)}), 400
        flash("Não foi possível alterar o status.", "error")
        return redirect(url_for("funcionario.usuarios"))

    if _is_xhr():
        now2 = agora_utc()
        st_key = _status_key_usuario_obj(u, now2)
        return jsonify({
            "ok": True,
            "user": {"id": u.id, "status_key": st_key},
            "stats": _usuarios_stats(now2)
        })

    flash("Status atualizado.", "success")
    return redirect(url_for("funcionario.usuarios"))


@funcionario_bp.post("/usuarios/<int:usuario_id>/apagar")
@funcionario_required
def usuario_apagar(usuario_id):
    u = Usuario.query.get_or_404(usuario_id)

    # se já estiver apagado, não faz nada
    if (u.status or "").strip().lower() == "apagado":
        if _is_xhr():
            now_utc = agora_utc()
            return jsonify({
                "ok": True,
                "msg": "Usuário já estava apagado.",
                "stats": _usuarios_stats(now_utc)
            })
        flash("Usuário já estava apagado.", "info")
        return redirect(url_for("funcionario.usuarios"))

    # NOVO BLOQUEIO
    if _usuario_tem_reserva_bloqueante(u.id):
        msg = "Não é possível apagar este usuário porque ele possui reserva/empréstimo ativo."

        if _is_xhr():
            now_utc = agora_utc()
            return jsonify({
                "ok": False,
                "msg": msg,
                "stats": _usuarios_stats(now_utc)
            }), 400

        flash(msg, "error")
        return redirect(url_for("funcionario.usuarios"))

    try:
        func_id = session.get("funcionario_id")
        _soft_delete_usuario(u, funcionario_id=func_id)

        bd.session.commit()

    except Exception as e:
        bd.session.rollback()
        if _is_xhr():
            return jsonify({"ok": False, "msg": f"Não foi possível apagar o usuário: {e}"}), 400
        flash("Não foi possível apagar o usuário.", "error")
        return redirect(url_for("funcionario.usuarios"))

    if _is_xhr():
        now_utc = agora_utc()
        return jsonify({
            "ok": True,
            "msg": "Usuário apagado (modo seguro).",
            "stats": _usuarios_stats(now_utc),
        })

    flash("Usuário apagado (modo seguro).", "success")
    return redirect(url_for("funcionario.usuarios"))


@funcionario_bp.post("/usuarios/<int:usuario_id>/ativar")
@funcionario_required
def usuario_ativar(usuario_id):
    u = Usuario.query.get_or_404(usuario_id)
    now_utc = agora_utc()

    try:
        _set_status_usuario(u, "ativo", now_utc)
        bd.session.commit()
    except Exception as e:
        bd.session.rollback()
        if _is_xhr():
            return jsonify({"ok": False, "msg": str(e)}), 400
        flash("Não foi possível ativar o usuário.", "error")
        return redirect(url_for("funcionario.usuarios"))

    if _is_xhr():
        now2 = agora_utc()
        return jsonify({
            "ok": True,
            "user": {"id": u.id, "status_key": "ativo"},
            "stats": _usuarios_stats(now2)
        })

    flash("Usuário ativado.", "success")
    return redirect(url_for("funcionario.usuarios"))


# =========================
# RESERVAS - LISTAGEM (com filtro usuario_id)
# =========================
@funcionario_bp.get("/reservas")
@funcionario_required
def reservas():
    # filtros
    q_text = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip().lower()
    tipo = (request.args.get("tipo") or "").strip().lower()

    # filtro de prazo
    prazo = (request.args.get("prazo") or "").strip().lower()

    # filtrar reservas por usuário (coluna "Reservas" em usuarios.html)
    usuario_id = request.args.get("usuario_id", type=int)

    # datas (YYYY-MM-DD) -> digitadas pelo usuário em horário BR
    de = (request.args.get("de") or "").strip()
    ate = (request.args.get("ate") or "").strip()

    # paginação
    page = request.args.get("page", 1, type=int)
    per_page = 10

    now_br = agora_br()
    hoje_br = now_br.date()
    now_utc = agora_utc()  # DATETIME UTC naive (compatível com MySQL)

    q = (
        Reserva.query
        .options(
            selectinload(Reserva.usuario),
            selectinload(Reserva.exemplar).selectinload(Exemplar.livro),
        )
        .order_by(Reserva.id.desc())
    )

    if usuario_id:
        q = q.filter(Reserva.usuario_id == usuario_id)

    # filtro status
    if status:
        q = q.filter(Reserva.status == status)

    # filtro tipo
    if tipo:
        q = q.filter(Reserva.tipo == tipo)

    # busca (usuário, email, título, autor, tombo/código exemplar)
    if q_text:
        like = f"%{q_text}%"

        # colunas tolerantes para usuário
        u_nome = (
            getattr(Usuario, "nome_usuario", None)
            or getattr(Usuario, "nome", None)
            or getattr(Usuario, "nome_completo", None)
            or getattr(Usuario, "username", None)
        )
        u_email = getattr(Usuario, "email", None)

        conds = []
        if u_nome is not None:
            conds.append(u_nome.ilike(like))
        if u_email is not None:
            conds.append(u_email.ilike(like))

        q = (
            q.join(Usuario, Usuario.id == Reserva.usuario_id)
             .join(Exemplar, Exemplar.id == Reserva.exemplar_id)
             .join(Livro, Livro.id == Exemplar.livro_id)
             .filter(or_(
                 *conds,
                 Exemplar.codigo_exemplar.ilike(like),
                 Livro.titulo.ilike(like),
                 Livro.autor.ilike(like),
             ))
        )

    # filtro por data (criado_em)
    def _parse_date(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    de_date = _parse_date(de) if de else None
    ate_date = _parse_date(ate) if ate else None

    if de_date:
        de_br = datetime.combine(de_date, time.min)   # BR 00:00:00
        de_utc = br_naive_para_utc_naive(de_br)       # converte para UTC
        q = q.filter(Reserva.criado_em >= de_utc)

    if ate_date:
        ate_br = datetime.combine(ate_date, time.max) # BR 23:59:59.999999
        ate_utc = br_naive_para_utc_naive(ate_br)     # converte para UTC
        q = q.filter(Reserva.criado_em <= ate_utc)

    # =========================
    # FILTRO DE PRAZO
    # =========================
    if prazo == "atrasado":
        q = q.filter(
            Reserva.status.in_(["retirado", "enviado"]),
            Reserva.devolver_ate.isnot(None),
            Reserva.devolver_ate < hoje_br
        )

    elif prazo == "vence_hoje":
        q = q.filter(
            Reserva.status.in_(["retirado", "enviado"]),
            Reserva.devolver_ate.isnot(None),
            Reserva.devolver_ate == hoje_br
        )

    elif prazo == "no_prazo":
        q = q.filter(
            Reserva.status.in_(["retirado", "enviado"]),
            Reserva.devolver_ate.isnot(None),
            Reserva.devolver_ate >= hoje_br
        )

    elif prazo == "retirada_expirada":
        q = q.filter(
            Reserva.status == "separado",
            Reserva.retirar_ate.isnot(None),
            Reserva.retirar_ate < now_utc
        )

    elif prazo == "qualquer_atraso":
        q = q.filter(
            or_(
                and_(
                    Reserva.status.in_(["retirado", "enviado"]),
                    Reserva.devolver_ate.isnot(None),
                    Reserva.devolver_ate < hoje_br
                ),
                and_(
                    Reserva.status == "separado",
                    Reserva.retirar_ate.isnot(None),
                    Reserva.retirar_ate < now_utc
                )
            )
        )

    pag = q.paginate(page=page, per_page=per_page, error_out=False)

    # CONVERSÃO PARA EXIBIÇÃO (UTC -> BR) + FLAGS DE PRAZO
    for r in pag.items:
        r.criado_em_br = utc_naive_para_br(r.criado_em) if getattr(r, "criado_em", None) else None
        r.retirar_ate_br = utc_naive_para_br(r.retirar_ate) if getattr(r, "retirar_ate", None) else None
        r.emprestado_em_br = utc_naive_para_br(r.emprestado_em) if getattr(r, "emprestado_em", None) else None
        r.devolvido_em_br = utc_naive_para_br(r.devolvido_em) if getattr(r, "devolvido_em", None) else None

        r.ui_prazo_label = None
        r.ui_prazo_class = None

        st = (r.status or "").strip().lower()

        if st in ("retirado", "enviado") and r.devolver_ate:
            if r.devolver_ate < hoje_br:
                dias = (hoje_br - r.devolver_ate).days
                r.ui_prazo_label = f"Atraso: {dias} dia{'s' if dias != 1 else ''}"
                r.ui_prazo_class = "mini-flag--danger"
            elif r.devolver_ate == hoje_br:
                r.ui_prazo_label = "Vence hoje"
                r.ui_prazo_class = "mini-flag--warn"
            else:
                r.ui_prazo_label = "No prazo"
                r.ui_prazo_class = "mini-flag--ok"

        elif st == "separado" and r.retirar_ate and r.retirar_ate < now_utc:
            r.ui_prazo_label = "Retirada expirada"
            r.ui_prazo_class = "mini-flag--danger"

    args_no_page = request.args.to_dict(flat=True)
    args_no_page.pop("page", None)

    pages = list(range(max(1, pag.page - 2), min(pag.pages, pag.page + 2) + 1))

    return render_template(
        "funcionario/reservas.html",
        reservas=pag.items,
        pag=pag,
        pages=pages,
        args_no_page=args_no_page,
        data_hoje=now_br.strftime("%d/%m/%Y"),
        hora_agora=now_br.strftime("%H:%M"),
    )


# =========================
# RESERVAS - MUDAR STATUS
# =========================
@funcionario_bp.post("/reservas/<int:reserva_id>/status")
@funcionario_required
def reserva_mudar_status(reserva_id):
    r = Reserva.query.get_or_404(reserva_id)
    garantir_snapshot_politica_reserva(r)

    novo_status = (request.form.get("status") or "").strip().lower()

    permitidos = {"pendente", "aprovada", "separado", "enviado", "retirado", "devolvido", "cancelado"}
    if novo_status not in permitidos:
        flash("Status inválido.", "error")
        return redirect(url_for("funcionario.reservas"))

    cancelar_retiradas_expiradas()

    ex = Exemplar.query.get(r.exemplar_id)
    usuario = Usuario.query.get(r.usuario_id)

    now = agora_utc()

    if novo_status == "cancelado":
        if r.status in ("devolvido", "retirado", "enviado", "cancelado"):
            flash("Não é possível cancelar uma reserva já devolvida, retirada, enviada ou cancelada.", "error")
            return redirect(url_for("funcionario.reservas"))

        r.status = "cancelado"

        if ex and (ex.status or "").strip().lower() == "reservado":
            ex.status = "disponivel"

        bd.session.commit()
        flash("Reserva cancelada.", "success")
        return redirect(url_for("funcionario.reservas"))

    if novo_status == "aprovada":
        if r.status != "pendente":
            flash("Só é possível aprovar reservas pendentes.", "error")
            return redirect(url_for("funcionario.reservas"))
        r.status = "aprovada"
        bd.session.commit()
        flash("Reserva aprovada.", "success")
        return redirect(url_for("funcionario.reservas"))

    if novo_status == "separado":
        if r.tipo != "retirada":
            flash("Somente retirada pode ficar como 'separado'.", "error")
            return redirect(url_for("funcionario.reservas"))
        if r.status not in ("aprovada",):
            flash("Para separar, a reserva precisa estar aprovada.", "error")
            return redirect(url_for("funcionario.reservas"))

        prazo_retirada_horas = int(r.prazo_retirada_horas_aplicado or 48)

        r.status = "separado"
        if r.retirar_ate is None:
            r.retirar_ate = now + timedelta(hours=prazo_retirada_horas)

        bd.session.commit()
        flash(f"Exemplar separado. Usuário tem {prazo_retirada_horas}h para retirar.", "success")
        return redirect(url_for("funcionario.reservas"))

    if novo_status == "enviado":
        if r.tipo != "entrega":
            flash("Somente entrega pode ficar como 'enviado'.", "error")
            return redirect(url_for("funcionario.reservas"))

        if r.status != "aprovada":
            flash("Para enviar, a reserva precisa estar aprovada.", "error")
            return redirect(url_for("funcionario.reservas"))

        prazo_devolucao_dias = int(r.prazo_devolucao_dias_aplicado or 14)

        r.status = "enviado"
        if r.emprestado_em is None:
            r.emprestado_em = now
        if r.devolver_ate is None:
            r.devolver_ate = (now + timedelta(days=prazo_devolucao_dias)).date()

        if ex:
            ex.status = "emprestado"

        rastreio = (request.form.get("codigo_rastreio") or "").strip()
        if rastreio:
            r.codigo_rastreio = rastreio

        bd.session.commit()
        flash(
            f"Reserva marcada como enviada (empréstimo iniciado por {prazo_devolucao_dias} dia(s)).",
            "success"
        )
        return redirect(url_for("funcionario.reservas"))

    if novo_status == "retirado":
        if r.tipo != "retirada":
            flash("Somente retirada pode ficar como 'retirado'.", "error")
            return redirect(url_for("funcionario.reservas"))

        if r.status != "separado":
            flash("Para retirar, a reserva precisa estar como 'separado'.", "error")
            return redirect(url_for("funcionario.reservas"))

        if r.retirar_ate and r.retirar_ate < now:
            r.status = "cancelado"
            if ex and (ex.status or "").strip().lower() == "reservado":
                ex.status = "disponivel"
            bd.session.commit()
            flash("Prazo de retirada expirou. Reserva cancelada automaticamente.", "error")
            return redirect(url_for("funcionario.reservas"))

        prazo_devolucao_dias = int(r.prazo_devolucao_dias_aplicado or 14)

        r.status = "retirado"
        r.emprestado_em = now
        r.devolver_ate = (now + timedelta(days=prazo_devolucao_dias)).date()

        if ex:
            ex.status = "emprestado"

        bd.session.commit()
        flash(
            f"Reserva marcada como retirada. Prazo de devolução iniciado por {prazo_devolucao_dias} dia(s).",
            "success"
        )
        return redirect(url_for("funcionario.reservas"))

    if novo_status == "devolvido":
        if r.status not in ("retirado", "enviado"):
            flash("Só é possível devolver quando o exemplar já foi entregue/retirado.", "error")
            return redirect(url_for("funcionario.reservas"))

        r.status = "devolvido"
        r.devolvido_em = now

        if ex:
            ex.status = "disponivel"

        dias_bloqueio = 0
        if usuario and r.devolver_ate:
            dias_atraso = (now.date() - r.devolver_ate).days
            if dias_atraso > 0:
                dias_bloqueio = aplicar_bloqueio_por_atraso_configuravel(usuario, r, dias_atraso)

        bd.session.commit()

        if dias_bloqueio > 0:
            flash(
                f"Reserva marcada como devolvida. Bloqueio por atraso aplicado por {dias_bloqueio} dia(s).",
                "success"
            )
        else:
            flash("Reserva marcada como devolvida.", "success")

        return redirect(url_for("funcionario.reservas"))

    flash("Transição de status não implementada.", "error")
    return redirect(url_for("funcionario.reservas"))


@funcionario_bp.get("/usuarios/<int:usuario_id>/detalhes")
@funcionario_required
def usuario_detalhes(usuario_id: int):
    now_utc = agora_utc()
    u = Usuario.query.get_or_404(usuario_id)

    endereco = {
        "cep": getattr(u, "cep", None),
        "rua": getattr(u, "rua", None),
        "numero": getattr(u, "numero", None),
        "bairro": getattr(u, "bairro", None),
        "cidade": getattr(u, "cidade", None),
        "uf": getattr(u, "uf", None),
        "complemento": getattr(u, "complemento", None),
    }

    reservas = (
        Reserva.query
        .options(
            selectinload(Reserva.exemplar).selectinload(Exemplar.livro)
        )
        .filter(Reserva.usuario_id == usuario_id)
        .order_by(desc(Reserva.criado_em))
        .limit(300)
        .all()
    )

    atuais = []
    historico = []

    for r in reservas:
        livro = None
        if getattr(r, "exemplar", None) is not None:
            livro = getattr(r.exemplar, "livro", None)

        item = {
            "reserva_id": r.id,
            "status": getattr(r, "status", None),
            "tipo": getattr(r, "tipo", None),

            "livro_id": getattr(livro, "id", None) if livro else None,
            "livro_titulo": getattr(livro, "titulo", None) if livro else f"Livro (exemplar {r.exemplar_id})",
            "capa_url": _get_livro_capa_url(livro),
            "livro_url": None,

            "criado_em": _dt_br_str(getattr(r, "criado_em", None)),
            "retirar_ate": _dt_br_str(getattr(r, "retirar_ate", None)),
            "emprestado_em": _dt_br_str(getattr(r, "emprestado_em", None)),
            "devolver_ate": _dt_br_str(getattr(r, "devolver_ate", None)),
            "devolvido_em": _dt_br_str(getattr(r, "devolvido_em", None)),
        }

        if livro and getattr(livro, "ativo", True):
            try:
                item["livro_url"] = url_for("main.livro_detalhe", id=livro.id)
            except Exception:
                item["livro_url"] = None
        else:
            item["livro_url"] = None

        status_reserva = (getattr(r, "status", "") or "").strip().lower()
        devolvido_em = getattr(r, "devolvido_em", None)

        STATUS_EM_POSSE = {
            "retirado",
            "enviado",
            "emprestado",
            "atrasado",
        }

        if status_reserva in STATUS_EM_POSSE and not devolvido_em:
            atuais.append(item)
        else:
            historico.append(item)

    topicos = (
        ForumTopico.query
        .filter(ForumTopico.autor_id == usuario_id)
        .order_by(desc(ForumTopico.criado_em))
        .limit(100)
        .all()
    )

    comentarios = (
        ForumComentario.query
        .filter(ForumComentario.autor_id == usuario_id)
        .order_by(desc(ForumComentario.criado_em))
        .limit(200)
        .all()
    )

    def _trecho(txt: str, n: int = 160):
        s = (txt or "").strip()
        return (s[:n] + "…") if len(s) > n else s

    topicos_json = []
    for t in topicos:
        try:
            topico_url = url_for("forum.topico_detalhe", topico_id=t.id, slug=t.slug)
        except Exception:
            topico_url = None

        texto_topico = (
            getattr(t, "conteudo", None)
            or getattr(t, "texto", None)
            or getattr(t, "corpo", None)
            or ""
        )

        topicos_json.append({
            "id": t.id,
            "titulo": t.titulo,
            "criado_em": _dt_br_str(getattr(t, "criado_em", None)),
            "upvotes": getattr(t, "upvotes_count", 0),
            "oculto": bool(getattr(t, "oculto", False)),
            "trancado": bool(getattr(t, "trancado", False)),
            "url": topico_url,
            "trecho": _trecho(texto_topico, 180),
        })

    comentarios_json = []
    for c in comentarios:
        topico_titulo = None
        topico_url = None

        try:
            if hasattr(c, "topico") and c.topico:
                topico_titulo = c.topico.titulo
                topico_url = url_for("forum.topico_detalhe", topico_id=c.topico.id, slug=c.topico.slug)
        except Exception:
            pass

        texto = getattr(c, "conteudo", "") or getattr(c, "texto", "") or ""
        comentarios_json.append({
            "id": c.id,
            "criado_em": _dt_br_str(getattr(c, "criado_em", None)),
            "trecho": _trecho(texto, 180),
            "upvotes": getattr(c, "upvotes_count", 0),
            "topico_titulo": topico_titulo,
            "topico_url": topico_url,
        })

    payload = {
        "ok": True,
        "usuario": {
            "id": u.id,
            "nome": _nome_exibicao(u),
            "email": getattr(u, "email", None) or "",
            "status_conta": getattr(u, "status", None) or "ativo",
            "bloqueado": bool(u.esta_bloqueado()) if hasattr(u, "esta_bloqueado") else False,
            "online": _usuario_online(u, now_utc=now_utc),
            "ultimo_ativo_em": _dt_br_str(getattr(u, "ultimo_ativo_em", None)),
            "avatar_url": _get_avatar_url(u),
            "criado_em": _dt_br_str(getattr(u, "criado_em", None)),
        },
        "endereco": endereco,
        "livros": {
            "atuais": atuais[:80],
            "historico": historico[:200],
        },
        "forum": {
            "topicos": topicos_json,
            "comentarios": comentarios_json,
        }
    }

    return jsonify(payload)