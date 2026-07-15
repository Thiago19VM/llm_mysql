import os
import re
import hashlib
import json

from . import main_bp
from datetime import timedelta
from uuid import uuid4

from flask import render_template, request, redirect, url_for, flash, session, current_app, jsonify
from sqlalchemy import or_
from sqlalchemy.exc import DataError
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app import bd
from app.models import Usuario
from app.models.livro import Livro
from app.models.exemplar import Exemplar
from app.models.reserva import Reserva
from app.services.politicas_emprestimo import (
    obter_politica_ativa,
    usuario_atingiu_limite_reservas,
    congelar_politica_na_reserva,
    garantir_snapshot_politica_reserva,
)
from app.utils.datetime_utils import agora_utc, agora_br, utc_naive_para_br


ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp"}

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


@main_bp.app_template_filter("genero_label")
def genero_label_filter(valor):
    return _label_genero(valor)


def _somente_digitos(valor: str) -> str:
    return re.sub(r"\D+", "", (valor or "").strip())


def usuario_ja_tem_reserva_ativa_do_livro(usuario_id: int, livro_id: int) -> bool:
    """
    Verifica se o usuário já possui uma reserva ATIVA desse mesmo livro
    (independente do exemplar/cópia).
    """
    status_ativos = ("pendente", "aprovada", "separado", "enviado", "retirado")

    existe = (
        Reserva.query
        .join(Exemplar, Reserva.exemplar_id == Exemplar.id)
        .filter(
            Reserva.usuario_id == usuario_id,
            Exemplar.livro_id == livro_id,
            Reserva.status.in_(status_ativos)
        )
        .first()
        is not None
    )
    return existe


STATUS_EMPRESTIMO_ATIVO = ("retirado", "enviado")


def obter_resumo_atraso_ativo_usuario(usuario_id: int):
    """
    Verifica se o usuário possui livro em atraso ainda não devolvido.
    Enquanto existir atraso ativo, ele não pode reservar novos livros.
    """
    hoje = agora_br().date()

    reservas_atrasadas = (
        Reserva.query
        .filter(
            Reserva.usuario_id == usuario_id,
            Reserva.status.in_(STATUS_EMPRESTIMO_ATIVO),
            Reserva.devolvido_em.is_(None),
            Reserva.devolver_ate.isnot(None),
            Reserva.devolver_ate < hoje,
        )
        .all()
    )

    if not reservas_atrasadas:
        return {
            "tem_atraso": False,
            "total": 0,
            "maior_atraso_dias": 0,
        }

    maior_atraso = max(
        (hoje - r.devolver_ate).days
        for r in reservas_atrasadas
    )

    return {
        "tem_atraso": True,
        "total": len(reservas_atrasadas),
        "maior_atraso_dias": maior_atraso,
    }


def mensagem_atraso_ativo(resumo: dict) -> str:
    """Monta a mensagem exibida quando o usuário possui empréstimo ativo em atraso."""
    dias = int(resumo.get("maior_atraso_dias", 0) or 0)
    total = int(resumo.get("total", 0) or 0)

    return (
        f"Você possui {total} empréstimo(s) em atraso. "
        f"O maior atraso está em {dias} dia{'s' if dias != 1 else ''}. "
        "Devolva o(s) livro(s) atrasado(s) para poder realizar novas reservas."
    )


def preparar_prazos_minhas_reservas(reservas):
    """
    Adiciona atributos auxiliares para exibir a situação do prazo na tela Minhas Reservas.
    Não altera o banco; apenas prepara dados para o template.
    """
    hoje = agora_br().date()

    for r in reservas:
        r.ui_prazo_label = None
        r.ui_prazo_class = None

        status = (r.status or "").strip().lower()

        if status in STATUS_EMPRESTIMO_ATIVO and r.devolver_ate:
            if r.devolver_ate < hoje:
                dias = (hoje - r.devolver_ate).days
                r.ui_prazo_label = f"Atrasado há {dias} dia{'s' if dias != 1 else ''}"
                r.ui_prazo_class = "mini-flag--danger"
            elif r.devolver_ate == hoje:
                r.ui_prazo_label = "Devolve hoje"
                r.ui_prazo_class = "mini-flag--warn"
            else:
                r.ui_prazo_label = "No prazo"
                r.ui_prazo_class = "mini-flag--ok"


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTS


def login_required():
    if not session.get("usuario_id"):
        flash("Faça login para acessar seu perfil.", "warning")
        return False
    return True


def exemplar_disponivel(exemplar) -> bool:
    if not getattr(exemplar, "ativo", True):
        return False
    return (exemplar.status or "").strip().lower() == "disponivel"


def calcular_estoque(livro):
    exemplares = [ex for ex in (livro.exemplares or []) if getattr(ex, "ativo", True)]
    total = len(exemplares)
    disponiveis = sum(1 for ex in exemplares if exemplar_disponivel(ex))
    return total, disponiveis


def buscar_livros_parecidos(livro, limite=6):
    """
    Monta uma lista de livros parecidos usando:
    1) mesmo gênero
    2) mesmo autor
    3) fallback com outros livros ativos
    """
    livros_parecidos = []
    ids_adicionados = {livro.id}

    def adicionar_unicos(lista):
        for item in lista:
            if item.id not in ids_adicionados:
                livros_parecidos.append(item)
                ids_adicionados.add(item.id)

            if len(livros_parecidos) >= limite:
                break

    if livro.genero:
        mesmos_genero = (
            Livro.query
            .filter(
                Livro.ativo.is_(True),
                Livro.id != livro.id,
                Livro.genero == livro.genero
            )
            .order_by(Livro.id.desc())
            .limit(limite)
            .all()
        )
        adicionar_unicos(mesmos_genero)

    if len(livros_parecidos) < limite and livro.autor:
        mesmos_autor = (
            Livro.query
            .filter(
                Livro.ativo.is_(True),
                Livro.id != livro.id,
                Livro.autor == livro.autor
            )
            .order_by(Livro.id.desc())
            .limit(limite)
            .all()
        )
        adicionar_unicos(mesmos_autor)

    if len(livros_parecidos) < limite:
        outros = (
            Livro.query
            .filter(
                Livro.ativo.is_(True),
                Livro.id != livro.id
            )
            .order_by(Livro.id.desc())
            .limit(12)
            .all()
        )
        adicionar_unicos(outros)

    return livros_parecidos


def cancelar_retiradas_expiradas():
    """Cancela automaticamente reservas 'separado' (retirada) cujo retirar_ate expirou."""
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
        return

    for r in expiradas:
        r.status = "cancelado"

        ex = Exemplar.query.get(r.exemplar_id)
        if ex and (ex.status or "").strip().lower() == "reservado":
            ex.status = "disponivel"

    bd.session.commit()


def _aplicar_filtros_acervo(query, q, genero, ano, editora, tipo_capa):
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Livro.titulo.ilike(like),
                Livro.autor.ilike(like)
            )
        )

    if genero:
        query = query.filter(Livro.genero == genero)

    if ano.isdigit():
        query = query.filter(Livro.ano == int(ano))

    if editora:
        query = query.filter(Livro.editora == editora)

    if tipo_capa:
        query = query.filter(Livro.tipo_capa == tipo_capa)

    return query


def _aplicar_ordenacao_acervo(query, ordenar):
    if ordenar == "antigos":
        return query.order_by(Livro.id.asc())

    if ordenar == "titulo_asc":
        return query.order_by(Livro.titulo.asc(), Livro.id.asc())

    if ordenar == "titulo_desc":
        return query.order_by(Livro.titulo.desc(), Livro.id.desc())

    if ordenar == "ano_desc":
        return query.order_by(Livro.ano.desc(), Livro.id.desc())

    if ordenar == "ano_asc":
        return query.order_by(Livro.ano.asc(), Livro.id.asc())

    return query.order_by(Livro.id.desc())  # recentes


def _preparar_livros_acervo(lista_livros):
    for livro in lista_livros:
        livro.genero_label = _label_genero(livro.genero)


def _listar_generos_acervo():
    valores = (
        Livro.query
        .with_entities(Livro.genero)
        .filter(
            Livro.ativo.is_(True),
            Livro.genero.isnot(None),
            Livro.genero != ""
        )
        .distinct()
        .order_by(Livro.genero.asc())
        .all()
    )

    generos = []
    vistos = set()

    for (genero_raw,) in valores:
        label = _label_genero(genero_raw)
        chave = (genero_raw or "").strip()
        if chave and chave not in vistos:
            generos.append(chave)
            vistos.add(chave)

    return generos


def _listar_anos_acervo():
    return [
        a[0] for a in (
            Livro.query
            .with_entities(Livro.ano)
            .filter(
                Livro.ativo.is_(True),
                Livro.ano.isnot(None)
            )
            .distinct()
            .order_by(Livro.ano.desc())
            .all()
        )
    ]


def _listar_editoras_acervo():
    return [
        e[0] for e in (
            Livro.query
            .with_entities(Livro.editora)
            .filter(
                Livro.ativo.is_(True),
                Livro.editora.isnot(None),
                Livro.editora != ""
            )
            .distinct()
            .order_by(Livro.editora.asc())
            .all()
        )
    ]


def _listar_tipos_capa_acervo():
    return [
        tc[0] for tc in (
            Livro.query
            .with_entities(Livro.tipo_capa)
            .filter(
                Livro.ativo.is_(True),
                Livro.tipo_capa.isnot(None),
                Livro.tipo_capa != ""
            )
            .distinct()
            .order_by(Livro.tipo_capa.asc())
            .all()
        )
    ]

def mensagem_atraso_ativo(resumo: dict) -> str:
    total = resumo.get("total", 0)

    return (
        f"Você possui {total} reserva{'s' if total != 1 else ''} em atraso. "
        "Só será possível alugar novos livros depois de regularizar suas pendências com a biblioteca."
    )


@main_bp.get("/")
def home():
    total_livros = Livro.query.filter(Livro.ativo.is_(True)).count()

    total_exemplares = (
        Exemplar.query
        .join(Livro, Livro.id == Exemplar.livro_id)
        .filter(Livro.ativo.is_(True))
        .count()
    )

    disponiveis = (
        Exemplar.query
        .join(Livro, Livro.id == Exemplar.livro_id)
        .filter(Livro.ativo.is_(True), Exemplar.status == "disponivel")
        .count()
    )

    featured_books = (
        Livro.query
        .filter(Livro.ativo.is_(True))
        .order_by(Livro.id.desc())
        .limit(8)
        .all()
    )

    forum_topics = [
        {"title": "Recomendações de ficção científica para iniciantes", "author": "Pedro R.", "replies": 45, "genre": "Ficção Científica", "timeAgo": "4h atrás"},
        {"title": "Clube de leitura — livro do mês", "author": "Ana C.", "replies": 67, "genre": "Clássico", "timeAgo": "6h atrás"},
        {"title": "Melhores thrillers brasileiros", "author": "Lucas M.", "replies": 31, "genre": "Thriller", "timeAgo": "8h atrás"},
    ]

    stats = {
        "livros": total_livros,
        "leitores": 100,
        "entregas": 50,
    }

    return render_template(
        "home.html",
        featured_books=featured_books,
        stats=stats,
        forum_topics=forum_topics,
        total_exemplares=total_exemplares,
        disponiveis=disponiveis,
    )


@main_bp.get("/quem-somos")
def quem_somos():
    return render_template("quem_somos.html")


@main_bp.get("/forum")
def forum():
    return "Fórum (criar página depois)"


@main_bp.route("/perfil", methods=["GET", "POST"])
def perfil():
    if not login_required():
        return redirect(url_for("auth.login"))

    usuario = Usuario.query.get_or_404(session["usuario_id"])

    if not session.get("nome_usuario"):
        session["nome_usuario"] = (
            getattr(usuario, "nome_usuario", None)
            or getattr(usuario, "username", None)
            or getattr(usuario, "nome_completo", None)
            or ""
        )

    if getattr(usuario, "foto", None):
        session["usuario_foto"] = usuario.foto
    else:
        session.pop("usuario_foto", None)

    if request.method == "POST":
        was_completo = usuario.perfil_completo_para_reserva()

        telefone = _somente_digitos(request.form.get("telefone"))
        cep = _somente_digitos(request.form.get("cep"))
        rua = (request.form.get("rua") or "").strip()
        numero = (request.form.get("numero") or "").strip()
        bairro = (request.form.get("bairro") or "").strip()
        cidade = (request.form.get("cidade") or "").strip()
        uf = (request.form.get("uf") or "").strip().upper()[:2]

        complemento = (request.form.get("complemento") or "").strip()
        bio = (request.form.get("bio") or "").strip()

        obrigatorios = {
            "Telefone": telefone,
            "CEP": cep,
            "Rua": rua,
            "Número": numero,
            "Bairro": bairro,
            "Cidade": cidade,
            "UF": uf,
        }

        erros = []

        if telefone and len(telefone) > 11:
            erros.append("O telefone deve conter no máximo 11 dígitos.")

        if cep and len(cep) != 8:
            erros.append("O CEP deve conter exatamente 8 dígitos.")

        if uf and len(uf) != 2:
            erros.append("A UF deve conter 2 letras.")

        if bio and len(bio) > 280:
            erros.append("A bio pode ter no máximo 280 caracteres.")

        if was_completo:
            faltando_agora = [label for label, val in obrigatorios.items() if not val]
            if faltando_agora:
                erros.append(
                    "Não é possível remover dados obrigatórios. Preencha: " + ", ".join(faltando_agora)
                )

        if erros:
            for erro in erros:
                flash(erro, "error")
            return redirect(url_for("main.perfil"))

        if was_completo:
            usuario.telefone = telefone
            usuario.cep = cep
            usuario.rua = rua
            usuario.numero = numero
            usuario.bairro = bairro
            usuario.cidade = cidade
            usuario.uf = uf
        else:
            if telefone:
                usuario.telefone = telefone
            if cep:
                usuario.cep = cep
            if rua:
                usuario.rua = rua
            if numero:
                usuario.numero = numero
            if bairro:
                usuario.bairro = bairro
            if cidade:
                usuario.cidade = cidade
            if uf:
                usuario.uf = uf

        usuario.complemento = complemento
        usuario.bio = bio

        file = request.files.get("foto")
        if file and file.filename:
            if not allowed_file(file.filename):
                flash("Formato inválido. Use PNG, JPG, JPEG ou WEBP.", "error")
                return redirect(url_for("main.perfil"))

            filename = secure_filename(file.filename)
            ext = filename.rsplit(".", 1)[1].lower()
            new_name = f"{uuid4().hex}.{ext}"

            folder = current_app.config["UPLOAD_FOLDER_PERFIS"]
            path = os.path.join(folder, new_name)
            file.save(path)

            usuario.foto = f"uploads/perfis/{new_name}"
            session["usuario_foto"] = usuario.foto

        try:
            bd.session.commit()
        except DataError:
            bd.session.rollback()
            flash("Algum campo excede o tamanho permitido. Revise telefone, CEP e endereço.", "error")
            return redirect(url_for("main.perfil"))

        if not session.get("nome_usuario"):
            session["nome_usuario"] = (
                getattr(usuario, "nome_usuario", None)
                or getattr(usuario, "username", None)
                or getattr(usuario, "nome_completo", None)
                or getattr(usuario, "nome", None)
                or ""
            )

        flash("Perfil atualizado com sucesso!", "success")
        return redirect(url_for("main.perfil"))

    faltando = usuario.campos_faltando_reserva()
    completo = usuario.perfil_completo_para_reserva()
    percent = int(round((1 - (len(faltando) / 7)) * 100)) if faltando else 100

    foto_url = None
    if getattr(usuario, "foto", None):
        foto_url = url_for("static", filename=usuario.foto)

    return render_template(
        "perfil.html",
        usuario=usuario,
        completo=completo,
        faltando=faltando,
        percent=percent,
        foto_url=foto_url,
    )


@main_bp.get("/acervo")
def acervo():
    q = (request.args.get("q") or "").strip()
    genero = (request.args.get("genero") or "").strip()
    ano = (request.args.get("ano") or "").strip()
    editora = (request.args.get("editora") or "").strip()
    tipo_capa = (request.args.get("tipo_capa") or "").strip()
    ordenar = (request.args.get("ordenar") or "recentes").strip().lower()

    page = request.args.get("page", 1, type=int)

    # per_page dinâmico vindo da URL
    per_page = request.args.get("per_page", type=int)
    if per_page not in (6, 8, 9, 12, 15):
        per_page = 12

    query = Livro.query.filter(Livro.ativo.is_(True))

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Livro.titulo.ilike(like),
                Livro.autor.ilike(like)
            )
        )

    if genero:
        query = query.filter(Livro.genero == genero)

    if ano.isdigit():
        query = query.filter(Livro.ano == int(ano))

    if editora:
        query = query.filter(Livro.editora == editora)

    if tipo_capa:
        query = query.filter(Livro.tipo_capa == tipo_capa)

    if ordenar == "antigos":
        query = query.order_by(Livro.id.asc())
    elif ordenar == "titulo_asc":
        query = query.order_by(Livro.titulo.asc(), Livro.id.asc())
    elif ordenar == "titulo_desc":
        query = query.order_by(Livro.titulo.desc(), Livro.id.desc())
    elif ordenar == "ano_desc":
        query = query.order_by(Livro.ano.desc(), Livro.id.desc())
    elif ordenar == "ano_asc":
        query = query.order_by(Livro.ano.asc(), Livro.id.asc())
    else:
        query = query.order_by(Livro.id.desc())

    pag = query.paginate(page=page, per_page=per_page, error_out=False)

    livros = pag.items
    total = pag.total

    for livro in livros:
        livro.genero_label = _label_genero(livro.genero)

    start = (pag.page - 1) * pag.per_page + 1 if total > 0 else 0
    end = (pag.page - 1) * pag.per_page + len(livros) if total > 0 else 0

    generos = [
        g[0] for g in (
            Livro.query
            .with_entities(Livro.genero)
            .filter(Livro.ativo.is_(True), Livro.genero.isnot(None), Livro.genero != "")
            .distinct()
            .order_by(Livro.genero.asc())
            .all()
        )
    ]

    anos = [
        a[0] for a in (
            Livro.query
            .with_entities(Livro.ano)
            .filter(Livro.ativo.is_(True), Livro.ano.isnot(None))
            .distinct()
            .order_by(Livro.ano.desc())
            .all()
        )
    ]

    editoras = [
        e[0] for e in (
            Livro.query
            .with_entities(Livro.editora)
            .filter(Livro.ativo.is_(True), Livro.editora.isnot(None), Livro.editora != "")
            .distinct()
            .order_by(Livro.editora.asc())
            .all()
        )
    ]

    tipos_capa = [
        tc[0] for tc in (
            Livro.query
            .with_entities(Livro.tipo_capa)
            .filter(Livro.ativo.is_(True), Livro.tipo_capa.isnot(None), Livro.tipo_capa != "")
            .distinct()
            .order_by(Livro.tipo_capa.asc())
            .all()
        )
    ]

    args_no_page = request.args.to_dict(flat=True)
    args_no_page.pop("page", None)

    pages = list(range(max(1, pag.page - 2), min(pag.pages, pag.page + 2) + 1))

    return render_template(
        "acervo.html",
        livros=livros,
        pag=pag,
        pages=pages,
        args_no_page=args_no_page,
        total=total,
        start=start,
        end=end,
        generos=generos,
        anos=anos,
        editoras=editoras,
        tipos_capa=tipos_capa,
    )


def _exemplar_disponivel(ex):
    """
    Tenta descobrir se o exemplar está disponível, sem depender 100% do seu model Exemplar.
    Ajuste se você já tem um padrão fixo.
    """
    status = getattr(ex, "status", None)
    if isinstance(status, str):
        return status.strip().lower() in ("disponivel", "disponível", "livre", "available")

    disp = getattr(ex, "disponivel", None)
    if isinstance(disp, bool):
        return disp

    return True

def gerar_assinatura_reservas(reservas):
    """
    Gera uma assinatura baseada nos dados importantes das reservas.
    Quando algo muda, a assinatura muda.
    """
    hoje = agora_br().date()
    payload = []

    for r in reservas:
        ex = getattr(r, "exemplar", None)
        livro = getattr(ex, "livro", None) if ex else None

        st = (r.status or "").strip().lower()
        dias_atraso = 0

        if st in ("retirado", "enviado") and r.devolver_ate and r.devolver_ate < hoje:
            dias_atraso = (hoje - r.devolver_ate).days

        payload.append({
            "id": r.id,
            "status": r.status,
            "tipo": r.tipo,
            "retirar_ate": r.retirar_ate.isoformat() if r.retirar_ate else None,
            "emprestado_em": r.emprestado_em.isoformat() if r.emprestado_em else None,
            "devolver_ate": r.devolver_ate.isoformat() if r.devolver_ate else None,
            "devolvido_em": r.devolvido_em.isoformat() if r.devolvido_em else None,
            "renovacoes": r.renovacoes,
            "max_renovacoes_aplicado": r.max_renovacoes_aplicado,
            "exemplar_status": ex.status if ex else None,
            "codigo_exemplar": ex.codigo_exemplar if ex else None,
            "livro_id": livro.id if livro else None,
            "livro_titulo": livro.titulo if livro else None,
            "dias_atraso": dias_atraso,
        })

    bruto = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()

@main_bp.get("/livro/<int:livro_id>")
def livro_detalhe(livro_id):
    livro = (
        Livro.query
        .options(selectinload(Livro.exemplares))
        .filter(
            Livro.id == livro_id,
            Livro.ativo.is_(True)
        )
        .first_or_404()
    )

    estoque_total, estoque_disponivel = calcular_estoque(livro)

    usuario = None
    ja_tem_reserva_ativa = False
    limite_reservas_atingido = False
    tem_atraso_ativo = False
    pode_reservar = False
    motivo_bloqueio_reserva = None

    politica_ativa = obter_politica_ativa()

    if session.get("usuario_id"):
        usuario = Usuario.query.get(session["usuario_id"])

        if usuario:
            ja_tem_reserva_ativa = usuario_ja_tem_reserva_ativa_do_livro(usuario.id, livro_id)
            limite_reservas_atingido = usuario_atingiu_limite_reservas(usuario.id, politica_ativa)

            resumo_atraso = obter_resumo_atraso_ativo_usuario(usuario.id)
            tem_atraso_ativo = resumo_atraso["tem_atraso"]

            if usuario.esta_bloqueado():
                motivo_bloqueio_reserva = usuario.motivo_bloqueio_msg()
            elif tem_atraso_ativo:
                motivo_bloqueio_reserva = mensagem_atraso_ativo(resumo_atraso)
            elif not usuario.perfil_completo_para_reserva():
                motivo_bloqueio_reserva = "Complete seu perfil para liberar as reservas."
            elif ja_tem_reserva_ativa:
                motivo_bloqueio_reserva = "Você já possui uma reserva ativa deste livro."
            elif limite_reservas_atingido:
                motivo_bloqueio_reserva = (
                    f"Você atingiu o limite de {politica_ativa.limite_reservas_ativas} reserva(s) ativa(s)."
                )
            elif estoque_disponivel <= 0:
                motivo_bloqueio_reserva = "Sem exemplares disponíveis no momento."

            pode_reservar = (
                (not usuario.esta_bloqueado())
                and (not tem_atraso_ativo)
                and usuario.perfil_completo_para_reserva()
                and (estoque_disponivel > 0)
                and (not ja_tem_reserva_ativa)
                and (not limite_reservas_atingido)
            )

    livros_parecidos = buscar_livros_parecidos(livro, limite=6)

    return render_template(
        "livro_detalhe.html",
        livro=livro,
        estoque_total=estoque_total,
        estoque_disponivel=estoque_disponivel,
        pode_reservar=pode_reservar,
        ja_tem_reserva_ativa=ja_tem_reserva_ativa,
        limite_reservas_atingido=limite_reservas_atingido,
        tem_atraso_ativo=tem_atraso_ativo,
        motivo_bloqueio_reserva=motivo_bloqueio_reserva,
        limite_reservas_ativas=politica_ativa.limite_reservas_ativas,
        livros_parecidos=livros_parecidos,
    )


@main_bp.post("/livro/<int:livro_id>/reservar")
def reservar_checkout(livro_id):
    if not login_required():
        return redirect(url_for("auth.login"))

    cancelar_retiradas_expiradas()

    usuario = Usuario.query.get_or_404(session["usuario_id"])
    politica = obter_politica_ativa()

    if usuario.esta_bloqueado():
        flash(usuario.motivo_bloqueio_msg(), "error")
        return redirect(url_for("main.minhas_reservas"))


    resumo_atraso = obter_resumo_atraso_ativo_usuario(usuario.id)
    if resumo_atraso["tem_atraso"]:
        flash(mensagem_atraso_ativo(resumo_atraso), "error")
        return redirect(url_for("main.minhas_reservas"))

    if not usuario.perfil_completo_para_reserva():
        flash("Complete seu perfil para liberar as reservas.", "error")
        return redirect(url_for("main.perfil"))

    if usuario_ja_tem_reserva_ativa_do_livro(usuario.id, livro_id):
        flash("Você já possui uma reserva ativa deste livro.", "error")
        return redirect(url_for("main.livro_detalhe", livro_id=livro_id))

    if usuario_atingiu_limite_reservas(usuario.id, politica):
        flash(
            f"Você atingiu o limite de {politica.limite_reservas_ativas} reserva(s) ativa(s).",
            "error"
        )
        return redirect(url_for("main.minhas_reservas"))

    livro = (
        Livro.query
        .filter(Livro.id == livro_id, Livro.ativo.is_(True))
        .first_or_404()
    )

    disp_count = (
        Exemplar.query
        .filter_by(livro_id=livro.id, status="disponivel", ativo=True)
        .count()
    )

    if disp_count <= 0:
        flash("Sem exemplares disponíveis no momento.", "error")
        return redirect(url_for("main.livro_detalhe", livro_id=livro.id))

    endereco_ok = all([
        (usuario.cep or "").strip(),
        (usuario.rua or "").strip(),
        (usuario.numero or "").strip(),
        (usuario.bairro or "").strip(),
        (usuario.cidade or "").strip(),
        (usuario.uf or "").strip(),
    ])

    return render_template(
        "reservar_checkout.html",
        livro=livro,
        usuario=usuario,
        endereco_ok=endereco_ok,
        estoque_disponivel=disp_count,
        politica_ativa=politica,
    )


@main_bp.post("/livro/<int:livro_id>/reservar/confirmar")
def reservar_confirmar(livro_id):
    if not login_required():
        return redirect(url_for("auth.login"))

    cancelar_retiradas_expiradas()

    usuario = Usuario.query.get_or_404(session["usuario_id"])
    politica = obter_politica_ativa()

    if usuario.esta_bloqueado():
        flash(usuario.motivo_bloqueio_msg(), "error")
        return redirect(url_for("main.minhas_reservas"))

    resumo_atraso = obter_resumo_atraso_ativo_usuario(usuario.id)
    if resumo_atraso["tem_atraso"]:
        flash(mensagem_atraso_ativo(resumo_atraso), "error")
        return redirect(url_for("main.minhas_reservas"))

    if not usuario.perfil_completo_para_reserva():
        flash("Complete seu perfil para liberar as reservas.", "error")
        return redirect(url_for("main.perfil"))

    if usuario_ja_tem_reserva_ativa_do_livro(usuario.id, livro_id):
        flash("Você já possui uma reserva ativa deste livro.", "warning")
        return redirect(url_for("main.minhas_reservas"))

    if usuario_atingiu_limite_reservas(usuario.id, politica):
        flash(
            f"Você atingiu o limite de {politica.limite_reservas_ativas} reserva(s) ativa(s).",
            "error"
        )
        return redirect(url_for("main.minhas_reservas"))

    tipo = (request.form.get("tipo") or "").strip().lower()
    if tipo not in ("retirada", "entrega"):
        flash("Selecione retirada ou entrega.", "error")
        return redirect(url_for("main.reservar_checkout", livro_id=livro_id))

    if tipo == "entrega":
        obrig = ["cep", "rua", "numero", "bairro", "cidade", "uf"]
        for campo in obrig:
            if not (getattr(usuario, campo, "") or "").strip():
                flash("Para entrega, complete seu endereço no perfil.", "error")
                return redirect(url_for("main.perfil"))

    livro = (
        Livro.query
        .filter(Livro.id == livro_id, Livro.ativo.is_(True))
        .first_or_404()
    )

    exemplar = (
        Exemplar.query
        .filter_by(livro_id=livro.id, status="disponivel", ativo=True)
        .order_by(Exemplar.id.asc())
        .first()
    )

    if not exemplar:
        flash("Sem exemplares disponíveis no momento.", "error")
        return redirect(url_for("main.livro_detalhe", livro_id=livro.id))

    exemplar.status = "reservado"

    reserva = Reserva(
        usuario_id=usuario.id,
        exemplar_id=exemplar.id,
        tipo=tipo,
        status="pendente",
        cep=usuario.cep,
        rua=usuario.rua,
        numero=usuario.numero,
        bairro=usuario.bairro,
        cidade=usuario.cidade,
        uf=usuario.uf,
        complemento=usuario.complemento,
        criado_em=agora_utc(),
        renovacoes=0,
    )

    bd.session.add(reserva)
    bd.session.flush()

    congelar_politica_na_reserva(reserva, politica)

    bd.session.commit()

    flash("Reserva criada! Aguarde a aprovação do funcionário.", "success")
    return redirect(url_for("main.minhas_reservas"))


@main_bp.get("/minhas-reservas")
def minhas_reservas():
    if not login_required():
        return redirect(url_for("auth.login"))

    usuario = Usuario.query.get_or_404(session["usuario_id"])

    reservas = (
        Reserva.query
        .options(
            selectinload(Reserva.exemplar).selectinload(Exemplar.livro)
        )
        .filter_by(usuario_id=usuario.id)
        .order_by(Reserva.id.desc())
        .all()
    )

    preparar_prazos_minhas_reservas(reservas)

    reservas_signature = gerar_assinatura_reservas(reservas)

    return render_template(
        "minhas_reservas.html",
        reservas=reservas,
        hoje=agora_br().date(),
        reservas_signature=reservas_signature
    )

@main_bp.get("/minhas-reservas/assinatura")
def minhas_reservas_assinatura():
    if not session.get("usuario_id"):
        return jsonify({
            "ok": False,
            "auth": False
        }), 401

    reservas = (
        Reserva.query
        .options(
            selectinload(Reserva.exemplar).selectinload(Exemplar.livro)
        )
        .filter_by(usuario_id=session["usuario_id"])
        .order_by(Reserva.id.desc())
        .all()
    )

    assinatura = gerar_assinatura_reservas(reservas)

    return jsonify({
        "ok": True,
        "signature": assinatura,
        "hora": agora_br().strftime("%d/%m/%Y %H:%M:%S")
    })

@main_bp.post("/minhas-reservas/<int:reserva_id>/cancelar")
def cancelar_reserva(reserva_id):
    if not login_required():
        return redirect(url_for("auth.login"))

    cancelar_retiradas_expiradas()

    usuario = Usuario.query.get_or_404(session["usuario_id"])
    r = Reserva.query.get_or_404(reserva_id)

    if r.usuario_id != usuario.id:
        flash("Ação não permitida.", "error")
        return redirect(url_for("main.minhas_reservas"))

    if r.status not in ("pendente", "aprovada", "separado"):
        flash("Você só pode cancelar reservas que ainda não foram retiradas, enviadas, devolvidas ou canceladas.", "error")
        return redirect(url_for("main.minhas_reservas"))

    r.status = "cancelado"

    ex = Exemplar.query.get(r.exemplar_id)
    if ex and (ex.status or "").strip().lower() == "reservado":
        ex.status = "disponivel"

    bd.session.commit()
    flash("Reserva cancelada.", "success")
    return redirect(url_for("main.minhas_reservas"))


@main_bp.post("/minhas-reservas/<int:reserva_id>/renovar")
def renovar_reserva(reserva_id):
    if not login_required():
        return redirect(url_for("auth.login"))

    cancelar_retiradas_expiradas()

    usuario = Usuario.query.get_or_404(session["usuario_id"])

    if usuario.esta_bloqueado():
        flash(usuario.motivo_bloqueio_msg(), "error")
        return redirect(url_for("main.minhas_reservas"))

    r = Reserva.query.get_or_404(reserva_id)
    if r.usuario_id != usuario.id:
        flash("Ação não permitida.", "error")
        return redirect(url_for("main.minhas_reservas"))

    garantir_snapshot_politica_reserva(r)

    if r.status not in ("retirado", "enviado"):
        flash("Só é possível renovar quando o exemplar já está com você.", "error")
        return redirect(url_for("main.minhas_reservas"))

    if r.devolvido_em is not None:
        flash("Esta reserva já foi devolvida.", "error")
        return redirect(url_for("main.minhas_reservas"))

    max_renovacoes = int(r.max_renovacoes_aplicado or 1)
    prazo_renovacao_dias = int(r.prazo_renovacao_dias_aplicado or 14)

    if r.renovacoes >= max_renovacoes:
        flash(f"Limite de renovação atingido ({max_renovacoes} renovação(ões)).", "error")
        return redirect(url_for("main.minhas_reservas"))

    if not r.devolver_ate:
        flash("Esta reserva não possui data de devolução definida.", "error")
        return redirect(url_for("main.minhas_reservas"))

    if agora_br().date() > r.devolver_ate:
        dias_atraso = (agora_br().date() - r.devolver_ate).days
        flash(
            f"Não é possível renovar este empréstimo, pois ele está atrasado há {dias_atraso} dia{'s' if dias_atraso != 1 else ''}. "
            "Regularize a devolução com a biblioteca.",
            "error"
        )
        return redirect(url_for("main.minhas_reservas"))

    ex = Exemplar.query.get(r.exemplar_id)
    livro_id = getattr(ex, "livro_id", None)

    if livro_id is not None:
        ha_fila = (
            Reserva.query
            .join(Exemplar, Reserva.exemplar_id == Exemplar.id)
            .filter(
                Exemplar.livro_id == livro_id,
                Reserva.usuario_id != usuario.id,
                Reserva.status.in_(("pendente", "aprovada", "separado")),
            )
            .first()
            is not None
        )
        if ha_fila:
            flash("Não foi possível renovar: há outra reserva aguardando este livro.", "error")
            return redirect(url_for("main.minhas_reservas"))

    r.devolver_ate = r.devolver_ate + timedelta(days=prazo_renovacao_dias)
    r.renovacoes += 1
    bd.session.commit()

    flash(
        f"Renovação realizada! Prazo estendido por mais {prazo_renovacao_dias} dia(s).",
        "success"
    )
    return redirect(url_for("main.minhas_reservas"))