from datetime import datetime, timezone, timedelta

TZ_BR = timezone(timedelta(hours=-3))
TZ_UTC = timezone.utc


def agora_utc():
    """
    Retorna datetime NAIVE em UTC (para salvar no banco).
    """
    return datetime.now(TZ_UTC).replace(tzinfo=None)


def agora_br():
    """
    Retorna datetime NAIVE em horário de Brasília (para exibição/interface).
    """
    return datetime.now(TZ_UTC).astimezone(TZ_BR).replace(tzinfo=None)


def utc_naive_para_br(dt_utc_naive):
    """
    Converte datetime naive UTC (vindo do banco) para datetime naive BR.
    """
    if not dt_utc_naive:
        return None
    return dt_utc_naive.replace(tzinfo=TZ_UTC).astimezone(TZ_BR).replace(tzinfo=None)