from functools import wraps

from flask import render_template, request, redirect, url_for, flash, session

from . import funcionario_bp
from app.extensions import bd
from app.utils.datetime_utils import agora_br, agora_utc
from app.models.politica_emprestimo import PoliticaEmprestimo
from app.services.politicas_emprestimo import obter_politica_ativa


def funcionario_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("funcionario_id"):
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


def _to_int(valor, padrao=0):
    try:
        return int(str(valor).strip())
    except Exception:
        return padrao


@funcionario_bp.get("/politicas-emprestimo")
@funcionario_required
def politicas_emprestimo():
    politica_atual = obter_politica_ativa()

    historico = (
        PoliticaEmprestimo.query
        .order_by(PoliticaEmprestimo.ativa.desc(), PoliticaEmprestimo.id.desc())
        .all()
    )

    now = agora_br()
    return render_template(
        "funcionario/politicas_emprestimo.html",
        politica_atual=politica_atual,
        historico=historico,
        data_hoje=now.strftime("%d/%m/%Y"),
        hora_agora=now.strftime("%H:%M"),
    )


@funcionario_bp.post("/politicas-emprestimo/salvar")
@funcionario_required
def politicas_emprestimo_salvar():
    nome = (request.form.get("nome") or "").strip() or "Nova política"

    prazo_devolucao_dias = _to_int(request.form.get("prazo_devolucao_dias"), 14)
    prazo_retirada_horas = _to_int(request.form.get("prazo_retirada_horas"), 48)
    limite_reservas_ativas = _to_int(request.form.get("limite_reservas_ativas"), 999)
    max_renovacoes = _to_int(request.form.get("max_renovacoes"), 1)
    prazo_renovacao_dias = _to_int(request.form.get("prazo_renovacao_dias"), 14)
    tolerancia_atraso_dias = _to_int(request.form.get("tolerancia_atraso_dias"), 0)

    tipo_bloqueio_atraso = (request.form.get("tipo_bloqueio_atraso") or "1_por_1").strip().lower()
    valor_bloqueio_atraso = _to_int(request.form.get("valor_bloqueio_atraso"), 1)

    observacao = (request.form.get("observacao") or "").strip() or None

    if prazo_devolucao_dias < 1:
        flash("O prazo de devolução deve ser pelo menos 1 dia.", "error")
        return redirect(url_for("funcionario.politicas_emprestimo"))

    if prazo_retirada_horas < 1:
        flash("O prazo de retirada deve ser pelo menos 1 hora.", "error")
        return redirect(url_for("funcionario.politicas_emprestimo"))

    if limite_reservas_ativas < 1:
        flash("O limite de reservas ativas deve ser pelo menos 1.", "error")
        return redirect(url_for("funcionario.politicas_emprestimo"))

    if max_renovacoes < 0:
        flash("O máximo de renovações não pode ser negativo.", "error")
        return redirect(url_for("funcionario.politicas_emprestimo"))

    if prazo_renovacao_dias < 1:
        flash("O prazo de renovação deve ser pelo menos 1 dia.", "error")
        return redirect(url_for("funcionario.politicas_emprestimo"))

    if tolerancia_atraso_dias < 0:
        flash("A tolerância de atraso não pode ser negativa.", "error")
        return redirect(url_for("funcionario.politicas_emprestimo"))

    if tipo_bloqueio_atraso not in {"sem_bloqueio", "1_por_1", "fixo"}:
        flash("Tipo de bloqueio por atraso inválido.", "error")
        return redirect(url_for("funcionario.politicas_emprestimo"))

    if tipo_bloqueio_atraso == "sem_bloqueio":
        valor_bloqueio_atraso = 0
    elif valor_bloqueio_atraso < 1:
        flash("O valor do bloqueio por atraso deve ser pelo menos 1.", "error")
        return redirect(url_for("funcionario.politicas_emprestimo"))

    atual = (
        PoliticaEmprestimo.query
        .filter(PoliticaEmprestimo.ativa.is_(True))
        .all()
    )

    for p in atual:
        p.ativa = False

    nova = PoliticaEmprestimo(
        nome=nome,
        observacao=observacao,
        prazo_devolucao_dias=prazo_devolucao_dias,
        prazo_retirada_horas=prazo_retirada_horas,
        limite_reservas_ativas=limite_reservas_ativas,
        max_renovacoes=max_renovacoes,
        prazo_renovacao_dias=prazo_renovacao_dias,
        tolerancia_atraso_dias=tolerancia_atraso_dias,
        tipo_bloqueio_atraso=tipo_bloqueio_atraso,
        valor_bloqueio_atraso=valor_bloqueio_atraso,
        ativa=True,
        vigente_desde=agora_utc(),
        criada_por=session.get("funcionario_id"),
    )

    bd.session.add(nova)
    bd.session.commit()

    flash("Nova política de empréstimo salva e ativada com sucesso.", "success")
    return redirect(url_for("funcionario.politicas_emprestimo"))


@funcionario_bp.post("/politicas-emprestimo/<int:politica_id>/ativar")
@funcionario_required
def politicas_emprestimo_ativar(politica_id):
    politica = PoliticaEmprestimo.query.get_or_404(politica_id)

    if politica.ativa:
        flash("Essa política já está ativa.", "info")
        return redirect(url_for("funcionario.politicas_emprestimo"))

    atuais = (
        PoliticaEmprestimo.query
        .filter(PoliticaEmprestimo.ativa.is_(True))
        .all()
    )
    for p in atuais:
        p.ativa = False

    politica.ativa = True
    politica.vigente_desde = agora_utc()

    bd.session.commit()

    flash("Política ativada com sucesso. Ela será usada apenas em novas reservas.", "success")
    return redirect(url_for("funcionario.politicas_emprestimo"))