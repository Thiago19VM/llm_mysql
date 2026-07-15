from app.extensions import bd
from datetime import datetime


class Usuario(bd.Model):
    __tablename__ = "usuarios"

    id = bd.Column(bd.BigInteger, primary_key=True)
    nome_usuario = bd.Column(bd.String(50), nullable=False, unique=True)
    email = bd.Column(bd.String(190), nullable=False, unique=True)
    hash_senha = bd.Column(bd.String(255), nullable=True)
    google_id = bd.Column(bd.String(64), unique=True, nullable=True)

    genero = bd.Column(bd.Enum("F", "M"), nullable=True)
    data_nascimento = bd.Column(bd.Date, nullable=True)
    criado_em = bd.Column(bd.DateTime, server_default=bd.func.now())

    telefone = bd.Column(bd.String(30), nullable=True)
    cep = bd.Column(bd.String(12), nullable=True)
    rua = bd.Column(bd.String(180), nullable=True)
    numero = bd.Column(bd.String(20), nullable=True)
    bairro = bd.Column(bd.String(120), nullable=True)
    cidade = bd.Column(bd.String(120), nullable=True)
    uf = bd.Column(bd.String(2), nullable=True)
    complemento = bd.Column(bd.String(180), nullable=True)
    foto = bd.Column(bd.String(255), nullable=True)
    bio = bd.Column(bd.String(280), nullable=True)

    # status persistente da conta (para Ativar/Desativar/Bloquear)
    # valores: "ativo" | "inativo" | "bloqueado"
    status = bd.Column(bd.String(20), nullable=False, server_default="ativo", index=True)

    # bloqueio por atraso (temporário)
    bloqueado_ate = bd.Column(bd.DateTime, nullable=True)

    # presença (online/offline) — UTC naive
    ultimo_ativo_em = bd.Column(bd.DateTime, nullable=True, index=True)

    reservas = bd.relationship("Reserva", back_populates="usuario", lazy=True)
    forum_privacidade = bd.Column(bd.String(20), nullable=False, server_default="todos")

    def perfil_completo_para_reserva(self) -> bool:
        obrigatorios = [
            self.telefone, self.cep, self.rua, self.numero,
            self.bairro, self.cidade, self.uf
        ]
        return all(v and str(v).strip() for v in obrigatorios)

    def campos_faltando_reserva(self):
        faltando = []
        if not (self.telefone and self.telefone.strip()): faltando.append("Telefone")
        if not (self.cep and self.cep.strip()): faltando.append("CEP")
        if not (self.rua and self.rua.strip()): faltando.append("Rua")
        if not (self.numero and self.numero.strip()): faltando.append("Número")
        if not (self.bairro and self.bairro.strip()): faltando.append("Bairro")
        if not (self.cidade and self.cidade.strip()): faltando.append("Cidade")
        if not (self.uf and self.uf.strip()): faltando.append("UF")
        return faltando

    #  helper de bloqueio (manual ou por atraso)
    def esta_bloqueado(self) -> bool:
        st = (self.status or "ativo").strip().lower()
        if st == "bloqueado":
            return True
        if not self.bloqueado_ate:
            return False
        return self.bloqueado_ate > datetime.utcnow()

    def motivo_bloqueio_msg(self) -> str:
        if not self.esta_bloqueado():
            return ""
        if self.bloqueado_ate and self.bloqueado_ate > datetime.utcnow():
            return f"Você está bloqueado para novas reservas até {self.bloqueado_ate.strftime('%d/%m/%Y %H:%M')}."
        return "Sua conta está bloqueada."