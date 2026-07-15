from sqlalchemy.dialects.mysql import BIGINT
from app.extensions import bd
from app.utils.datetime_utils import agora_utc


class Notificacao(bd.Model):
    __tablename__ = "notificacoes"

    id = bd.Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)

    usuario_id = bd.Column(
        BIGINT(unsigned=True),
        bd.ForeignKey("usuarios.id"),
        nullable=False,
        index=True
    )

    ator_id = bd.Column(
        BIGINT(unsigned=True),
        bd.ForeignKey("usuarios.id"),
        nullable=True,
        index=True
    )

    tipo = bd.Column(bd.String(50), nullable=False, index=True)
    titulo = bd.Column(bd.String(160), nullable=False)
    mensagem = bd.Column(bd.String(255), nullable=False)
    link = bd.Column(bd.String(255), nullable=True)

    lida = bd.Column(
        bd.Boolean,
        nullable=False,
        default=False,
        server_default=bd.text("0"),
        index=True
    )

    criada_em = bd.Column(
        bd.DateTime,
        nullable=False,
        default=agora_utc,
        server_default=bd.func.now(),
        index=True
    )

    usuario = bd.relationship("Usuario", foreign_keys=[usuario_id], backref="notificacoes_recebidas")
    ator = bd.relationship("Usuario", foreign_keys=[ator_id], backref="notificacoes_geradas")