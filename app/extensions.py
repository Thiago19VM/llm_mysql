from flask_sqlalchemy import SQLAlchemy
from authlib.integrations.flask_client import OAuth

bd = SQLAlchemy()
oauth = OAuth()