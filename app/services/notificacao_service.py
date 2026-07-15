
from app.extensions import bd
from app.models.notificacao import Notificacao


def criar_notificacao(
    usuario_id,
    ator_id,
    tipo,
    titulo,
    mensagem,
    link=None,
):
    notif = Notificacao(
        usuario_id=usuario_id,
        ator_id=ator_id,
        tipo=tipo,
        titulo=titulo,
        mensagem=mensagem,
        link=link,
        lida=False,
    )
    bd.session.add(notif)
    return notif