from sqlalchemy import text
from app import create_app, bd

app = create_app()

with app.app_context():
    resultado = bd.session.execute(text("SELECT 1")).scalar()
    print("Conexão OK:", resultado)