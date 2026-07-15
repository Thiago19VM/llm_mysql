# app/models/forum.py
from __future__ import annotations

from datetime import datetime
import re

from sqlalchemy.dialects import mysql

from app.extensions import bd
from app.utils.datetime_utils import agora_utc


# BIGINT UNSIGNED no MySQL (mantém compatível com outros bancos)
U_BIGINT = bd.BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql")

# INT UNSIGNED no MySQL (para FKs que apontam para funcionarios.id)
U_INT = bd.Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql")


def slugify(texto: str) -> str:
    texto = (texto or "").strip().lower()
    texto = re.sub(r"[^\w\s-]", "", texto, flags=re.UNICODE)
    texto = re.sub(r"[\s_-]+", "-", texto, flags=re.UNICODE).strip("-")
    return texto or "topico"


class ForumTopico(bd.Model):
    __tablename__ = "forum_topicos"

    id = bd.Column(U_BIGINT, primary_key=True)

    titulo = bd.Column(bd.String(180), nullable=False)
    slug = bd.Column(bd.String(220), nullable=False, unique=True)

    conteudo = bd.Column(bd.Text, nullable=False)

    # opcional: tópico vinculado a um livro
    livro_id = bd.Column(U_BIGINT, bd.ForeignKey("livros.id"), nullable=True, index=True)

    # autor (usuário normal)
    autor_id = bd.Column(U_BIGINT, bd.ForeignKey("usuarios.id"), nullable=False, index=True)

    # estado / moderação
    fixado = bd.Column(bd.Boolean, nullable=False, default=False)
    trancado = bd.Column(bd.Boolean, nullable=False, default=False)
    oculto = bd.Column(bd.Boolean, nullable=False, default=False)
    excluido = bd.Column(bd.Boolean, nullable=False, default=False, index=True)

    visualizacoes = bd.Column(bd.Integer, nullable=False, default=0)
    comentarios_count = bd.Column(bd.Integer, nullable=False, default=0)
    upvotes_count = bd.Column(bd.Integer, nullable=False, default=0)

    criado_em = bd.Column(bd.DateTime, nullable=False, default=datetime.utcnow, index=True)
    atualizado_em = bd.Column(
        bd.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    ultimo_comentario_em = bd.Column(bd.DateTime, nullable=True, index=True)

    # dados de moderação / auditoria
    excluido_em = bd.Column(bd.DateTime, nullable=True)
    excluido_por_id = bd.Column(U_INT, bd.ForeignKey("funcionarios.id"), nullable=True)
    motivo_exclusao = bd.Column(bd.Text, nullable=True)

    ocultado_em = bd.Column(bd.DateTime, nullable=True)
    ocultado_por_id = bd.Column(U_INT, bd.ForeignKey("funcionarios.id"), nullable=True)
    motivo_ocultacao = bd.Column(bd.Text, nullable=True)

    trancado_em = bd.Column(bd.DateTime, nullable=True)
    trancado_por_id = bd.Column(U_INT, bd.ForeignKey("funcionarios.id"), nullable=True)
    motivo_trancamento = bd.Column(bd.Text, nullable=True)

    # relações principais
    autor = bd.relationship("Usuario", foreign_keys=[autor_id], lazy="joined")
    livro = bd.relationship("Livro", foreign_keys=[livro_id], lazy="joined")

    # relações de moderação
    excluido_por = bd.relationship("Funcionario", foreign_keys=[excluido_por_id])
    ocultado_por = bd.relationship("Funcionario", foreign_keys=[ocultado_por_id])
    trancado_por = bd.relationship("Funcionario", foreign_keys=[trancado_por_id])

    comentarios = bd.relationship(
        "ForumComentario",
        back_populates="topico",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    upvotes = bd.relationship(
        "ForumTopicoUpvote",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="dynamic",
    )

    __table_args__ = (
        bd.Index(
            "ix_forum_topicos_atividade",
            "fixado",
            "oculto",
            "ultimo_comentario_em",
            "criado_em",
        ),
    )

    @property
    def url_slug(self):
        return self.slug or slugify(self.titulo)


class ForumComentario(bd.Model):
    __tablename__ = "forum_comentarios"

    id = bd.Column(U_BIGINT, primary_key=True)

    topico_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("forum_topicos.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    autor_id = bd.Column(U_BIGINT, bd.ForeignKey("usuarios.id"), nullable=False, index=True)

    # resposta (profundidade infinita)
    parent_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("forum_comentarios.id"),
        nullable=True,
        index=True,
    )

    conteudo = bd.Column(bd.Text, nullable=False)

    oculto = bd.Column(bd.Boolean, nullable=False, default=False)
    upvotes_count = bd.Column(bd.Integer, nullable=False, default=0)

    criado_em = bd.Column(bd.DateTime, nullable=False, default=datetime.utcnow, index=True)
    atualizado_em = bd.Column(
        bd.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    topico = bd.relationship("ForumTopico", back_populates="comentarios")
    autor = bd.relationship("Usuario", foreign_keys=[autor_id], lazy="joined")

    parent = bd.relationship(
        "ForumComentario",
        remote_side=[id],
        foreign_keys=[parent_id],
        backref=bd.backref("respostas", lazy="selectin"),
    )

    upvotes = bd.relationship(
        "ForumComentarioUpvote",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="dynamic",
    )

    reacoes = bd.relationship(
        "ForumComentarioReacao",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="dynamic",
    )

    __table_args__ = (
        bd.Index(
            "ix_forum_comentarios_topico_parent",
            "topico_id",
            "parent_id",
            "oculto",
            "criado_em",
        ),
    )


class ForumTopicoView(bd.Model):
    __tablename__ = "forum_topico_views"

    id = bd.Column(U_BIGINT, primary_key=True)
    topico_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("forum_topicos.id", ondelete="CASCADE"),
        nullable=False,
    )
    usuario_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    criado_em = bd.Column(bd.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        bd.UniqueConstraint("topico_id", "usuario_id", name="uq_forum_topico_views"),
        bd.Index("ix_forum_topico_views_topico", "topico_id"),
    )


class ForumPresenca(bd.Model):
    __tablename__ = "forum_presencas"

    id = bd.Column(U_BIGINT, primary_key=True)
    usuario_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    last_seen = bd.Column(bd.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        bd.UniqueConstraint("usuario_id", name="uq_forum_presencas_usuario"),
        bd.Index("ix_forum_presencas_last_seen", "last_seen"),
    )

class ForumSeguidor(bd.Model):
    __tablename__ = "forum_seguidores"

    id = bd.Column(U_BIGINT, primary_key=True)

    seguidor_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )

    seguido_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )

    status = bd.Column(
        bd.String(20),
        nullable=False,
        default="aceito",
        server_default="aceito",
    )

    criado_em = bd.Column(bd.DateTime, default=datetime.utcnow, nullable=False)
    respondido_em = bd.Column(bd.DateTime, nullable=True)

    seguidor = bd.relationship(
        "Usuario",
        foreign_keys=[seguidor_id],
        backref=bd.backref("seguindo_relacoes", lazy="dynamic"),
    )

    seguido = bd.relationship(
        "Usuario",
        foreign_keys=[seguido_id],
        backref=bd.backref("seguidores_relacoes", lazy="dynamic"),
    )

    __table_args__ = (
        bd.UniqueConstraint("seguidor_id", "seguido_id", name="uq_forum_seguidores"),
        bd.Index("ix_forum_seguidores_seguido", "seguido_id"),
        bd.Index("ix_forum_seguidores_seguidor", "seguidor_id"),
        bd.Index("ix_forum_seguidores_status", "status"),
    )


class ForumTopicoUpvote(bd.Model):
    __tablename__ = "forum_topico_upvotes"

    id = bd.Column(U_BIGINT, primary_key=True)
    topico_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("forum_topicos.id", ondelete="CASCADE"),
        nullable=False,
    )
    usuario_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    criado_em = bd.Column(bd.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        bd.UniqueConstraint("topico_id", "usuario_id", name="uq_forum_topico_upvote"),
        bd.Index("ix_forum_topico_upvotes_topico", "topico_id"),
        bd.Index("ix_forum_topico_upvotes_usuario", "usuario_id"),
    )


class ForumComentarioUpvote(bd.Model):
    __tablename__ = "forum_comentario_upvotes"

    id = bd.Column(U_BIGINT, primary_key=True)
    comentario_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("forum_comentarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    usuario_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    criado_em = bd.Column(bd.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        bd.UniqueConstraint("comentario_id", "usuario_id", name="uq_forum_comentario_upvote"),
        bd.Index("ix_forum_comentario_upvotes_comentario", "comentario_id"),
        bd.Index("ix_forum_comentario_upvotes_usuario", "usuario_id"),
    )


class ForumComentarioReacao(bd.Model):
    __tablename__ = "forum_comentario_reacoes"

    id = bd.Column(U_BIGINT, primary_key=True)
    comentario_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("forum_comentarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    usuario_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    emoji = bd.Column(bd.String(16), nullable=False)
    criado_em = bd.Column(bd.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        bd.UniqueConstraint(
            "comentario_id",
            "usuario_id",
            name="uq_forum_comentario_reacao",
        ),
        bd.Index("ix_forum_comentario_reacoes_comentario", "comentario_id"),
        bd.Index("ix_forum_comentario_reacoes_emoji", "emoji"),
        bd.Index("ix_forum_comentario_reacoes_usuario", "usuario_id"),
    )


class ForumModeracaoLog(bd.Model):
    __tablename__ = "forum_moderacao_logs"

    id = bd.Column(U_BIGINT, primary_key=True)

    topico_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("forum_topicos.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    moderador_id = bd.Column(
        U_INT,
        bd.ForeignKey("funcionarios.id"),
        nullable=False,
        index=True
    )
    autor_id = bd.Column(
        U_BIGINT,
        bd.ForeignKey("usuarios.id"),
        nullable=False,
        index=True
    )

    acao = bd.Column(bd.String(30), nullable=False, index=True)
    motivo = bd.Column(bd.Text, nullable=False)
    detalhes = bd.Column(bd.Text, nullable=True)

    criado_em = bd.Column(bd.DateTime, nullable=False, default=agora_utc, index=True)

    topico = bd.relationship(
        "ForumTopico",
        backref=bd.backref("logs_moderacao", lazy="dynamic", cascade="all, delete-orphan")
    )

    moderador = bd.relationship("Funcionario", foreign_keys=[moderador_id])
    autor = bd.relationship("Usuario", foreign_keys=[autor_id])