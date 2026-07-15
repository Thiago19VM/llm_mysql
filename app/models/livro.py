from datetime import datetime
from app.extensions import bd

class Livro(bd.Model):
    __tablename__ = "livros"

    id = bd.Column(bd.BigInteger, primary_key=True)
    titulo = bd.Column(bd.String(255), nullable=False)
    autor = bd.Column(bd.String(180), nullable=False)
    genero = bd.Column(bd.String(120))
    editora = bd.Column(bd.String(180))
    ano = bd.Column(bd.Integer)
    isbn = bd.Column(bd.String(20))
    capa_arquivo = bd.Column(bd.String(255))
    tipo_capa = bd.Column(bd.String(30), nullable=False, default="Livro de Bolso")
    sinopse = bd.Column(bd.Text)
    criado_em = bd.Column(bd.DateTime, default=datetime.utcnow)
    edicao = bd.Column(bd.String(30))
    excluido_em = bd.Column(bd.DateTime, nullable=True)
    ativo = bd.Column(bd.Boolean, nullable=False, default=True)



    exemplares = bd.relationship("Exemplar", backref="livro", lazy=True)