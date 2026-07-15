from flask import Blueprint

funcionario_bp = Blueprint("funcionario", __name__, url_prefix="")

from . import routes
from . import routes_politicas
from . import estatisticas_routes