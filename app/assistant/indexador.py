#!/usr/bin/env python3
"""
indexador.py  —  v2 (DDL-aware)
Gera store.pkl com chunking inteligente para schema SQL:
  - Cada CREATE TABLE vira um chunk separado
  - Seção de REGRAS vira um chunk separado
  - Cada EXEMPLO de query vira um chunk separado
  - Chunks pequenos demais são agrupados

Execute uma vez antes de subir o app:
    python indexador.py

Ao atualizar o schema_context.txt, rode novamente para regenerar o índice.
"""

import pickle
import re
import numpy as np
import torch
import faiss
from sentence_transformers import SentenceTransformer
from extensao import *

EMBED_MODEL  = "sentence-transformers/multi-qa-mpnet-base-dot-v1"
SCHEMA_FILE = "/home/llm/thiago/esquema/schema_tickets.txt"
STORE_FILE  = "/home/llm/thiago/esquema/store_tickets.pkl"
MIN_CHUNK_WORDS = 50


def split_sql_schema(text: str) -> list[str]:
    """
    Divide o schema_????????.txt em chunks semânticos:
      1. Cabeçalho / comentários iniciais
      2. Um chunk por CREATE TABLE (DDL completo da tabela)
      3. Chunk das REGRAS CRÍTICAS
      4. Um chunk por EXEMPLO de query (EX-N)
    """
    chunks = []

    # ── 1. Separa por bloco CREATE TABLE ─────────────────────────────────────
    # Cada bloco vai desde "-- ==...== \n-- DIMENSÃO X" até o próximo separador
    table_pattern = re.compile(
        r"(-- =+\n-- (?:TABELA|DIMENSÃO|DIMENSAO)[^\n]*\n-- =+.*?(?=-- =+\n-- (?:TABELA|DIMENSÃO|DIMENSAO|REGRAS|ALIASES|EXEMPLOS)|$))",
        re.DOTALL | re.IGNORECASE,
    )

    # ── 2. Separa REGRAS CRÍTICAS ─────────────────────────────────────────────
    rules_pattern = re.compile(
        r"(-- =+\n-- REGRAS CRÍTICAS.*?(?=-- =+\n-- (?:ALIASES|EXEMPLOS)|$))",
        re.DOTALL | re.IGNORECASE,
    )

    # ── 3. Separa cada EXEMPLO ────────────────────────────────────────────────
    example_pattern = re.compile(
        r"(-- EX-\d+:.*?(?=-- EX-\d+:|$))",
        re.DOTALL | re.IGNORECASE,
    )

    # Extrai tabelas
    for m in table_pattern.finditer(text):
        chunk = m.group(1).strip()
        if chunk:
            chunks.append(chunk)

    # Extrai regras
    for m in rules_pattern.finditer(text):
        chunk = m.group(1).strip()
        if chunk:
            chunks.append(chunk)

    # Extrai exemplos
    for m in example_pattern.finditer(text):
        chunk = m.group(1).strip()
        if chunk:
            chunks.append(chunk)

    # Fallback: se os regex não encontraram nada, faz chunking simples por palavras
    if not chunks:
        print("⚠️  Regex DDL não encontrou blocos — usando chunking por palavras (fallback)")
        words = text.split()
        step  = 420  # ~500 palavras com overlap de 80
        for i in range(0, len(words), step):
            c = " ".join(words[i : i + 500])
            if c.strip():
                chunks.append(c)
        return chunks

    # Agrupa chunks muito pequenos com o anterior
    merged = []
    buffer = ""
    for c in chunks:
        if len(c.split()) < MIN_CHUNK_WORDS and buffer:
            buffer += "\n\n" + c
        else:
            if buffer:
                merged.append(buffer)
            buffer = c
    if buffer:
        merged.append(buffer)

    return merged


def build_index():
    print(f"📄 Lendo {SCHEMA_FILE}...")
    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        raw = f.read()
    print(f"   {len(raw)} caracteres lidos")

    print("✂️  Dividindo em chunks semânticos...")
    chunks = split_sql_schema(raw)
    print(f"   ✅ {len(chunks)} chunks gerados")

    for i, c in enumerate(chunks):
        preview = c[:80].replace("\n", " ")
        print(f"   [{i:02d}] {len(c.split()):>4} palavras | {preview}...")

    print("\n🔢 Gerando embeddings...")
    embedder = SentenceTransformer(EMBED_MODEL, device="cuda" if torch.cuda.is_available() else "cpu")
    vectors = embedder.encode(chunks, show_progress_bar=True, normalize_embeddings=True)

    vectors = np.array(vectors, dtype="float32")

    print("📦 Construindo índice FAISS (IndexFlatIP = cosine similarity)...")
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)

    with open(STORE_FILE, "wb") as f:
        pickle.dump({"index": index, "chunks": chunks}, f)

    print(f"\n✅ Índice salvo em '{STORE_FILE}'")
    print(f"   {index.ntotal} vetores | dimensão {vectors.shape[1]}")
    print("\nPróximo passo: python app.py")


if __name__ == "__main__":
    build_index()
