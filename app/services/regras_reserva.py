# app/services/regras_reserva.py

from datetime import date
from app.models.reserva import Reserva


STATUS_EMPRESTIMO_ATIVO = ("retirado", "enviado")


def obter_atraso_ativo_usuario(usuario_id: int):
    """
    Retorna informações sobre empréstimos atrasados ainda não devolvidos.
    Enquanto existir atraso ativo, o usuário não pode reservar novos livros.
    """
    hoje = date.today()

    reservas_atrasadas = (
        Reserva.query
        .filter(
            Reserva.usuario_id == usuario_id,
            Reserva.status.in_(STATUS_EMPRESTIMO_ATIVO),
            Reserva.devolver_ate.isnot(None),
            Reserva.devolver_ate < hoje
        )
        .all()
    )

    if not reservas_atrasadas:
        return {
            "tem_atraso": False,
            "maior_atraso_dias": 0,
            "reservas": []
        }

    maior_atraso = max((hoje - r.devolver_ate).days for r in reservas_atrasadas)

    return {
        "tem_atraso": True,
        "maior_atraso_dias": maior_atraso,
        "reservas": reservas_atrasadas
    }


def usuario_pode_reservar(usuario):
    """
    Valida se o usuário pode fazer nova reserva.
    Bloqueia por:
    1. bloqueio formal em bloqueado_ate;
    2. empréstimo atrasado ainda não devolvido.
    """
    hoje = date.today()

    if getattr(usuario, "bloqueado_ate", None) and usuario.bloqueado_ate.date() > hoje:
        return False, f"Você está bloqueado até {usuario.bloqueado_ate.strftime('%d/%m/%Y')}."

    atraso = obter_atraso_ativo_usuario(usuario.id)

    if atraso["tem_atraso"]:
        dias = atraso["maior_atraso_dias"]
        return False, (
            f"Você possui livro em atraso há {dias} dia{'s' if dias != 1 else ''}. "
            "Devolva o livro atrasado para poder fazer novas reservas."
        )

    return True, None