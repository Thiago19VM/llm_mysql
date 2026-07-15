# app/blueprints/forum/__init__.py
from flask import Blueprint

forum_bp = Blueprint("forum", __name__, url_prefix="/forum")

from . import routes  # noqa
from . import moderacao_routes