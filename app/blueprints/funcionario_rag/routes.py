from functools import wraps
import os
import time
import requests

from flask import (
    render_template,
    request,
    jsonify,
    session,
    redirect,
    url_for,
)

from . import funcionario_rag_bp


RAG_API_URL = os.getenv(
    "RAG_API_URL",
    "http://192.168.0.10:8010"
    #"http://100.105.74.61:8010"
)


def funcionario_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("funcionario_id"):
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


def primeiro_valor_valido(*valores):
    for valor in valores:
        if valor is not None and valor != "":
            return valor
    return None


def converter_inteiro_ou_none(valor):
    if valor is None or valor == "":
        return None

    if isinstance(valor, list):
        return len(valor)

    if isinstance(valor, tuple):
        return len(valor)

    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def estatisticas_padrao(
    tempo_ms=0,
    modelo="—",
    prompt_tokens=None,
    completion_tokens=None,
    total_tokens=None,
    documentos_usados=None
):
    return {
        "tempo_ms": round(float(tempo_ms or 0), 2),
        "modelo": modelo or "—",
        "prompt_tokens": converter_inteiro_ou_none(prompt_tokens),
        "completion_tokens": converter_inteiro_ou_none(completion_tokens),
        "total_tokens": converter_inteiro_ou_none(total_tokens),
        "documentos_usados": converter_inteiro_ou_none(documentos_usados),
    }


def extrair_estatisticas_do_payload(payload, tempo_ms):
    """
    Tenta aproveitar estatísticas que vierem do servidor RAG.
    Se tokens/docs não vierem, mantém None para o frontend exibir "Não retornado".
    """

    estatisticas = (
        payload.get("estatisticas")
        or payload.get("stats")
        or payload.get("metrics")
        or payload.get("usage")
        or {}
    )

    modelo = primeiro_valor_valido(
        estatisticas.get("modelo"),
        estatisticas.get("model"),
        estatisticas.get("llm_model"),
        estatisticas.get("modelo_llm"),
        estatisticas.get("model_name"),
        payload.get("modelo"),
        payload.get("model"),
        payload.get("llm_model"),
        payload.get("modelo_llm"),
        payload.get("model_name"),
        "RAG remoto"
    )

    prompt_tokens = primeiro_valor_valido(
        estatisticas.get("prompt_tokens"),
        estatisticas.get("input_tokens"),
        estatisticas.get("tokens_prompt"),
        estatisticas.get("promptTokens"),
        estatisticas.get("inputTokens"),
        payload.get("prompt_tokens"),
        payload.get("input_tokens"),
        payload.get("tokens_prompt"),
        payload.get("promptTokens"),
        payload.get("inputTokens"),
    )

    completion_tokens = primeiro_valor_valido(
        estatisticas.get("completion_tokens"),
        estatisticas.get("output_tokens"),
        estatisticas.get("tokens_completion"),
        estatisticas.get("completionTokens"),
        estatisticas.get("outputTokens"),
        payload.get("completion_tokens"),
        payload.get("output_tokens"),
        payload.get("tokens_completion"),
        payload.get("completionTokens"),
        payload.get("outputTokens"),
    )

    total_tokens = primeiro_valor_valido(
        estatisticas.get("total_tokens"),
        estatisticas.get("tokens_total"),
        estatisticas.get("totalTokens"),
        payload.get("total_tokens"),
        payload.get("tokens_total"),
        payload.get("totalTokens"),
    )

    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)

    documentos_usados = primeiro_valor_valido(
        estatisticas.get("documentos_usados"),
        estatisticas.get("docs_usados"),
        estatisticas.get("contextos_usados"),
        estatisticas.get("fontes_usadas"),
        estatisticas.get("documentos_recuperados"),
        payload.get("documentos_usados"),
        payload.get("docs_usados"),
        payload.get("contextos_usados"),
        payload.get("fontes_usadas"),
        payload.get("documentos_recuperados"),
    )

    if documentos_usados is None:
        for chave in (
            "documentos",
            "docs",
            "fontes",
            "contextos",
            "sources",
            "chunks",
            "documentos_recuperados",
        ):
            valor = payload.get(chave)

            if isinstance(valor, list):
                documentos_usados = len(valor)
                break

            if isinstance(valor, tuple):
                documentos_usados = len(valor)
                break

    return estatisticas_padrao(
        tempo_ms=tempo_ms,
        modelo=modelo,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        documentos_usados=documentos_usados
    )


@funcionario_rag_bp.get("/")
@funcionario_required
def painel_rag():
    return render_template("funcionario/estatisticas.html")


@funcionario_rag_bp.post("/perguntar")
@funcionario_required
def perguntar_rag():
    inicio = time.perf_counter()

    data = request.get_json(silent=True) or {}
    pergunta = (data.get("pergunta") or "").strip()
    historico = data.get("historico") or []

    if not isinstance(historico, list):
        historico = []

    historico = historico[-8:]

    if not pergunta:
        return jsonify({
            "ok": False,
            "tipo": "erro",
            "erro": "Digite uma pergunta.",
            "estatisticas": estatisticas_padrao(
                tempo_ms=0,
                modelo="—"
            )
        }), 400

    try:
        resp = requests.post(
            f"{RAG_API_URL}/rag/perguntar",
            json={
                "pergunta": pergunta,
                "historico": historico
            },
            timeout=120
        )

        fim = time.perf_counter()
        tempo_ms = round((fim - inicio) * 1000, 2)

        try:
            payload = resp.json()
        except Exception:
            return jsonify({
                "ok": False,
                "tipo": "erro",
                "erro": "O servidor RAG respondeu, mas não retornou JSON válido.",
                "estatisticas": estatisticas_padrao(
                    tempo_ms=tempo_ms,
                    modelo="RAG remoto"
                )
            }), 502

        if not isinstance(payload, dict):
            return jsonify({
                "ok": False,
                "tipo": "erro",
                "erro": "O servidor RAG retornou um JSON em formato inesperado.",
                "estatisticas": estatisticas_padrao(
                    tempo_ms=tempo_ms,
                    modelo="RAG remoto"
                )
            }), 502

        payload["estatisticas"] = extrair_estatisticas_do_payload(payload, tempo_ms)

        return jsonify(payload), resp.status_code

    except requests.exceptions.Timeout:
        fim = time.perf_counter()
        tempo_ms = round((fim - inicio) * 1000, 2)

        return jsonify({
            "ok": False,
            "tipo": "erro",
            "erro": "O RAG demorou demais para responder.",
            "estatisticas": estatisticas_padrao(
                tempo_ms=tempo_ms,
                modelo="RAG remoto"
            )
        }), 504

    except requests.exceptions.ConnectionError:
        fim = time.perf_counter()
        tempo_ms = round((fim - inicio) * 1000, 2)

        return jsonify({
            "ok": False,
            "tipo": "erro",
            "erro": "Não foi possível conectar ao servidor RAG.",
            "estatisticas": estatisticas_padrao(
                tempo_ms=tempo_ms,
                modelo="RAG remoto"
            )
        }), 502

    except Exception as e:
        fim = time.perf_counter()
        tempo_ms = round((fim - inicio) * 1000, 2)

        return jsonify({
            "ok": False,
            "tipo": "erro",
            "erro": f"Erro inesperado ao chamar o RAG: {e}",
            "estatisticas": estatisticas_padrao(
                tempo_ms=tempo_ms,
                modelo="RAG remoto"
            )
        }), 500


@funcionario_rag_bp.get("/metricas")
@funcionario_required
def metricas_rag():
    try:
        resp = requests.get(
            f"{RAG_API_URL}/health/metrics",
            timeout=5
        )

        try:
            payload = resp.json()
        except Exception:
            return jsonify({
                "ok": False,
                "erro": "O servidor RAG respondeu, mas não retornou JSON válido.",
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
            }), 502

        return jsonify(payload), resp.status_code

    except requests.exceptions.Timeout:
        return jsonify({
            "ok": False,
            "erro": "Timeout ao consultar métricas do servidor RAG.",
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
        }), 504

    except requests.exceptions.ConnectionError:
        return jsonify({
            "ok": False,
            "erro": "Não foi possível conectar ao servidor RAG.",
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
        }), 502

    except Exception as e:
        return jsonify({
            "ok": False,
            "erro": f"Erro inesperado ao consultar métricas do RAG: {e}",
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
        }), 500