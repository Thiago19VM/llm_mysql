from flask import Blueprint

funcionario_rag_bp = Blueprint(
    "funcionario_rag",
    __name__,
    url_prefix="/funcionario/rag"
)

from . import routes