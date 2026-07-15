from sqlalchemy.dialects.mysql import BIGINT
from app.extensions import bd


class PoliticaEmprestimo(bd.Model):
    __tablename__ = "politicas_emprestimo"

    id = bd.Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)

    nome = bd.Column(bd.String(120), nullable=False)
    observacao = bd.Column(bd.String(255), nullable=True)

    prazo_devolucao_dias = bd.Column(bd.Integer, nullable=False, default=14)
    prazo_retirada_horas = bd.Column(bd.Integer, nullable=False, default=48)

    # 999 = praticamente sem limite, preservando o comportamento atual
    limite_reservas_ativas = bd.Column(bd.Integer, nullable=False, default=999)

    max_renovacoes = bd.Column(bd.Integer, nullable=False, default=1)
    prazo_renovacao_dias = bd.Column(bd.Integer, nullable=False, default=14)

    tolerancia_atraso_dias = bd.Column(bd.Integer, nullable=False, default=0)

    # opções:
    # - sem_bloqueio
    # - 1_por_1   -> cada dia de atraso gera N dias de bloqueio
    # - fixo      -> sempre aplica N dias fixos
    tipo_bloqueio_atraso = bd.Column(bd.String(20), nullable=False, default="1_por_1")
    valor_bloqueio_atraso = bd.Column(bd.Integer, nullable=False, default=1)

    ativa = bd.Column(bd.Boolean, nullable=False, default=True)
    vigente_desde = bd.Column(bd.DateTime, nullable=False, server_default=bd.func.now())
    criada_em = bd.Column(bd.DateTime, nullable=False, server_default=bd.func.now())
    criada_por = bd.Column(BIGINT(unsigned=True), nullable=True)

    reservas = bd.relationship("Reserva", back_populates="politica", lazy=True)

    @property
    def tipo_bloqueio_label(self) -> str:
        t = (self.tipo_bloqueio_atraso or "").strip().lower()
        if t == "sem_bloqueio":
            return "Sem bloqueio"
        if t == "fixo":
            return f"Fixo ({self.valor_bloqueio_atraso} dia(s))"
        return f"1 por 1 × {self.valor_bloqueio_atraso}"