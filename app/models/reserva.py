from datetime import datetime, date
from sqlalchemy.dialects.mysql import BIGINT
from app.extensions import bd


class Reserva(bd.Model):
    __tablename__ = "reservas"

    id = bd.Column(BIGINT(unsigned=True), primary_key=True, autoincrement=True)

    usuario_id = bd.Column(BIGINT(unsigned=True), bd.ForeignKey("usuarios.id"), nullable=False, index=True)
    exemplar_id = bd.Column(BIGINT(unsigned=True), bd.ForeignKey("exemplares.id"), nullable=False, index=True)

    tipo = bd.Column(bd.String(20), nullable=False, default="retirada")
    status = bd.Column(bd.String(20), nullable=False, default="pendente")

    cep = bd.Column(bd.String(12))
    rua = bd.Column(bd.String(180))
    numero = bd.Column(bd.String(20))
    bairro = bd.Column(bd.String(120))
    cidade = bd.Column(bd.String(120))
    uf = bd.Column(bd.String(2))
    complemento = bd.Column(bd.String(180))

    observacao = bd.Column(bd.String(255))
    codigo_rastreio = bd.Column(bd.String(80))

    criado_em = bd.Column(bd.DateTime, default=datetime.utcnow)

    exemplar = bd.relationship("Exemplar", back_populates="reservas")

    retirar_ate = bd.Column(bd.DateTime, nullable=True)

    emprestado_em = bd.Column(bd.DateTime, nullable=True)
    devolver_ate = bd.Column(bd.Date, nullable=True)
    devolvido_em = bd.Column(bd.DateTime, nullable=True)

    renovacoes = bd.Column(bd.Integer, nullable=False, default=0)

    # =========================
    # SNAPSHOT DA POLÍTICA
    # =========================
    politica_id = bd.Column(
        BIGINT(unsigned=True),
        bd.ForeignKey("politicas_emprestimo.id"),
        nullable=True,
        index=True
    )

    prazo_devolucao_dias_aplicado = bd.Column(bd.Integer, nullable=True)
    prazo_retirada_horas_aplicado = bd.Column(bd.Integer, nullable=True)

    limite_reservas_ativas_aplicado = bd.Column(bd.Integer, nullable=True)

    max_renovacoes_aplicado = bd.Column(bd.Integer, nullable=True)
    prazo_renovacao_dias_aplicado = bd.Column(bd.Integer, nullable=True)

    tolerancia_atraso_dias_aplicada = bd.Column(bd.Integer, nullable=True)
    tipo_bloqueio_atraso_aplicado = bd.Column(bd.String(20), nullable=True)
    valor_bloqueio_atraso_aplicado = bd.Column(bd.Integer, nullable=True)

    usuario = bd.relationship("Usuario", back_populates="reservas")
    politica = bd.relationship("PoliticaEmprestimo", back_populates="reservas")

    # =========================
    # HELPERS DE NEGÓCIO
    # =========================

    @property
    def status_norm(self) -> str:
        return (self.status or "").strip().lower()

    @property
    def tipo_norm(self) -> str:
        return (self.tipo or "").strip().lower()

    @property
    def max_renovacoes_permitidas(self) -> int:
        try:
            return max(int(self.max_renovacoes_aplicado or 1), 0)
        except Exception:
            return 1

    @property
    def prazo_renovacao_dias(self) -> int:
        try:
            return max(int(self.prazo_renovacao_dias_aplicado or 14), 1)
        except Exception:
            return 14

    def esta_em_emprestimo(self) -> bool:
        return self.status_norm in {"retirado", "enviado"}

    def devolucao_em_atraso(self, hoje: date | None = None) -> bool:
        if hoje is None:
            hoje = date.today()

        return (
            self.esta_em_emprestimo()
            and self.devolver_ate is not None
            and self.devolver_ate < hoje
        )

    def devolucao_vence_hoje(self, hoje: date | None = None) -> bool:
        if hoje is None:
            hoje = date.today()

        return (
            self.esta_em_emprestimo()
            and self.devolver_ate is not None
            and self.devolver_ate == hoje
        )

    def devolucao_no_prazo(self, hoje: date | None = None) -> bool:
        if hoje is None:
            hoje = date.today()

        return (
            self.esta_em_emprestimo()
            and self.devolver_ate is not None
            and self.devolver_ate >= hoje
        )

    def retirada_expirada(self, agora_utc: datetime | None = None) -> bool:
        if agora_utc is None:
            agora_utc = datetime.utcnow()

        return (
            self.status_norm == "separado"
            and self.retirar_ate is not None
            and self.retirar_ate < agora_utc
        )

    def dias_atraso_devolucao(self, hoje: date | None = None) -> int:
        if hoje is None:
            hoje = date.today()

        if not self.devolucao_em_atraso(hoje=hoje):
            return 0

        return (hoje - self.devolver_ate).days