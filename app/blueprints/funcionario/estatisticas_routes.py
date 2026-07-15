# app/blueprints/funcionario/estatisticas_routes.py

from datetime import datetime, timedelta

from flask import render_template
from sqlalchemy import func, or_

from . import funcionario_bp
from .routes import funcionario_required

# from .decorators import funcionario_required

from app.utils.datetime_utils import agora_utc, agora_br
from app.extensions import bd

from app.models.livro import Livro
from app.models.exemplar import Exemplar
from app.models.usuario import Usuario
from app.models.reserva import Reserva


def _meses_recentes(qtd=6):
    """
    Retorna uma lista de tuplas (ano, mes) dos últimos meses
    e uma lista de labels formatadas, ex: ['Out/25', 'Nov/25', ...]
    """
    nomes = [
        "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
        "Jul", "Ago", "Set", "Out", "Nov", "Dez"
    ]

    atual = agora_br()
    ano_atual = atual.year
    mes_atual = atual.month

    meses = []
    for i in range(qtd - 1, -1, -1):
        ano = ano_atual
        mes = mes_atual - i

        while mes <= 0:
            mes += 12
            ano -= 1

        meses.append((ano, mes))

    labels = [f"{nomes[mes - 1]}/{str(ano)[2:]}" for ano, mes in meses]
    return meses, labels


@funcionario_bp.get("/estatisticas")
@funcionario_required
def estatisticas():
    agora = agora_utc()
    hoje = agora_br().date()
    inicio_30_dias = agora - timedelta(days=30)

    # Referência dos últimos 6 meses
    meses_ref, labels_meses = _meses_recentes(6)
    primeiro_ano, primeiro_mes = meses_ref[0]
    inicio_6_meses = datetime(primeiro_ano, primeiro_mes, 1)

    # ==========================================================
    # CARDS / KPIs
    # ==========================================================


    total_livros = Livro.query.filter(Livro.ativo.is_(True)).count()
    total_exemplares = Exemplar.query.count()
    total_usuarios = Usuario.query.count()
    total_reservas = Reserva.query.count()

    aguardando_retirada = Reserva.query.filter(
        func.lower(Reserva.status) == "separado",
        Reserva.retirar_ate.isnot(None),
        Reserva.retirar_ate >= agora
    ).count()

    retirada_expirada = Reserva.query.filter(
        func.lower(Reserva.status) == "separado",
        Reserva.retirar_ate.isnot(None),
        Reserva.retirar_ate < agora
    ).count()

    emprestimos_ativos = Reserva.query.filter(
        func.lower(Reserva.status).in_(["retirado", "enviado"]),
        Reserva.devolvido_em.is_(None)
    ).count()

    emprestimos_no_prazo = Reserva.query.filter(
        func.lower(Reserva.status).in_(["retirado", "enviado"]),
        Reserva.devolvido_em.is_(None),
        or_(
            Reserva.devolver_ate.is_(None),
            Reserva.devolver_ate >= hoje
        )
    ).count()

    atrasadas = Reserva.query.filter(
        func.lower(Reserva.status).in_(["retirado", "enviado"]),
        Reserva.devolvido_em.is_(None),
        Reserva.devolver_ate.isnot(None),
        Reserva.devolver_ate < hoje
    ).count()

    devolvidas_30_dias = Reserva.query.filter(
        Reserva.devolvido_em.isnot(None),
        Reserva.devolvido_em >= inicio_30_dias
    ).count()

    devolvidas_no_prazo_30 = Reserva.query.filter(
        Reserva.devolvido_em.isnot(None),
        Reserva.devolvido_em >= inicio_30_dias,
        Reserva.devolver_ate.isnot(None),
        func.date(Reserva.devolvido_em) <= Reserva.devolver_ate
    ).count()

    devolvidas_com_atraso_30 = Reserva.query.filter(
        Reserva.devolvido_em.isnot(None),
        Reserva.devolvido_em >= inicio_30_dias,
        Reserva.devolver_ate.isnot(None),
        func.date(Reserva.devolvido_em) > Reserva.devolver_ate
    ).count()

    taxa_atraso_abertos = round((atrasadas / emprestimos_ativos) * 100, 1) if emprestimos_ativos else 0.0
    taxa_devolucao_prazo_30 = round((devolvidas_no_prazo_30 / devolvidas_30_dias) * 100, 1) if devolvidas_30_dias else 0.0

    # ==========================================================
    # GRÁFICO 1: MOVIMENTAÇÃO DOS ÚLTIMOS 6 MESES
    # ==========================================================

    reservas_por_mes_rows = (
        bd.session.query(
            func.year(Reserva.criado_em).label("ano"),
            func.month(Reserva.criado_em).label("mes"),
            func.count(Reserva.id).label("total")
        )
        .filter(Reserva.criado_em >= inicio_6_meses)
        .group_by(func.year(Reserva.criado_em), func.month(Reserva.criado_em))
        .all()
    )

    mapa_reservas_mes = {(ano, mes): total for ano, mes, total in reservas_por_mes_rows}
    serie_reservas_mes = [mapa_reservas_mes.get((ano, mes), 0) for ano, mes in meses_ref]

    devolucoes_por_mes_rows = (
        bd.session.query(
            func.year(Reserva.devolvido_em).label("ano"),
            func.month(Reserva.devolvido_em).label("mes"),
            func.count(Reserva.id).label("total")
        )
        .filter(
            Reserva.devolvido_em.isnot(None),
            Reserva.devolvido_em >= inicio_6_meses
        )
        .group_by(func.year(Reserva.devolvido_em), func.month(Reserva.devolvido_em))
        .all()
    )

    mapa_devolucoes_mes = {(ano, mes): total for ano, mes, total in devolucoes_por_mes_rows}
    serie_devolucoes_mes = [mapa_devolucoes_mes.get((ano, mes), 0) for ano, mes in meses_ref]

    # ==========================================================
    # GRÁFICO 2: TOP 5 LIVROS MAIS RESERVADOS
    # Reserva -> Exemplar -> Livro
    # ==========================================================

    top_livros_rows = (
        bd.session.query(
            Livro.titulo,
            func.count(Reserva.id).label("total")
        )
        .join(Exemplar, Exemplar.livro_id == Livro.id)
        .join(Reserva, Reserva.exemplar_id == Exemplar.id)
        .group_by(Livro.id, Livro.titulo)
        .order_by(func.count(Reserva.id).desc(), Livro.titulo.asc())
        .limit(5)
        .all()
    )

    top_livros_labels = [row[0] for row in top_livros_rows]
    top_livros_valores = [row[1] for row in top_livros_rows]

    # ==========================================================
    # GRÁFICO 3: RESERVAS POR GÊNERO
    # ==========================================================

    generos_rows = (
        bd.session.query(
            func.coalesce(Livro.genero, "Não informado").label("genero"),
            func.count(Reserva.id).label("total")
        )
        .join(Exemplar, Exemplar.livro_id == Livro.id)
        .join(Reserva, Reserva.exemplar_id == Exemplar.id)
        .group_by(func.coalesce(Livro.genero, "Não informado"))
        .order_by(func.count(Reserva.id).desc())
        .limit(6)
        .all()
    )

    generos_labels = [row[0] for row in generos_rows]
    generos_valores = [row[1] for row in generos_rows]

    # ==========================================================
    # DADOS PARA TEMPLATE
    # ==========================================================

    cards = {
        "total_livros": total_livros,
        "total_exemplares": total_exemplares,
        "total_usuarios": total_usuarios,
        "total_reservas": total_reservas,
        "aguardando_retirada": aguardando_retirada,
        "retirada_expirada": retirada_expirada,
        "emprestimos_ativos": emprestimos_ativos,
        "emprestimos_no_prazo": emprestimos_no_prazo,
        "atrasadas": atrasadas,
        "devolvidas_30_dias": devolvidas_30_dias,
        "taxa_atraso_abertos": taxa_atraso_abertos,
        "taxa_devolucao_prazo_30": taxa_devolucao_prazo_30,
    }

    graficos = {
        "movimentacao": {
            "labels": labels_meses,
            "reservas": serie_reservas_mes,
            "devolucoes": serie_devolucoes_mes,
        },
        "situacao_atual": {
            "labels": [
                "Aguardando retirada",
                "Retirada expirada",
                "Em empréstimo",
                "Atrasadas"
            ],
            "valores": [
                aguardando_retirada,
                retirada_expirada,
                emprestimos_no_prazo,
                atrasadas
            ],
        },
        "devolucoes_30_dias": {
            "labels": ["No prazo", "Com atraso"],
            "valores": [devolvidas_no_prazo_30, devolvidas_com_atraso_30],
        },
        "top_livros": {
            "labels": top_livros_labels,
            "valores": top_livros_valores,
        },
        "por_genero": {
            "labels": generos_labels,
            "valores": generos_valores,
        }
    }

    return render_template(
        "funcionario/estatisticas.html",
        cards=cards,
        graficos=graficos,
        data_hoje=agora_br().strftime("%d/%m/%Y"),
        hora_agora=agora_br().strftime("%H:%M")
    )