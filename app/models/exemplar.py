from datetime import datetime
from app.extensions import bd

class Exemplar(bd.Model):
    __tablename__ = "exemplares"

    id = bd.Column(bd.BigInteger, primary_key=True)
    livro_id = bd.Column(bd.BigInteger, bd.ForeignKey("livros.id"), nullable=False)
    codigo_exemplar = bd.Column(bd.String(60), nullable=False, unique=True)
    status = bd.Column(bd.String(20), nullable=False, default="disponivel")
    localizacao = bd.Column(bd.String(120))
    ativo = bd.Column(bd.Boolean, nullable=False, default=True)
    criado_em = bd.Column(bd.DateTime, default=datetime.utcnow)
    excluido_em = bd.Column(bd.DateTime, nullable=True)
    #  RELAÇÃO inversa (sem backref)
    reservas = bd.relationship("Reserva", back_populates="exemplar", lazy=True)
