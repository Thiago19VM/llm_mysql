from fastapi import FastAPI
from pydantic import BaseModel
import pandas as pd
import time
import psutil
import subprocess
import math
import numpy as np
import pandas as pd

from rag_core import ask

app = FastAPI(title="LiteraWEB RAG API")


class MensagemHistorico(BaseModel):
    role: str
    content: str


class PerguntaRequest(BaseModel):
    pergunta: str
    historico: list[MensagemHistorico] = []

def limpar_para_json(obj):
    if isinstance(obj, pd.DataFrame):
        df = obj.copy()
        df = df.replace([np.inf, -np.inf], np.nan)
        df = df.where(pd.notnull(df), None)
        return df.to_dict(orient="records")

    if isinstance(obj, dict):
        return {k: limpar_para_json(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [limpar_para_json(v) for v in obj]

    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj

    return obj

def primeiro_valor_valido(*valores):
    for valor in valores:
        if valor is not None and valor != "":
            return valor
    return None


def contar_documentos_usados(result):
    """
    Tenta descobrir quantos documentos/contextos foram usados pelo RAG.
    Depende do que a função ask() retorna.
    """

    possiveis_chaves = [
        "documentos_usados",
        "docs_usados",
        "contextos_usados",
        "fontes_usadas",
        "documentos_recuperados",
        "documentos",
        "docs",
        "fontes",
        "contextos",
        "sources",
        "chunks",
    ]

    for chave in possiveis_chaves:
        valor = result.get(chave)

        if isinstance(valor, list):
            return len(valor)

        if isinstance(valor, tuple):
            return len(valor)

        if isinstance(valor, int):
            return valor

    estatisticas = result.get("estatisticas") or {}
    valor = estatisticas.get("documentos_usados")

    if isinstance(valor, list):
        return len(valor)

    if isinstance(valor, tuple):
        return len(valor)

    if isinstance(valor, int):
        return valor

    return None


def extrair_tokens(result):
    """
    Tenta extrair tokens do retorno da função ask().
    Se ask() ainda não retornar tokens, os campos ficam como None.
    """

    usage = (
        result.get("usage")
        or result.get("token_usage")
        or result.get("tokens")
        or result.get("estatisticas")
        or result.get("metrics")
        or {}
    )

    prompt_tokens = primeiro_valor_valido(
        usage.get("prompt_tokens"),
        usage.get("input_tokens"),
        usage.get("tokens_prompt"),
        usage.get("promptTokens"),
        usage.get("inputTokens"),
        result.get("prompt_tokens"),
        result.get("input_tokens"),
        result.get("tokens_prompt"),
    )

    completion_tokens = primeiro_valor_valido(
        usage.get("completion_tokens"),
        usage.get("output_tokens"),
        usage.get("tokens_completion"),
        usage.get("completionTokens"),
        usage.get("outputTokens"),
        result.get("completion_tokens"),
        result.get("output_tokens"),
        result.get("tokens_completion"),
    )

    total_tokens = primeiro_valor_valido(
        usage.get("total_tokens"),
        usage.get("tokens_total"),
        usage.get("totalTokens"),
        result.get("total_tokens"),
        result.get("tokens_total"),
    )

    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)

    return prompt_tokens, completion_tokens, total_tokens


def montar_estatisticas(result, tempo_ms):
    prompt_tokens, completion_tokens, total_tokens = extrair_tokens(result)

    estatisticas_result = result.get("estatisticas") or {}

    modelo = primeiro_valor_valido(
        estatisticas_result.get("modelo"),
        estatisticas_result.get("model"),
        estatisticas_result.get("llm_model"),
        estatisticas_result.get("modelo_llm"),
        estatisticas_result.get("model_name"),
        result.get("modelo"),
        result.get("model"),
        result.get("llm_model"),
        result.get("modelo_llm"),
        result.get("model_name"),
        "Não retornado"
    )

    documentos_usados = contar_documentos_usados(result)

    return {
        "tempo_ms": round(float(tempo_ms or 0), 2),
        "modelo": modelo,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "documentos_usados": documentos_usados,
    }


def normalizar_historico_api(historico):
    """
    Converte o histórico recebido pela API em uma lista simples de dicts,
    limitada às últimas mensagens relevantes.
    """

    if not historico:
        return []

    historico_normalizado = []

    for msg in historico[-8:]:
        role = str(getattr(msg, "role", "") or "").strip().lower()
        content = str(getattr(msg, "content", "") or "").strip()

        if role not in ("user", "assistant"):
            continue

        if not content:
            continue

        historico_normalizado.append({
            "role": role,
            "content": content[:2000]
        })

    return historico_normalizado


@app.get("/health")
def health():
    return {"ok": True, "status": "RAG online"}


@app.get("/health/metrics")
def health_metrics():
    """
    Retorna métricas resumidas da máquina que roda o RAG:
    GPU, VRAM, temperatura da GPU, CPU, temperatura da CPU e RAM.
    """

    def obter_metricas_gpu():
        try:
            cmd = [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits"
            ]

            resultado = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3
            )

            if resultado.returncode != 0:
                return {
                    "disponivel": False,
                    "uso_percent": None,
                    "memoria_usada_mb": None,
                    "memoria_total_mb": None,
                    "temperatura_c": None,
                    "erro": "nvidia-smi não retornou dados."
                }

            linhas = resultado.stdout.strip().splitlines()
            if not linhas:
                return {
                    "disponivel": False,
                    "uso_percent": None,
                    "memoria_usada_mb": None,
                    "memoria_total_mb": None,
                    "temperatura_c": None,
                    "erro": "nvidia-smi retornou saída vazia."
                }

            linha = linhas[0]
            partes = [p.strip() for p in linha.split(",")]

            return {
                "disponivel": True,
                "uso_percent": float(partes[0]),
                "memoria_usada_mb": float(partes[1]),
                "memoria_total_mb": float(partes[2]),
                "temperatura_c": float(partes[3]),
                "erro": None
            }

        except Exception as erro:
            return {
                "disponivel": False,
                "uso_percent": None,
                "memoria_usada_mb": None,
                "memoria_total_mb": None,
                "temperatura_c": None,
                "erro": str(erro)
            }

    def obter_temperatura_cpu():
        try:
            sensores = psutil.sensors_temperatures()

            if not sensores:
                return None

            nomes_preferidos = [
                "coretemp",
                "k10temp",
                "cpu_thermal",
                "acpitz"
            ]

            for nome in nomes_preferidos:
                if nome in sensores and sensores[nome]:
                    temperaturas = [
                        item.current
                        for item in sensores[nome]
                        if item.current is not None
                    ]

                    if temperaturas:
                        return round(max(temperaturas), 1)

            for entradas in sensores.values():
                temperaturas = [
                    item.current
                    for item in entradas
                    if item.current is not None
                ]

                if temperaturas:
                    return round(max(temperaturas), 1)

            return None

        except Exception:
            return None

    try:
        memoria = psutil.virtual_memory()

        return {
            "ok": True,
            "status": "online",
            "gpu": obter_metricas_gpu(),
            "cpu": {
                "uso_percent": psutil.cpu_percent(interval=0.2),
                "temperatura_c": obter_temperatura_cpu()
            },
            "ram": {
                "uso_percent": memoria.percent,
                "usada_gb": round(memoria.used / (1024 ** 3), 2),
                "total_gb": round(memoria.total / (1024 ** 3), 2)
            }
        }

    except Exception as erro:
        return {
            "ok": False,
            "status": "erro",
            "erro": str(erro),
            "gpu": {
                "disponivel": False,
                "uso_percent": None,
                "memoria_usada_mb": None,
                "memoria_total_mb": None,
                "temperatura_c": None
            },
            "cpu": {
                "uso_percent": None,
                "temperatura_c": None
            },
            "ram": {
                "uso_percent": None,
                "usada_gb": None,
                "total_gb": None
            }
        }


@app.post("/rag/perguntar")
def perguntar(payload: PerguntaRequest):
    inicio = time.perf_counter()

    pergunta = (payload.pergunta or "").strip()
    historico = normalizar_historico_api(payload.historico)

    if not pergunta:
        return {
            "ok": False,
            "tipo": "erro",
            "erro": "Pergunta vazia.",
            "estatisticas": {
                "tempo_ms": 0,
                "modelo": "Não retornado",
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "documentos_usados": None,
            }
        }

    try:
        result = ask(
            pergunta,
            use_rag=True,
            historico=historico
        )

    except TypeError as erro:
        return {
            "ok": False,
            "tipo": "erro",
            "erro": (
                "O api_rag.py já está enviando histórico, mas a função ask() "
                "do rag_core.py ainda não aceita o parâmetro historico. "
                "Atualize a assinatura para: "
                "ask(SolicitacaoUsuario: str, use_rag: bool = True, historico: list[dict] | None = None). "
                f"Detalhe técnico: {erro}"
            ),
            "estatisticas": {
                "tempo_ms": round((time.perf_counter() - inicio) * 1000, 2),
                "modelo": "Não retornado",
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "documentos_usados": None,
            }
        }

    tempo_ms = round((time.perf_counter() - inicio) * 1000, 2)
    estatisticas = montar_estatisticas(result, tempo_ms)

    if result.get("error"):
        resposta_erro = {
            "ok": False,
            "tipo": "erro",
            "erro": result.get("error_user") or result.get("error"),
            "erro_tecnico": result.get("error"),
            "sql": result.get("sql", ""),
            "estatisticas": estatisticas,
            "timing": result.get("timing", {})
        }

        return limpar_para_json(resposta_erro)

    df = result.get("df")

    if isinstance(df, pd.DataFrame) and not df.empty:
    	df = df.replace([np.inf, -np.inf], np.nan)
    	df = df.astype(object).where(pd.notnull(df), None)

    	linhas = df.to_dict(orient="records")
    	colunas = list(df.columns)
    	tipo = "dados"
    else:
    	linhas = []
    	colunas = []
    	tipo = "conceitual"

    resposta_api = {
    	"ok": True,
    	"tipo": tipo,
    	"resposta": result.get("interpretacao", ""),
    	"sql": result.get("sql", ""),
    	"colunas": colunas,
    	"linhas": linhas,
    	"grafico": result.get("viz", {}),
    	"timing": result.get("timing", {}),
    	"estatisticas": estatisticas
    }

    return limpar_para_json(resposta_api)
