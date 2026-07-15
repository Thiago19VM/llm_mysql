from datetime import timedelta

from app.extensions import bd
from app.utils.datetime_utils import agora_utc
from app.models.reserva import Reserva
from app.models.politica_emprestimo import PoliticaEmprestimo


STATUS_RESERVA_ATIVA = ("pendente", "aprovada", "separado", "enviado", "retirado")


def criar_politica_inicial() -> PoliticaEmprestimo:
    politica = PoliticaEmprestimo(
        nome="Política padrão (legado)",
        observacao="Criada automaticamente a partir das regras originais do sistema.",
        prazo_devolucao_dias=14,
        prazo_retirada_horas=48,
        limite_reservas_ativas=999,
        max_renovacoes=1,
        prazo_renovacao_dias=14,
        tolerancia_atraso_dias=0,
        tipo_bloqueio_atraso="1_por_1",
        valor_bloqueio_atraso=1,
        ativa=True,
        vigente_desde=agora_utc(),
    )
    bd.session.add(politica)
    bd.session.commit()
    return politica


def obter_politica_ativa() -> PoliticaEmprestimo:
    politica = (
        PoliticaEmprestimo.query
        .filter(PoliticaEmprestimo.ativa.is_(True))
        .order_by(PoliticaEmprestimo.vigente_desde.desc(), PoliticaEmprestimo.id.desc())
        .first()
    )

    if politica is None:
        politica = criar_politica_inicial()

    return politica


def contar_reservas_ativas_usuario(usuario_id: int) -> int:
    return (
        Reserva.query
        .filter(
            Reserva.usuario_id == usuario_id,
            Reserva.status.in_(STATUS_RESERVA_ATIVA)
        )
        .count()
    )


def usuario_atingiu_limite_reservas(usuario_id: int, politica: PoliticaEmprestimo | None = None) -> bool:
    politica = politica or obter_politica_ativa()

    try:
        limite = int(politica.limite_reservas_ativas or 0)
    except Exception:
        limite = 0

    if limite <= 0:
        return False

    return contar_reservas_ativas_usuario(usuario_id) >= limite


def congelar_politica_na_reserva(reserva: Reserva, politica: PoliticaEmprestimo | None = None) -> Reserva:
    politica = politica or obter_politica_ativa()

    if reserva.politica_id is None:
        reserva.politica_id = politica.id

    if reserva.prazo_devolucao_dias_aplicado is None:
        reserva.prazo_devolucao_dias_aplicado = politica.prazo_devolucao_dias

    if reserva.prazo_retirada_horas_aplicado is None:
        reserva.prazo_retirada_horas_aplicado = politica.prazo_retirada_horas

    if reserva.limite_reservas_ativas_aplicado is None:
        reserva.limite_reservas_ativas_aplicado = politica.limite_reservas_ativas

    if reserva.max_renovacoes_aplicado is None:
        reserva.max_renovacoes_aplicado = politica.max_renovacoes

    if reserva.prazo_renovacao_dias_aplicado is None:
        reserva.prazo_renovacao_dias_aplicado = politica.prazo_renovacao_dias

    if reserva.tolerancia_atraso_dias_aplicada is None:
        reserva.tolerancia_atraso_dias_aplicada = politica.tolerancia_atraso_dias

    if not reserva.tipo_bloqueio_atraso_aplicado:
        reserva.tipo_bloqueio_atraso_aplicado = politica.tipo_bloqueio_atraso

    if reserva.valor_bloqueio_atraso_aplicado is None:
        reserva.valor_bloqueio_atraso_aplicado = politica.valor_bloqueio_atraso

    return reserva


def garantir_snapshot_politica_reserva(reserva: Reserva) -> Reserva:
    campos_ok = all([
        reserva.politica_id is not None,
        reserva.prazo_devolucao_dias_aplicado is not None,
        reserva.prazo_retirada_horas_aplicado is not None,
        reserva.max_renovacoes_aplicado is not None,
        reserva.prazo_renovacao_dias_aplicado is not None,
        reserva.tolerancia_atraso_dias_aplicada is not None,
        bool(reserva.tipo_bloqueio_atraso_aplicado),
        reserva.valor_bloqueio_atraso_aplicado is not None,
    ])

    if campos_ok:
        return reserva

    politica = None
    if reserva.politica_id:
        politica = PoliticaEmprestimo.query.get(reserva.politica_id)

    return congelar_politica_na_reserva(reserva, politica=politica or obter_politica_ativa())


def aplicar_bloqueio_por_atraso_configuravel(usuario, reserva: Reserva, dias_atraso: int) -> int:
    if dias_atraso <= 0:
        return 0

    garantir_snapshot_politica_reserva(reserva)

    try:
        tolerancia = max(int(reserva.tolerancia_atraso_dias_aplicada or 0), 0)
    except Exception:
        tolerancia = 0

    atraso_real = dias_atraso - tolerancia
    if atraso_real <= 0:
        return 0

    tipo = (reserva.tipo_bloqueio_atraso_aplicado or "1_por_1").strip().lower()

    try:
        valor = max(int(reserva.valor_bloqueio_atraso_aplicado or 0), 0)
    except Exception:
        valor = 0

    if tipo == "sem_bloqueio" or valor <= 0:
        return 0

    if tipo == "fixo":
        dias_bloqueio = valor
    else:
        dias_bloqueio = atraso_real * valor

    if dias_bloqueio <= 0:
        return 0

    now = agora_utc()
    novo_bloqueio = now + timedelta(days=dias_bloqueio)

    if getattr(usuario, "bloqueado_ate", None) and usuario.bloqueado_ate > now:
        usuario.bloqueado_ate = max(usuario.bloqueado_ate, novo_bloqueio)
    else:
        usuario.bloqueado_ate = novo_bloqueio

    return dias_bloqueio