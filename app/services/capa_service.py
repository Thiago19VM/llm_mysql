from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import requests
from flask import current_app

from app.extensions import bd
from app.models.livro import Livro


TIMEOUT = 15


def limpar_isbn(valor: str | None) -> str:
    if not valor:
        return ""
    return re.sub(r"[^0-9Xx]", "", str(valor)).upper()


def normalizar_texto(valor: str | None) -> str:
    if not valor:
        return ""
    valor = valor.lower().strip()
    valor = re.sub(r"[^\w\s]", " ", valor, flags=re.UNICODE)
    valor = re.sub(r"\s+", " ", valor)
    return valor


def slug_basico(texto: str) -> str:
    texto = (texto or "").strip().lower()
    texto = re.sub(r"[^\w\s-]", "", texto, flags=re.UNICODE)
    texto = re.sub(r"[-\s]+", "_", texto)
    return texto[:150] or "livro"


def nome_arquivo_capa(livro: Livro) -> str:
    titulo = getattr(livro, "titulo", "") or ""
    autor = getattr(livro, "autor", "") or ""
    return f"{slug_basico(f'{titulo}__{autor}')}.jpg"


def garantir_pasta_capas() -> Path:
    pasta = Path(current_app.root_path) / "static" / "uploads" / "capas"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def baixar_imagem(url: str, destino: Path) -> bool:
    try:
        resp = requests.get(
            url,
            timeout=TIMEOUT,
            headers={"User-Agent": "LiteraWEB/1.0"},
        )

        if resp.status_code != 200 or not resp.content:
            return False

        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "image" not in content_type:
            return False

        destino.write_bytes(resp.content)
        return True

    except requests.RequestException:
        return False


def tentar_openlibrary_por_isbn(isbn: str, destino: Path) -> bool:
    if not isbn:
        return False

    url = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
    return baixar_imagem(url, destino)


def _score_resultado_openlibrary(
    doc: dict,
    titulo_ref: str,
    autor_ref: str,
) -> int:
    score = 0

    titulo_doc = normalizar_texto(doc.get("title"))
    autores_doc = [normalizar_texto(a) for a in (doc.get("author_name") or [])]

    if titulo_doc and titulo_ref:
        if titulo_doc == titulo_ref:
            score += 60
        elif titulo_ref in titulo_doc or titulo_doc in titulo_ref:
            score += 35

    if autor_ref and autores_doc:
        if any(a == autor_ref for a in autores_doc):
            score += 40
        elif any(autor_ref in a or a in autor_ref for a in autores_doc):
            score += 20

    if doc.get("cover_i"):
        score += 15

    if doc.get("first_publish_year"):
        score += 5

    return score


def buscar_cover_id_por_titulo_autor(titulo: str, autor: str) -> Optional[int]:
    titulo_ref = normalizar_texto(titulo)
    autor_ref = normalizar_texto(autor)

    if not titulo_ref:
        return None

    try:
        resp = requests.get(
            "https://openlibrary.org/search.json",
            params={
                "title": titulo,
                "author": autor,
                "fields": "title,author_name,cover_i,first_publish_year",
                "limit": 10,
            },
            timeout=TIMEOUT,
            headers={"User-Agent": "LiteraWEB/1.0"},
        )

        if resp.status_code != 200:
            return None

        data = resp.json()
        docs = data.get("docs") or []
        if not docs:
            return None

        melhor_doc = None
        melhor_score = -1

        for doc in docs:
            if not doc.get("cover_i"):
                continue

            score = _score_resultado_openlibrary(doc, titulo_ref, autor_ref)
            if score > melhor_score:
                melhor_score = score
                melhor_doc = doc

        if not melhor_doc:
            return None

        cover_i = melhor_doc.get("cover_i")
        if isinstance(cover_i, int):
            return cover_i

        try:
            return int(cover_i)
        except (TypeError, ValueError):
            return None

    except requests.RequestException:
        return None


def tentar_openlibrary_por_titulo_autor(
    titulo: str,
    autor: str,
    destino: Path,
) -> bool:
    cover_id = buscar_cover_id_por_titulo_autor(titulo=titulo, autor=autor)
    if not cover_id:
        return False

    url = f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg?default=false"
    return baixar_imagem(url, destino)


def atualizar_capa_livro(livro: Livro, nome_arquivo: str) -> None:
    livro.capa_arquivo = f"uploads/capas/{nome_arquivo}"
    bd.session.add(livro)


def baixar_capa_para_livro(livro: Livro, sobrescrever: bool = False) -> dict:
    if not livro:
        return {"ok": False, "motivo": "livro_invalido"}

    if getattr(livro, "capa_arquivo", None) and not sobrescrever:
        return {"ok": False, "motivo": "ja_tem_capa"}

    pasta = garantir_pasta_capas()
    nome_arquivo = nome_arquivo_capa(livro)
    destino = pasta / nome_arquivo

    titulo = getattr(livro, "titulo", "") or ""
    autor = getattr(livro, "autor", "") or ""
    isbn = limpar_isbn(getattr(livro, "isbn", None))

    # 1) tenta pelo ISBN
    if isbn:
        ok = tentar_openlibrary_por_isbn(isbn, destino)
        if ok:
            atualizar_capa_livro(livro, nome_arquivo)
            return {
                "ok": True,
                "motivo": "capa_baixada",
                "fonte": "openlibrary_isbn",
                "arquivo": nome_arquivo,
            }

    # 2) fallback por título + autor
    ok = tentar_openlibrary_por_titulo_autor(titulo=titulo, autor=autor, destino=destino)
    if ok:
        atualizar_capa_livro(livro, nome_arquivo)
        return {
            "ok": True,
            "motivo": "capa_baixada",
            "fonte": "openlibrary_titulo_autor",
            "arquivo": nome_arquivo,
        }

    return {"ok": False, "motivo": "capa_nao_encontrada"}


def baixar_capas_em_lote(
    limite: int | None = None,
    sobrescrever: bool = False,
    somente_sem_capa: bool = True,
) -> dict:
    query = Livro.query.order_by(Livro.id.asc())

    if somente_sem_capa:
        query = query.filter(
            (Livro.capa_arquivo.is_(None)) | (Livro.capa_arquivo == "")
        )

    if limite:
        query = query.limit(limite)

    livros = query.all()

    total = 0
    sucesso = 0
    falhas = []

    for livro in livros:
        total += 1
        resultado = baixar_capa_para_livro(livro, sobrescrever=sobrescrever)

        if resultado["ok"]:
            sucesso += 1
        else:
            falhas.append({
                "id": livro.id,
                "titulo": livro.titulo,
                "motivo": resultado["motivo"],
            })

    bd.session.commit()

    return {
        "total_processados": total,
        "sucesso": sucesso,
        "falhas": falhas,
    }