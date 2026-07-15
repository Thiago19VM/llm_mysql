from app.extensions import bd

class Funcionario(bd.Model):
    __tablename__ = "funcionarios"

    id = bd.Column(bd.Integer, primary_key=True)
    nome = bd.Column(bd.String(80), nullable=False)
    email = bd.Column(bd.String(120), unique=True, nullable=False, index=True)
    hash_senha = bd.Column(bd.String(255), nullable=False)
    ativo = bd.Column(bd.Boolean, nullable=False, default=True)