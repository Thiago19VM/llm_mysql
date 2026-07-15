#!/usr/bin/env python3
"""
rag_core.py — LiteraWEB RAG API Core
vLLM Server + OpenAI Client | MySQL via SQLAlchemy
"""

from openai import OpenAI
import os
import re
import time
import pickle
import warnings
import numpy as np
import pandas as pd
import torch
from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sentence_transformers import SentenceTransformer
from wcwidth import wcswidth
from rag_logger import GerarLog
from extensao import *


# ══════════════════════════════════════════════════════════════════════════════
#  Utilitários
# ══════════════════════════════════════════════════════════════════════════════

def Box(msg: str):
    LarguraVisual = wcswidth(msg)
    w = LarguraVisual + 4
    GerarLog("┌" + "─" * w + "┐")
    GerarLog("│  " + msg + "  │")
    GerarLog("└" + "─" * w + "┘")


def nova_estatistica_llm() -> dict:
    return {
        "modelo": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "documentos_usados": None,
    }


def _somar_valor_atual(valor_atual, valor_novo):
    if valor_novo is None:
        return valor_atual

    try:
        valor_novo = int(valor_novo)
    except (TypeError, ValueError):
        return valor_atual

    if valor_atual is None:
        return valor_novo

    try:
        return int(valor_atual) + valor_novo
    except (TypeError, ValueError):
        return valor_novo


def estimar_tokens_por_texto(texto: str) -> int:
    """
    Estimativa simples caso o servidor vLLM/OpenAI-compatible não retorne usage.
    Aproximação comum: 1 token ~= 4 caracteres.
    """
    if not texto:
        return 0

    return max(1, len(str(texto)) // 4)


def estimar_prompt_tokens(messages: list[dict]) -> int:
    total_chars = 0

    for msg in messages or []:
        total_chars += len(str(msg.get("role", "")))
        total_chars += len(str(msg.get("content", "")))

    return max(1, total_chars // 4)


def registrar_usage_llm(estatisticas: dict | None, response, messages: list[dict] | None = None, raw: str = "") -> None:
    """
    Registra tokens reais quando o vLLM retorna response.usage.
    Se não retornar usage, usa estimativa para não deixar tokens zerados.
    """
    if estatisticas is None:
        return

    if not estatisticas.get("modelo"):
        estatisticas["modelo"] = LLM_NAME

    usage = getattr(response, "usage", None)

    if usage:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)

        estatisticas["prompt_tokens"] = _somar_valor_atual(
            estatisticas.get("prompt_tokens"),
            prompt_tokens
        )

        estatisticas["completion_tokens"] = _somar_valor_atual(
            estatisticas.get("completion_tokens"),
            completion_tokens
        )

        estatisticas["total_tokens"] = _somar_valor_atual(
            estatisticas.get("total_tokens"),
            total_tokens
        )

        return

    prompt_estimado = estimar_prompt_tokens(messages or [])
    completion_estimado = estimar_tokens_por_texto(raw)
    total_estimado = prompt_estimado + completion_estimado

    estatisticas["prompt_tokens"] = _somar_valor_atual(
        estatisticas.get("prompt_tokens"),
        prompt_estimado
    )

    estatisticas["completion_tokens"] = _somar_valor_atual(
        estatisticas.get("completion_tokens"),
        completion_estimado
    )

    estatisticas["total_tokens"] = _somar_valor_atual(
        estatisticas.get("total_tokens"),
        total_estimado
    )


# ══════════════════════════════════════════════════════════════════════════════
#  RAG
# ══════════════════════════════════════════════════════════════════════════════

def _load_store():
    with open(STORE_FILE, "rb") as f:
        s = pickle.load(f)

    return s["index"], s["chunks"]


def retrieve_context_info(SolicitacaoUsuario: str) -> tuple[str, int]:
    index, chunks = _load_store()

    q_vec = np.array(
        embedder.encode([SolicitacaoUsuario], normalize_embeddings=True),
        dtype="float32"
    )

    _, ids = index.search(q_vec, RAG_TOP_K)

    chunks_recuperados = [
        chunks[i]
        for i in ids[0]
        if i < len(chunks)
    ]

    context = "\n\n---\n\n".join(chunks_recuperados)

    return context, len(chunks_recuperados)


def retrieve_context(SolicitacaoUsuario: str) -> str:
    context, _ = retrieve_context_info(SolicitacaoUsuario)
    return context


# ══════════════════════════════════════════════════════════════════════════════
#  Chamada à LLM via vLLM OpenAI-compatible
# ══════════════════════════════════════════════════════════════════════════════

def ChamarLLM(
    messages: list[dict],
    max_new_tokens: int = 5000,
    temperature=0.1,
    estatisticas: dict | None = None
) -> str:
    """
    Chama o servidor vLLM em modo OpenAI-compatible.
    Também registra uso de tokens em estatisticas.
    """
    Box(" Chamando vLLM server")

    try:
        response = client.chat.completions.create(
            model=LLM_NAME,
            messages=messages,
            max_tokens=max_new_tokens,
            temperature=temperature,
            top_p=1.0,
            stream=False,
            extra_body={"repetition_penalty": 1.1},
        )

        raw = response.choices[0].message.content.strip()

        registrar_usage_llm(
            estatisticas=estatisticas,
            response=response,
            messages=messages,
            raw=raw
        )

        GerarLog(f"   ✅ vLLM retornou {len(raw)} chars")

        usage = getattr(response, "usage", None)

        if usage:
            GerarLog(
                "   🔢 tokens reais "
                f"prompt={getattr(usage, 'prompt_tokens', None)} "
                f"completion={getattr(usage, 'completion_tokens', None)} "
                f"total={getattr(usage, 'total_tokens', None)}"
            )
        elif estatisticas:
            GerarLog(
                "   🔢 tokens estimados "
                f"prompt={estatisticas.get('prompt_tokens')} "
                f"completion={estatisticas.get('completion_tokens')} "
                f"total={estatisticas.get('total_tokens')}"
            )

        return raw

    except Exception as e:
        GerarLog(f"   ❌ Erro na chamada ao vLLM: {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
#  Extração do SQL final
# ══════════════════════════════════════════════════════════════════════════════

def LimparSQLExtraido(sql: str) -> str:
    if not sql:
        return ""

    sql = str(sql).strip()

    # Remove tags XML usadas pelo prompt
    sql = re.sub(r"</?sql>", "", sql, flags=re.IGNORECASE).strip()
    sql = re.sub(r"</?rascunho>", "", sql, flags=re.IGNORECASE).strip()

    # Remove blocos markdown no começo e no fim
    sql = re.sub(r"^\s*```(?:sql|mysql)?\s*", "", sql, flags=re.IGNORECASE).strip()
    sql = re.sub(r"\s*```\s*$", "", sql).strip()

    # Remove qualquer sobra de markdown no meio
    sql = sql.replace("```sql", "")
    sql = sql.replace("```SQL", "")
    sql = sql.replace("```mysql", "")
    sql = sql.replace("```MYSQL", "")
    sql = sql.replace("```", "")

    # Remove comentários/explicações antes do SELECT ou WITH, se houver
    m = re.search(r"\b(WITH|SELECT)\b", sql, flags=re.IGNORECASE)
    if m:
        sql = sql[m.start():].strip()

    return sql.strip()


def ExtrairSqlFinal(raw: str) -> tuple[str, str]:
    m = re.search(r"<sql>(.*?)</sql>", raw, re.DOTALL | re.IGNORECASE)
    sql_final = m.group(1).strip() if m else ""

    if not sql_final:
        blocks = re.findall(r"```sql\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
        if blocks:
            sql_final = blocks[-1].strip()
            GerarLog("   ⚠️  Fallback: SQL extraído de bloco ```sql")

    r = re.search(r"<rascunho>(.*?)</rascunho>", raw, re.DOTALL | re.IGNORECASE)
    rascunho = r.group(1).strip() if r else ""

    if not rascunho:
        blocks = re.findall(r"```sql\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
        if blocks:
            rascunho = blocks[0].strip()

    sql_final = LimparSQLExtraido(sql_final)
    rascunho = LimparSQLExtraido(rascunho)

    return sql_final, rascunho

def CorrigirCTEPerdida(sql_final: str, rascunho: str) -> str:
    """
    Se o modelo criou uma CTE no rascunho, mas colocou no <sql> final
    apenas o SELECT que depende dessa CTE, usa o rascunho completo.
    """
    if not sql_final or not rascunho:
        return sql_final

    sql_final_limpo = LimparSQLExtraido(sql_final)
    rascunho_limpo = LimparSQLExtraido(rascunho)

    if not rascunho_limpo.upper().startswith("WITH"):
        return sql_final_limpo

    if sql_final_limpo.upper().startswith("WITH"):
        return sql_final_limpo

    ctes = re.findall(
        r"(?:WITH|,)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+AS\s*\(",
        rascunho_limpo,
        flags=re.IGNORECASE
    )

    for cte in ctes:
        if re.search(rf"\bFROM\s+{re.escape(cte)}\b", sql_final_limpo, re.IGNORECASE) or \
           re.search(rf"\bJOIN\s+{re.escape(cte)}\b", sql_final_limpo, re.IGNORECASE):
            GerarLog(
                f"   ⚠️ SQL final dependia da CTE '{cte}', mas perdeu o WITH. "
                "Usando rascunho completo como SQL final."
            )
            return rascunho_limpo

    return sql_final_limpo

# ══════════════════════════════════════════════════════════════════════════════
#  Conversa, histórico e perguntas de acompanhamento
# ══════════════════════════════════════════════════════════════════════════════

def NormalizarHistorico(historico: list[dict] | None, limite: int = 8) -> list[dict]:
    """
    Mantém apenas as últimas mensagens úteis da conversa.
    Isso evita prompt grande demais e reduz risco de conteúdo inválido.
    """
    if not historico:
        return []

    normalizado = []

    for msg in historico[-limite:]:
        if not isinstance(msg, dict):
            continue

        role = str(msg.get("role", "")).strip().lower()
        content = str(msg.get("content", "")).strip()

        if role not in ("user", "assistant"):
            continue

        if not content:
            continue

        normalizado.append({
            "role": role,
            "content": content[:2000]
        })

    return normalizado


def HistoricoParaTexto(historico: list[dict] | None, limite: int = 8) -> str:
    historico = NormalizarHistorico(historico, limite=limite)

    if not historico:
        return "Sem histórico recente."

    linhas = []

    for msg in historico:
        papel = "Funcionário" if msg["role"] == "user" else "Assistente"
        linhas.append(f"{papel}: {msg['content']}")

    return "\n".join(linhas)


def EhPerguntaDeAcompanhamento(pergunta: str, historico: list[dict] | None = None) -> bool:
    """
    Detecta perguntas que dependem da conversa anterior.
    Exemplos: "por quê?", "como assim?", "e se for 10 dias?", "isso vale para usuários?".
    """
    if not historico:
        return False

    q = (pergunta or "").strip().lower()
    q_sem = re.sub(r"[?!\.\s]+$", "", q).strip()

    if not q_sem:
        return False

    gatilhos_exatos = {
        "por que",
        "porque",
        "por quê",
        "porquê",
        "pq",
        "como assim",
        "explique",
        "explique melhor",
        "detalhe",
        "detalhe melhor",
        "não entendi",
        "nao entendi",
        "e isso",
        "e esse",
        "e essa",
        "isso",
        "essa regra",
        "esse caso",
        "me explique",
        "me explica",
    }

    if q_sem in gatilhos_exatos:
        return True

    gatilhos_inicio = (
        "e se ",
        "e para ",
        "e quanto ",
        "mas ",
        "então ",
        "entao ",
        "nesse caso",
        "neste caso",
        "com isso",
        "isso vale",
        "essa regra vale",
    )

    if q_sem.startswith(gatilhos_inicio):
        return True

    # Perguntas muito curtas costumam depender do assunto anterior.
    if len(q_sem.split()) <= 5:
        return True

    pronomes_referencia = (
        "isso",
        "essa",
        "esse",
        "ele",
        "ela",
        "eles",
        "elas",
        "tal regra",
        "esse valor",
        "essa política",
        "essa politica",
        "essa resposta",
        "o anterior",
    )

    return any(p in q_sem for p in pronomes_referencia)


def ReescreverPerguntaComHistorico(
    pergunta: str,
    historico: list[dict] | None = None,
    estatisticas: dict | None = None
) -> str:
    """
    Reescreve somente perguntas de acompanhamento, para que o RAG recupere
    contexto usando uma pergunta completa em vez de termos soltos como "por quê?".
    """
    pergunta_limpa = (pergunta or "").strip()

    if not EhPerguntaDeAcompanhamento(pergunta_limpa, historico):
        return pergunta_limpa

    historico_txt = HistoricoParaTexto(historico, limite=8)

    messages = [
        {
            "role": "system",
            "content": """
Você reescreve perguntas de acompanhamento para ficarem completas e independentes.

REGRAS:
- Não responda à pergunta.
- Não gere SQL.
- Não explique nada.
- Use o histórico apenas para resolver referências como "por quê?", "isso", "essa regra", "ele", "ela".
- O histórico serve apenas para entender a referência da pergunta atual; ele não é fonte de verdade sobre dados, regras ou resultados.
- Não trate afirmações do funcionário como verdade absoluta.
- Se o funcionário corrigir uma resposta anterior, preserve a ideia de verificação na pergunta reescrita.
- Se o funcionário disser "você inventou", "isso está errado", "não veio do banco" ou algo semelhante, reescreva como um pedido de verificação/correção, não como confirmação do dado.
- Não adicione fatos novos.
- Não adicione nomes, números, livros, usuários, datas, status ou regras que não estejam claramente na pergunta atual ou no histórico.
- Não complete a pergunta com exemplos externos.
- Não use conhecimento geral para preencher lacunas.
- Preserve a intenção original do funcionário.
- Retorne somente a pergunta reescrita.
- Use português do Brasil.
"""
        },
        {
            "role": "user",
            "content": f"""
HISTÓRICO RECENTE:
{historico_txt}

PERGUNTA ATUAL:
{pergunta_limpa}

Reescreva a pergunta atual para ficar completa e independente.
"""
        }
    ]

    reescrita = ChamarLLM(
        messages,
        max_new_tokens=180,
        temperature=0.0,
        estatisticas=estatisticas
    )

    reescrita = (reescrita or "").strip()

    if not reescrita:
        return pergunta_limpa

    # Limpa prefixos comuns caso o modelo insista em rotular a saída.
    reescrita = re.sub(r"^Pergunta reescrita:\s*", "", reescrita, flags=re.IGNORECASE).strip()
    reescrita = re.sub(r"^Pergunta:\s*", "", reescrita, flags=re.IGNORECASE).strip()

    # Evita que uma falha da LLM injete resposta longa no lugar da pergunta.
    if len(reescrita) > 500:
        return pergunta_limpa

    return reescrita


# ══════════════════════════════════════════════════════════════════════════════
#  Classificação e resposta conceitual
# ══════════════════════════════════════════════════════════════════════════════

def ClassificarPergunta(SolicitacaoUsuario: str) -> str:
    """
    Decide se a pergunta deve consultar o banco (dados) ou ser respondida
    pelas regras/documentação recuperadas (conceitual).

    Regra principal:
    - Se a pergunta pede registros, contagens, listas, rankings, gráficos,
      estado atual ou informações existentes no sistema, trata como DADOS.
    - Se a pergunta pede explicação, regra, significado ou funcionamento,
      trata como CONCEITUAL.
    """
    q = (SolicitacaoUsuario or "").lower().strip()

    if not q:
        return "conceitual"

    # Normalização simples para reduzir variações
    q_norm = re.sub(r"\s+", " ", q)

    # 1) Perguntas claramente conceituais
    gatilhos_conceituais_fortes = [
        "como funciona",
        "explique",
        "me explique",
        "me explica",
        "o que é",
        "o que significa",
        "qual o significado",
        "qual é o significado",
        "qual a regra",
        "quais as regras",
        "quais são as regras",
        "quando uma reserva é considerada",
        "quando um usuário é considerado",
        "quando um usuario é considerado",
        "em que momento",
        "de que forma",
        "como é",
        "como são",
        "como sao",
        "funcionamento",
        "fluxo",
        "processo",
        "significado",
        "definição",
        "definicao",
        "para que serve",
        "por que",
        "porque",
        "por quê",
        "pq",
        "qual a diferença",
        "qual é a diferença",
        "diferença entre",
        "diferenca entre",
    ]

    if any(g in q_norm for g in gatilhos_conceituais_fortes):
        return "conceitual"

    # 2) Pedidos explícitos de dados
    gatilhos_dados_fortes = [
        "quantos",
        "quantas",
        "quanto",
        "total",
        "média",
        "media",
        "percentual",
        "porcentagem",
        "ranking",
        "top",
        "listar",
        "liste",
        "lista",
        "mostre",
        "mostrar",
        "mostra",
        "traga",
        "retorne",
        "exiba",
        "exibir",
        "buscar",
        "busque",
        "consultar",
        "consulte",
        "consulta",
        "relatório",
        "relatorio",
        "gráfico",
        "grafico",
        "distribuição",
        "distribuicao",
        "comparar",
        "comparativo",
        "evolução",
        "evolucao",
        "tendência",
        "tendencia",
        "maiores",
        "menores",
        "mais emprestados",
        "menos emprestados",
        "mais reservados",
        "menos reservados",
        "mais comentados",
        "mais livros",
        "últimos",
        "ultimos",
        "últimas",
        "ultimas",
        "recentes",
    ]

    if any(g in q_norm for g in gatilhos_dados_fortes):
        return "dados"

    # 3) Perguntas começando com termos que normalmente pedem dados reais
    padroes_inicio_dados = [
        r"^quais\b",
        r"^qual\b",
        r"^quem\b",
        r"^onde\b",
    ]

    entidades_do_sistema = [
        "livro",
        "livros",
        "acervo",
        "exemplar",
        "exemplares",
        "usuário",
        "usuarios",
        "usuario",
        "usuários",
        "reserva",
        "reservas",
        "empréstimo",
        "emprestimo",
        "empréstimos",
        "emprestimos",
        "devolução",
        "devolucao",
        "devoluções",
        "devolucoes",
        "funcionário",
        "funcionario",
        "funcionários",
        "funcionarios",
        "política",
        "politica",
        "políticas",
        "politicas",
        "tópico",
        "topico",
        "tópicos",
        "topicos",
        "comentário",
        "comentario",
        "comentários",
        "comentarios",
        "fórum",
        "forum",
        "autor",
        "autores",
        "gênero",
        "genero",
        "gêneros",
        "generos",
        "status",
    ]

    comeca_com_pergunta_de_dados = any(
        re.search(padrao, q_norm)
        for padrao in padroes_inicio_dados
    )

    menciona_entidade_do_sistema = any(
        entidade in q_norm
        for entidade in entidades_do_sistema
    )

    if comeca_com_pergunta_de_dados and menciona_entidade_do_sistema:
        return "dados"

    # 4) Estado atual/vigente normalmente exige consulta ao banco
    gatilhos_estado_atual = [
        "atual",
        "atuais",
        "ativa",
        "ativo",
        "ativas",
        "ativos",
        "vigente",
        "vigentes",
        "em vigor",
        "configurada",
        "configurado",
        "configuradas",
        "configurados",
        "habilitada",
        "habilitado",
        "habilitadas",
        "habilitados",
        "em aberto",
        "pendente",
        "pendentes",
        "disponível",
        "disponivel",
        "disponíveis",
        "disponiveis",
        "indisponível",
        "indisponivel",
        "indisponíveis",
        "indisponiveis",
        "em atraso",
        "atrasado",
        "atrasada",
        "atrasados",
        "atrasadas",
        "bloqueado",
        "bloqueada",
        "bloqueados",
        "bloqueadas",
    ]

    if any(g in q_norm for g in gatilhos_estado_atual) and menciona_entidade_do_sistema:
        return "dados"

    # 5) Por padrão, no painel administrativo é melhor tentar consultar dados.
    # Isso evita respostas do tipo "você pode fazer uma SQL..." quando o usuário queria resultado.
    return "dados"

def ResponderAssistenteRAG(
    SolicitacaoUsuario: str,
    context: str,
    estatisticas: dict | None = None,
    historico: list[dict] | None = None,
    pergunta_original: str | None = None
) -> str:
    historico_txt = HistoricoParaTexto(historico, limite=8)
    pergunta_original = pergunta_original or SolicitacaoUsuario

    messages = [
        {
            "role": "system",
            "content": """
Você é o assistente inteligente do painel administrativo do LiteraWeb.

Seu papel é ajudar funcionários da biblioteca a entenderem o sistema, as regras e os dados.
Responda como um assistente humano, claro e útil.

ESTILO DE RESPOSTA:
- Responda em português do Brasil.
- Seja direto, mas natural.
- Comece pela resposta mais útil para a pergunta.
- Evite linguagem robótica.
- Não comece com frases como "Com base no contexto fornecido".
- Não mencione RAG, schema, DDL, prompt ou detalhes internos.
- Use nomes das tabelas e colunas apenas quando isso ajudar.
- Explique em linguagem de negócio primeiro, e só depois cite detalhes técnicos.
- Use parágrafos curtos.
- Use listas quando houver regras, etapas ou vários pontos importantes.
- Se a pergunta for conceitual, não gere SQL.
- Em perguntas conceituais ou de regra de negócio, explique em linguagem administrativa.
- Evite citar SQL, funções como CURDATE(), NOW(), DATE_FORMAT(), operadores como <, >, =, IN, ou expressões técnicas.
- Só cite nomes de tabelas, colunas ou SQL se o funcionário pedir detalhes técnicos.
- Quando o contexto trouxer uma regra em formato SQL, traduza para linguagem natural.

CONVERSA E HISTÓRICO:
- Use o histórico recente apenas para entender perguntas de acompanhamento.
- O histórico recente NÃO é fonte de verdade sobre regras, dados ou funcionalidades do sistema.
- Se o funcionário perguntar "por quê?", "como assim?", "explique melhor" ou algo parecido, explique a resposta anterior somente quando o contexto atual sustentar a explicação.
- Não confirme afirmações anteriores que não estejam apoiadas pelo contexto recuperado.
- Não diga que a pergunta está incompleta se o histórico recente permitir entender o assunto.
- Se mesmo com o histórico não der para entender a referência, peça uma reformulação curta.

COMO DOSAR O DETALHE:
- Se a pergunta for simples, responda curto.
- Se a pergunta pedir regras, funcionamento ou estrutura, explique com detalhes suficientes.
- Se a pergunta pedir campos de uma tabela, organize por grupos e explique o uso prático.
- Se a pergunta parecer pedir um dado atual do banco, não invente valores; diga de forma simples que é necessário consultar os dados atuais.

REGRAS CONTRA ALUCINAÇÃO EM RESPOSTAS CONCEITUAIS:
- Use o contexto fornecido como fonte de verdade.
- Não use conhecimento geral para completar regras do sistema.
- Não presuma funcionalidades comuns de outros sistemas de biblioteca.
- Não afirme que existe uma tela, botão, campo, status, regra, tabela ou coluna se isso não aparecer no contexto.
- Não invente canais de comunicação como e-mail, SMS, aplicativo, push notification ou alerta automático.
- Não invente permissões, níveis de acesso, perfis de usuário, prazos, limites, multas, bloqueios ou tolerâncias.
- Não invente tabelas, colunas, regras, valores, canais de notificação ou funcionalidades.
- Não diga que o sistema envia e-mail, notificação no site, aplicativo móvel ou alerta automático se isso não estiver explicitamente no contexto.
- Se o contexto não trouxer a regra solicitada, diga: "Não encontrei essa regra no material disponível do sistema."
- Se a pergunta envolver dados atuais do banco, não responda com números estimados.
- Diferencie regra documentada de hipótese.
- Nunca apresente hipótese como se fosse regra oficial do LiteraWeb.
- Nunca exponha hash_senha, google_id, senha ou dados sensíveis.
""",
        },
        {
            "role": "user",
            "content": f"""
HISTÓRICO RECENTE:
{historico_txt}

CONTEXTO DO LITERAWEB:
{context}

PERGUNTA ORIGINAL DO FUNCIONÁRIO:
{pergunta_original}

PERGUNTA USADA PARA BUSCA/RESPOSTA:
{SolicitacaoUsuario}

Responda de forma natural e útil para um funcionário administrativo, usando somente informações sustentadas pelo contexto.
""",
        },
    ]

    return ChamarLLM(
        messages,
        max_new_tokens=1400,
        temperature=0.20,
        estatisticas=estatisticas
    )


def ResumoFidelidadeDados(df: pd.DataFrame | None, limite_valores: int = 20) -> str:
    """
    Gera um resumo curto dos dados reais retornados pelo SQL para reforçar
    a fidelidade factual durante a etapa de polimento.
    """
    if df is None or df.empty:
        return "Nenhum dado tabular retornado."

    linhas = [
        f"Total de linhas retornadas pelo banco: {len(df)}",
        f"Colunas retornadas: {', '.join(map(str, df.columns.tolist()))}",
    ]

    for col in df.columns:
        try:
            valores = df[col].dropna().astype(str).head(limite_valores).tolist()
        except Exception:
            valores = []

        if valores:
            linhas.append(f"Valores reais em {col}: {valores}")

    return "\n".join(linhas)


def PolirResposta(
    resposta: str,
    pergunta: str,
    estatisticas: dict | None = None,
    metadados_dados: str | None = None
) -> str:
    if not resposta:
        return resposta

    if not POLIR_RESPOSTAS:
        return resposta

    metadados_dados = metadados_dados or "Sem metadados tabulares adicionais."

    messages = [
        {
            "role": "system",
            "content": """
Reescreva a resposta para soar natural, clara e útil para um funcionário administrativo.

REGRAS DE ESTILO:
- Responda em português do Brasil.
- Comece pela resposta direta.
- Não mencione que está reescrevendo.
- Não mencione SQL, RAG, prompt, schema ou detalhes internos, salvo se a pergunta pedir isso.
- Não diga "para encontrar" ou "você precisa consultar" se a resposta já contém o dado final.
- Se a pergunta for simples, mantenha a resposta curta.
- Se envolver configuração, regra ou vários campos importantes, organize em bullets.
- Se envolver análise, destaque o principal achado primeiro.
- Se a resposta for continuação de uma conversa, mantenha a continuidade.
- Se a pergunta for "por quê?", "como assim?" ou "explique melhor", responda explicando a resposta anterior sem criar novos fatos.
- Evite linguagem robótica e frases genéricas.
- Remova termos técnicos de SQL em respostas conceituais, como CURDATE(), NOW(), DATE_FORMAT(), IN, SELECT e WHERE, salvo se o usuário pedir SQL.
- Traduza condições técnicas para linguagem natural.
- Exemplo: "devolver_ate < CURDATE()" deve virar "a data limite de devolução já passou".

REGRAS DE FIDELIDADE AO TEXTO ORIGINAL:
- Reescreva apenas a forma do texto, nunca o conteúdo factual.
- Não adicione fatos novos.
- Não remova informações importantes.
- Não acrescente itens em listas, rankings, tabelas ou gráficos.
- Não complete rankings com dados que não estavam na resposta original.
- Se a resposta original trouxe 6 registros, mantenha exatamente esses 6 registros.
- Não invente nomes de livros, usuários, autores, reservas, empréstimos, exemplares, datas, status, gêneros, categorias ou quantidades.
- Não substitua nomes reais por exemplos.
- Não use conhecimento geral para melhorar a resposta.
- Não transforme uma limitação em certeza.
- Não transforme hipóteses em certezas.
- Preserve cautelas como "provavelmente", "pelo contexto" e "não dá para confirmar".
- Se a resposta original disser que encontrou apenas alguns registros, preserve essa limitação.
- Se a pergunta pediu 10 itens, mas a resposta original trouxe menos, explique que apenas aquela quantidade foi encontrada.
- Não remova avisos de incerteza ou limitações dos dados.
- Não transforme "não encontrado" em uma resposta afirmativa.
- Não acrescente canais de notificação, recursos ou funcionalidades que não estejam na resposta original.

REGRAS ESPECÍFICAS PARA DADOS TABULARES:
- Use os metadados de dados apenas para impedir acréscimos indevidos.
- A resposta polida deve respeitar a quantidade de linhas, nomes e valores informados nos metadados.
- Se o banco retornou menos itens do que o usuário pediu, não complete a lista.
""",
        },
        {
            "role": "user",
            "content": f"""
PERGUNTA:
{pergunta}

METADADOS DOS DADOS REAIS, QUANDO EXISTIREM:
{metadados_dados}

RESPOSTA ORIGINAL:
{resposta}
""",
        },
    ]

    polida = ChamarLLM(
        messages,
        max_new_tokens=900,
        temperature=0.10,
        estatisticas=estatisticas
    )

    return polida.strip() if polida else resposta


# ══════════════════════════════════════════════════════════════════════════════
#  Geração SQL
# ══════════════════════════════════════════════════════════════════════════════

def GerarSQL(
    SolicitacaoUsuario: str,
    context: str,
    estatisticas: dict | None = None
) -> tuple[str, str, str]:
    GerarLog("🤖 Gerando e revisando SQL via vLLM...")

    user_msg = (
        "=== DDL DO BANCO ===\n"
        f"{context}\n\n"
        "=== PERGUNTA ===\n"
        f"{SolicitacaoUsuario}\n\n"
        "Escreva o SQL em <rascunho>, revise e coloque o SQL final em <sql>.\n"
	    "Dentro de <sql>, retorne SOMENTE SQL puro.\n"
	    "Não use ```sql, não use markdown, não use explicações e não coloque texto junto da consulta final.\n"
        "A consulta deve buscar os dados reais no banco, não explicar como fazer a consulta."
    )

    messages = [
        {"role": "system", "content": SYSTEM_SQL},
        {"role": "user", "content": user_msg},
    ]

    raw = ChamarLLM(
        messages,
        max_new_tokens=5000,
        temperature=0.0,
        estatisticas=estatisticas
    )

    GerarLog(f"\n{'─' * 60}")
    Box("📋 OUTPUT COMPLETO DA LLM:")
    GerarLog(raw)
    GerarLog(f"{'─' * 60}\n")

    sql_final, rascunho = ExtrairSqlFinal(raw)
    sql_final = CorrigirCTEPerdida(sql_final, rascunho)

    if rascunho and sql_final and rascunho.strip() != sql_final.strip():
        GerarLog("   🔧 LLM corrigiu o rascunho")
    elif sql_final:
        GerarLog("   ✅ Rascunho aprovado sem alterações")
    else:
        GerarLog("   ❌ Falha ao extrair <sql>")

    return sql_final, rascunho, raw


# ══════════════════════════════════════════════════════════════════════════════
#  Segurança SQL
# ══════════════════════════════════════════════════════════════════════════════

def ValidarSeguranca(sql: str) -> tuple[bool, str]:
    if not sql or not sql.strip():
        return False, "❌ SQL não encontrado."

    sql_limpo = sql.strip()
    sql_upper = sql_limpo.upper()

    if not re.match(r"(?:WITH|SELECT)\b", sql_upper):
        return False, f"❌ SQL não começa com SELECT ou WITH.\n{sql[:150]}"

    # Bloqueia sintaxes comuns de outros bancos que não funcionam no MySQL
    padroes_mysql_invalidos = [
        (
            r"\bFILTER\s*\(\s*WHERE\b",
            "❌ SQL usa FILTER (WHERE ...), que é sintaxe de PostgreSQL. Em MySQL use SUM(CASE WHEN ... THEN 1 ELSE 0 END)."
        ),
        (
            r"\bILIKE\b",
            "❌ SQL usa ILIKE, que não é sintaxe MySQL. Em MySQL use LIKE."
        ),
        (
            r"\bDATE_TRUNC\s*\(",
            "❌ SQL usa DATE_TRUNC, que não é sintaxe MySQL. Em MySQL use DATE_FORMAT()."
        ),
        (
            r"\bRETURNING\b",
            "❌ SQL usa RETURNING, que não deve ser usado nas consultas do painel."
        ),
        (
            r"::\s*\w+",
            "❌ SQL usa conversão com ::tipo, que é sintaxe de PostgreSQL. Use funções compatíveis com MySQL."
        ),
    ]

    for padrao, mensagem in padroes_mysql_invalidos:
        if re.search(padrao, sql_limpo, re.IGNORECASE):
            return False, mensagem

    # Bloqueia casos em que o modelo usa uma CTE como se fosse tabela real
    if not sql_upper.startswith("WITH"):
        ctes_suspeitas = [
            "GENERO_STATS",
            "LIVRO_DEMANDAS",
            "RESERVAS_POR_LIVRO",
            "EXEMPLARES_POR_LIVRO",
            "USUARIO_STATS",
            "FORUM_STATS",
        ]

        for cte in ctes_suspeitas:
            if re.search(rf"\bFROM\s+{cte}\b", sql_upper) or re.search(rf"\bJOIN\s+{cte}\b", sql_upper):
                return False, (
                    f"❌ SQL final usa '{cte}' como se fosse tabela, "
                    "mas essa CTE não foi definida com WITH no próprio SQL."
                )

    if re.search(r"\bdbo\.", sql_limpo, re.IGNORECASE):
        return False, "❌ Prefixo 'dbo.' é de SQL Server e não deve ser usado em MySQL."

    if re.search(r"\bTOP\s+\d+\b", sql_upper):
        return False, "❌ TOP é de SQL Server. Em MySQL use LIMIT no final da consulta."

    if re.search(r"\b(hash_senha|google_id|senha)\b", sql_limpo, re.IGNORECASE):
        return False, "❌ Consulta bloqueada: tentativa de selecionar campo sensível."

    for cmd in (
        "DROP", "DELETE", "TRUNCATE", "INSERT", "UPDATE",
        "ALTER", "CREATE", "EXEC", "EXECUTE", "MERGE", "XP_"
    ):
        if re.search(rf"\b{cmd}\b", sql_upper):
            return False, f"❌ Comando '{cmd}' proibido."

    return True, ""


# ══════════════════════════════════════════════════════════════════════════════
#  Banco de Dados
# ══════════════════════════════════════════════════════════════════════════════

def _create_engine():
    if DB_CONN_STRING:
        return create_engine(
            DB_CONN_STRING,
            pool_pre_ping=True,
            pool_recycle=3600,
            future=True,
        )

    url = URL.create(
        drivername="mysql+pymysql",
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_SERVER,
        port=DB_PORT,
        database=DB_DATABASE,
        query={"charset": "utf8mb4"},
    )

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=3600,
        future=True,
    )


def ExecutarSQL(sql: str) -> pd.DataFrame:
    sql = LimparSQLExtraido(sql)

    GerarLog("📌 SQL FINAL ENVIADA AO MYSQL:")
    GerarLog(repr(sql))

    try:
        with ENGINE.connect() as conn:
            return pd.read_sql(sql_text(sql), conn)

    except Exception as e:
        GerarLog("❌ ERRO REAL AO EXECUTAR SQL:")
        GerarLog(f"Tipo do erro: {type(e)}")
        GerarLog(f"Mensagem: {e}")
        GerarLog("SQL que falhou:")
        GerarLog(sql)
        raise


# ══════════════════════════════════════════════════════════════════════════════
#  Interpretação
# ══════════════════════════════════════════════════════════════════════════════

def GerarInterpretacao(
    SolicitacaoUsuario: str,
    df: pd.DataFrame,
    estatisticas: dict | None = None,
    historico: list[dict] | None = None,
    pergunta_original: str | None = None
) -> str:
    if df.empty:
        return "Não encontrei registros para essa pergunta. Pode ser que não existam dados com esses filtros no momento."

    historico_txt = HistoricoParaTexto(historico, limite=8)
    pergunta_original = pergunta_original or SolicitacaoUsuario

    LimiteModelo = 12288
    ReservaResposta = 900
    MargemSeguranca = 200

    metadados_dados = ResumoFidelidadeDados(df)

    SystemPrompt = """
Você é o assistente analítico do painel administrativo do LiteraWeb.

Você ajuda funcionários da biblioteca a entenderem os dados reais do sistema.
Seu tom deve ser claro, natural e útil, como um analista explicando o resultado.

REGRAS GERAIS:
- Responda em português do Brasil.
- Comece respondendo diretamente à pergunta do usuário.
- Não explique como consultar se os dados já foram retornados.
- Não comece com "Com base nos dados", "Com base na consulta" ou frases parecidas.
- Não mencione RAG, prompt, SQL, schema, DDL ou detalhes internos.
- Explique o significado prático do resultado.
- Use nomes técnicos apenas quando ajudarem o funcionário.
- Nunca exponha campos sensíveis.

REGRAS DE FIDELIDADE AOS DADOS:
- Use exclusivamente os dados presentes em DADOS RETORNADOS / DADOS AMOSTRADOS / METADADOS DOS DADOS.
- Nunca invente livros, usuários, reservas, empréstimos, exemplares, autores, gêneros, datas, status, quantidades ou qualquer outro valor.
- Nunca complete listas, rankings, tabelas ou gráficos com itens que não apareceram nos dados retornados.
- Se a pergunta pedir "top 10", "10 mais", "ranking com 10" ou algo semelhante, mas o banco retornar menos registros, informe claramente a quantidade real encontrada.
- Não diga que existem 10 registros se somente 6 foram retornados pelo banco.
- LIMIT 10 significa até 10 registros, não exatamente 10 registros.
- Se houver empate entre registros, explique apenas o empate entre os itens retornados.
- Não crie novos registros para preencher posições faltantes.
- A resposta textual deve respeitar exatamente os nomes, títulos, quantidades e valores presentes nos dados retornados.
- Não altere nomes de livros, usuários, autores ou categorias.
- Não corrija, traduza ou substitua títulos existentes no banco.
- Não suponha dados ausentes.
- Se uma informação não estiver nos dados retornados, diga que ela não foi encontrada no resultado consultado.
- Se os dados retornados forem insuficientes para responder completamente, explique a limitação em vez de completar por conta própria.
- Nunca utilize conhecimento geral, exemplos famosos ou títulos conhecidos para preencher lacunas.

REGRAS PARA AUSÊNCIA OU LIMITAÇÃO DE DADOS:
- Se nenhum registro for encontrado, diga que não foram encontrados dados com os filtros usados.
- Não sugira que existem dados fora do resultado.
- Não estime valores.
- Não apresente exemplos como se fossem dados reais.
- Não diga "provavelmente existem" registros.
- Não invente possíveis causas específicas.
- Pode dizer apenas que os dados podem não existir no momento ou que os filtros podem ter restringido o resultado.

REGRAS PARA RANKINGS E TOP N:
- Em rankings, liste somente os registros retornados pelo banco.
- Se o usuário pedir top 10 e o banco retornar 6 registros, apresente apenas 6.
- Explique: "Embora a pergunta tenha solicitado 10 itens, foram encontrados apenas 6 registros que atendem aos critérios."
- Não complete posições faltantes.
- Não invente nomes para preencher o ranking.
- Não use livros famosos, exemplos genéricos ou conhecimento externo.
- Não diga "os 10 mais" se houver menos de 10 registros.
- Prefira dizer "os livros encontrados no ranking foram..." quando houver menos itens.
- Se houver empate, mantenha a ordenação retornada pelo banco.
- Não altere a ordem dos dados, salvo se a pergunta pedir outra ordenação e os dados retornados sustentarem isso.

REGRAS DE PRIVACIDADE E SEGURANÇA:
- Nunca exponha senha, hash_senha, google_id, tokens, chaves, credenciais ou dados sensíveis.
- Se a pergunta pedir dados sensíveis, informe que essa informação não pode ser exibida.

CONVERSA:
- Use o histórico recente apenas para entender perguntas de acompanhamento.
- O histórico não é fonte de verdade sobre regras ou dados; ele só ajuda a entender a referência da pergunta.
- Se a pergunta atual for "por quê?", explique o motivo da resposta anterior usando os dados retornados ou o contexto real.
- Não confirme afirmações anteriores que não estejam apoiadas pelos dados retornados ou pelo contexto recuperado.
- Não diga que a pergunta está incompleta quando o histórico resolver a referência.

NÍVEL DE DETALHE:
- Para contagens simples, responda em uma frase direta e, se útil, acrescente uma observação curta.
- Para uma única configuração atual, apresente o nome/identificação primeiro e detalhe os campos relevantes em bullets.
- Para um único registro importante, apresente o registro e os principais campos em linguagem administrativa.
- Para listas, mostre os principais itens de forma organizada, sem explicar linha por linha em excesso.
- Para rankings, destaque os primeiros colocados e o que isso indica, respeitando somente os dados retornados.
- Para tendências, evolução ou comparação, destaque aumento, queda, padrão, alerta ou ponto de atenção.
- Para resultados grandes, resuma o achado principal em vez de repetir todos os dados.

FORMATO:
- Use parágrafos curtos.
- Use bullets quando houver vários campos importantes.
- Seja objetivo, mas completo quando a pergunta envolver regras, configurações ou status atual.
"""

    UserBase = f"""
HISTÓRICO RECENTE:
{historico_txt}

PERGUNTA ORIGINAL DO FUNCIONÁRIO:
{pergunta_original}

PERGUNTA INTERPRETADA:
{SolicitacaoUsuario}

METADADOS DOS DADOS:
{metadados_dados}

DADOS RETORNADOS:
Os dados abaixo vieram do banco do LiteraWeb.

O QUE FAZER:
Responda diretamente à pergunta do funcionário usando somente os dados retornados.
Não crie registros que não estejam nos dados.
Não complete rankings, listas, tabelas ou gráficos com valores inventados.
Se a pergunta solicitar uma quantidade maior do que a quantidade de linhas retornadas, informe que o banco retornou apenas a quantidade disponível.
Se a pergunta pedir "top 10" e apenas 6 linhas forem retornadas, responda que foram encontrados apenas 6 registros que atendem aos critérios.
Nunca transforme ausência de dados em dado presumido.
Nunca utilize conhecimento geral, exemplos famosos ou títulos conhecidos para preencher lacunas.
Se for uma configuração atual, apresente primeiro o nome/identificação da configuração.
Depois liste os campos importantes em linguagem administrativa.
Não ensine o usuário a fazer a consulta; entregue a resposta final.
Adapte o nível de detalhe à pergunta: curto para perguntas simples, mais organizado para configurações, regras, registros importantes, rankings ou tendências.
"""

    Promptfixo = SystemPrompt + UserBase
    TokensBase = len(Promptfixo) // 4
    TokensDisponiveis = LimiteModelo - TokensBase - ReservaResposta - MargemSeguranca

    linhas = []
    TokensUsados = 0

    for _, row in df.iterrows():
        linha_txt = row.to_frame().T.to_string(index=False, header=(len(linhas) == 0))
        TokensLinha = max(1, len(linha_txt) // 4)

        if TokensUsados + TokensLinha > TokensDisponiveis:
            break

        linhas.append(linha_txt)
        TokensUsados += TokensLinha

    amostra = "\n".join(linhas)

    messages = [
        {"role": "system", "content": SystemPrompt},
        {
            "role": "user",
            "content": f"""{UserBase}

DADOS AMOSTRADOS ({len(linhas)} linhas de {len(df)} totais):
{amostra}

ATENÇÃO:
- Se DADOS AMOSTRADOS tiver menos linhas do que o usuário pediu, informe apenas a quantidade encontrada/amostrada.
- Não complete com dados externos.
- Não invente itens ausentes.
""",
        },
    ]

    raw = ChamarLLM(
        messages,
        max_new_tokens=1100,
        temperature=0.10,
        estatisticas=estatisticas
    )

    resposta = raw.strip() if raw else "Consegui consultar os dados, mas não consegui gerar uma interpretação em linguagem natural."

    return PolirResposta(
        resposta,
        pergunta_original,
        estatisticas=estatisticas,
        metadados_dados=metadados_dados
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Visualização
# ══════════════════════════════════════════════════════════════════════════════

def _viz_fallback(SolicitacaoUsuario: str, df: pd.DataFrame) -> dict:
    import pandas as _pd

    q = SolicitacaoUsuario.lower()

    if any(p in q for p in ("linha", "linhas", "line", "tendência", "evolução", "série")):
        tipo = "line"
    elif any(p in q for p in ("pizza", "pie", "distribuição", "proporção")):
        tipo = "pie"
    elif any(p in q for p in ("barra", "barras", "bar", "ranking", "comparar", "top")):
        tipo = "bar"
    elif any(p in q for p in ("dispersão", "scatter", "correlação")):
        tipo = "scatter"
    elif df.empty or len(df.columns) < 2:
        tipo = "table_only"
    else:
        has_time = any(
            c in str(df.columns).lower()
            for c in ("mes", "month", "ano", "year", "data", "date", "trimestre")
        )

        tipo = "line" if has_time and len(df) >= 3 else (
            "pie" if len(df) <= 8 and len(df.columns) == 2 else "bar"
        )

    cols = list(df.columns)

    if not cols:
        return {
            "tipo": "table_only",
            "x": None,
            "y": None,
            "cor": None,
            "titulo": SolicitacaoUsuario[:80],
        }

    TEMPO = ("mes", "month", "trimestre", "quarter", "ano", "year", "data", "date")
    ORDEM = ("num", "seq", "ord", "rank", "pos", "id")

    textos = [c for c in cols if not _pd.api.types.is_numeric_dtype(df[c])]
    nums = [c for c in cols if _pd.api.types.is_numeric_dtype(df[c])]

    auxiliar = [c for c in nums if any(k in c.lower() for k in TEMPO + ORDEM)]
    metricas = [c for c in nums if c not in auxiliar] or nums

    x = (
        next((c for c in textos if any(k in c.lower() for k in TEMPO)), None)
        or (textos[0] if textos else None)
        or (auxiliar[0] if auxiliar else cols[0])
    )

    y = metricas[0] if metricas else cols[-1]

    cor = next(
        (
            c for c in cols
            if any(k in c.lower() for k in ("ano", "year"))
            and df[c].nunique() > 1
        ),
        None
    )

    titulo = SolicitacaoUsuario[:80]

    GerarLog(f"   ⚠️  Fallback heurístico → tipo={tipo} x={x} y={y} cor={cor}")

    return {
        "tipo": tipo,
        "x": x,
        "y": y,
        "cor": cor,
        "titulo": titulo,
    }


SYSTEM_VIZ = """Você é especialista em visualização de dados para o painel administrativo do LiteraWeb.
Sua tarefa é analisar uma pergunta administrativa e as colunas do resultado SQL,
e decidir a melhor forma de visualizar esses dados.

TIPOS DISPONÍVEIS:
- line       → séries temporais, evolução por mês/trimestre/ano, tendências, YOY
- bar        → rankings, comparações entre categorias, top-N, volumes por categoria
- pie        → distribuições proporcionais / percentuais, máximo 8 categorias
- scatter    → correlação entre duas variáveis numéricas contínuas
- table_only → sem dimensão visual clara

REGRAS PARA EIXOS:
- x  → categoria ou tempo
- y  → métrica principal
- cor → segmentação para múltiplas séries, ou null
- titulo → título curto e descritivo em português

REGRAS DE FIDELIDADE À VISUALIZAÇÃO:
- Escolha apenas colunas que existem exatamente no resultado SQL.
- Não invente colunas.
- Não invente categorias.
- Não invente métricas.
- Não altere os dados.
- Não agregue novamente se o resultado já estiver agregado.
- Não escolha uma coluna para x, y ou cor se ela não existir exatamente no DataFrame.
- O título do gráfico deve descrever apenas o que os dados retornados mostram.
- Se os dados forem insuficientes para gráfico, retorne table_only.
- Se houver apenas uma linha, prefira table_only, salvo quando a pergunta pedir explicitamente gráfico e houver dimensão/métrica adequadas.
- Se a pergunta pedir gráfico, mas os dados não tiverem dimensão e métrica adequadas, retorne table_only.
- Não crie séries temporais se não houver coluna de data, mês, ano ou período.
- Não crie gráfico de pizza com muitas categorias.
- Nunca crie rótulos ou valores para o gráfico; eles serão gerados pelo sistema a partir do DataFrame.

FORMATO DE RESPOSTA:
JSON puro, sem markdown:
{"tipo":"bar","x":"categoria","y":"total","cor":null,"titulo":"Título do gráfico"}
"""


def DecidirVisualizacao(
    SolicitacaoUsuario: str,
    df: pd.DataFrame,
    estatisticas: dict | None = None
) -> dict:
    import json as _json

    TIPOS_VALIDOS = {"line", "bar", "pie", "scatter", "table_only"}
    cols = list(df.columns)

    if df.empty or not cols:
        return {
            "tipo": "table_only",
            "x": None,
            "y": None,
            "cor": None,
            "titulo": SolicitacaoUsuario[:80],
        }

    def _desc_col(c):
        dtype = "numérica" if pd.api.types.is_numeric_dtype(df[c]) else "texto"
        amostra = df[c].dropna().head(3).tolist()
        return f"{c} ({dtype}, ex: {amostra})"

    colunas_desc = "\n".join(f"  - {_desc_col(c)}" for c in cols)

    user_msg = (
        f"Pergunta: {SolicitacaoUsuario}\n\n"
        f"Resultado: {len(df)} linha(s), colunas:\n{colunas_desc}\n\n"
        "Responda com o JSON de visualização."
    )

    messages = [
        {"role": "system", "content": SYSTEM_VIZ},
        {"role": "user", "content": user_msg},
    ]

    raw = ChamarLLM(
        messages,
        max_new_tokens=120,
        temperature=0.0,
        estatisticas=estatisticas
    )

    try:
        json_str = re.sub(r"```(?:json)?|```", "", raw or "").strip()
        m = re.search(r"\{.*?\}", json_str, re.DOTALL)

        if not m:
            raise ValueError("Nenhum objeto JSON encontrado")

        dados = _json.loads(m.group())

        tipo = str(dados.get("tipo", "")).lower()
        x_col = dados.get("x")
        y_col = dados.get("y")
        cor = dados.get("cor") or None
        titulo = str(dados.get("titulo", SolicitacaoUsuario[:80]))

        if tipo not in TIPOS_VALIDOS:
            raise ValueError(f"tipo inválido: {tipo}")

        if tipo not in ("pie", "table_only") and x_col not in cols:
            raise ValueError(f"coluna x '{x_col}' não existe no DataFrame")

        if tipo not in ("table_only",) and y_col not in cols:
            raise ValueError(f"coluna y '{y_col}' não existe no DataFrame")

        if cor and cor not in cols:
            cor = None

        GerarLog(f"   🤖 LLM Decidindo Visualização → tipo={tipo} x={x_col} y={y_col} cor={cor}")

        return {
            "tipo": tipo,
            "x": x_col,
            "y": y_col,
            "cor": cor,
            "titulo": titulo,
        }

    except Exception as e:
        GerarLog(f"   ⚠️  Falha ao parsear JSON da LLM ({e}) — usando fallback")
        return _viz_fallback(SolicitacaoUsuario, df)


# ══════════════════════════════════════════════════════════════════════════════
#  API pública
# ══════════════════════════════════════════════════════════════════════════════

def ask(
    SolicitacaoUsuario: str,
    use_rag: bool = True,
    historico: list[dict] | None = None
) -> dict:
    timing = {}
    estatisticas = nova_estatistica_llm()
    estatisticas["modelo"] = LLM_NAME

    historico = NormalizarHistorico(historico, limite=8)
    pergunta_original = SolicitacaoUsuario

    SolicitacaoUsuario = ReescreverPerguntaComHistorico(
        SolicitacaoUsuario,
        historico=historico,
        estatisticas=estatisticas
    )

    if SolicitacaoUsuario != pergunta_original:
        GerarLog(f"🔁 Pergunta reescrita: {SolicitacaoUsuario}")

    result = {
        "sql": "",
        "sql_rascunho": "",
        "sql_raw": "",
        "df": pd.DataFrame(),
        "interpretacao": "",
        "chart_type": "table_only",
        "viz": {},
        "error": None,
        "error_user": None,
        "timing": timing,
        "estatisticas": estatisticas,
        "pergunta_original": pergunta_original,
        "pergunta_reescrita": SolicitacaoUsuario,
    }

    try:
        t0 = time.time()

        if use_rag:
            GerarLog("🔍 RAG...")

            context, documentos_usados = retrieve_context_info(SolicitacaoUsuario)
            estatisticas["documentos_usados"] = documentos_usados

            GerarLog(f"   ✅ {len(context)} chars")
            GerarLog(f"   📚 documentos/chunks usados: {documentos_usados}")

            timing["rag_s"] = round(time.time() - t0, 2)

            tipo_pergunta = ClassificarPergunta(SolicitacaoUsuario)
            GerarLog(f"🧭 Tipo de pergunta detectado: {tipo_pergunta}")

            if tipo_pergunta == "conceitual":
                GerarLog("💬 Respondendo com RAG sem gerar SQL...")

                t0 = time.time()

                resposta = ResponderAssistenteRAG(
                    SolicitacaoUsuario,
                    context,
                    estatisticas=estatisticas,
                    historico=historico,
                    pergunta_original=pergunta_original
                )

                resposta = PolirResposta(
                    resposta,
                    pergunta_original,
                    estatisticas=estatisticas
                )

                result["sql"] = "Pergunta conceitual — SQL não gerado."
                result["sql_rascunho"] = "Pergunta conceitual — SQL não gerado."
                result["sql_raw"] = resposta
                result["df"] = pd.DataFrame()
                result["interpretacao"] = resposta
                result["chart_type"] = "table_only"
                result["viz"] = {}
                result["error"] = None
                result["error_user"] = None
                timing["llm_interp_s"] = round(time.time() - t0, 2)

                return result

            t0 = time.time()

            sql, rascunho, raw = GerarSQL(
                SolicitacaoUsuario,
                context,
                estatisticas=estatisticas
            )

            timing["llm_sql_s"] = round(time.time() - t0, 2)

            result["sql"] = sql
            result["sql_rascunho"] = rascunho
            result["sql_raw"] = raw

            ok, motivo = ValidarSeguranca(sql)

            if not ok:
                result["error"] = motivo
                result["error_user"] = (
                    "Não consegui montar uma consulta segura para essa pergunta. "
                    "Tente reformular ou pedir de forma mais específica."
                )
                return result

            t0 = time.time()

            GerarLog("🗄️  Executando no MySQL via SQLAlchemy...")
            df = ExecutarSQL(sql)

            timing["db_s"] = round(time.time() - t0, 2)
            result["df"] = df

            GerarLog(f"   ✅ {len(df)} linhas")

            if df.empty:
                result["interpretacao"] = (
                    "Não encontrei registros para essa pergunta. "
                    "Pode ser que não existam dados com esses filtros no momento."
                )
                return result

            t0 = time.time()

            GerarLog("📝 Interpretação...")
            result["interpretacao"] = GerarInterpretacao(
                SolicitacaoUsuario,
                df,
                estatisticas=estatisticas,
                historico=historico,
                pergunta_original=pergunta_original
            )

            timing["llm_interp_s"] = round(time.time() - t0, 2)

            t0 = time.time()

            GerarLog("📊 Decidindo visualização com LLM...")
            viz = DecidirVisualizacao(
                SolicitacaoUsuario,
                df,
                estatisticas=estatisticas
            )

            timing["llm_chart_s"] = round(time.time() - t0, 2)

            result["chart_type"] = viz["tipo"]
            result["viz"] = viz

            GerarLog(f"   ✅ viz={viz}")

        else:
            contexto = """
Você é o assistente administrativo do LiteraWeb.
O acesso ao contexto RAG e ao banco está desabilitado nesta chamada.
Responda de forma natural, mas avise quando a pergunta depender das regras reais do sistema ou dos dados atuais.
"""

            historico_txt = HistoricoParaTexto(historico, limite=8)

            messages = [
                {"role": "system", "content": contexto},
                {
                    "role": "user",
                    "content": f"""
HISTÓRICO RECENTE:
{historico_txt}

PERGUNTA ORIGINAL:
{pergunta_original}

PERGUNTA INTERPRETADA:
{SolicitacaoUsuario}
"""
                },
            ]

            result["sql"] = "Não foi utilizado RAG."
            result["sql_rascunho"] = "Não foi utilizado RAG."
            result["sql_raw"] = "Não foi utilizado RAG."
            result["df"] = pd.DataFrame()

            resposta = ChamarLLM(
                messages,
                max_new_tokens=1000,
                temperature=0.20,
                estatisticas=estatisticas
            )

            result["interpretacao"] = PolirResposta(
                resposta,
                pergunta_original,
                estatisticas=estatisticas
            )

            result["chart_type"] = "table_only"
            result["error"] = None
            result["error_user"] = None
            result["timing"] = timing

    except SQLAlchemyError as e:
        GerarLog("❌ SQLAlchemyError capturado no ask():")
        GerarLog(f"Tipo: {type(e)}")
        GerarLog(f"Erro: {e}")
        GerarLog("SQL salva em result['sql']:")
        GerarLog(result["sql"])

        result["error"] = f"❌ Erro no banco:\n{e}\n\nSQL:\n{result['sql']}"
        result["error_user"] = "Consegui gerar a consulta, mas houve um erro ao consultar o banco de dados."

    except Exception:
        import traceback
        result["error"] = f"❌ Erro inesperado:\n{traceback.format_exc()}"
        result["error_user"] = "Ocorreu um erro inesperado ao processar sua pergunta."

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  Configurações
# ══════════════════════════════════════════════════════════════════════════════

warnings.filterwarnings("ignore")

os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True,max_split_size_mb:128"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

torch.set_num_threads(36)
torch.set_float32_matmul_precision("high")

DB_CONN_STRING = os.getenv("DB_CONN_STRING", "").strip()
DB_SERVER = os.getenv("DB_SERVER", "")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_DATABASE = os.getenv("DB_DATABASE", "literaweb")
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

POLIR_RESPOSTAS = os.getenv("RAG_POLIR_RESPOSTAS", "1").strip().lower() in (
    "1",
    "true",
    "sim",
    "yes",
)

ENGINE = _create_engine()

LLM_NAME = "unsloth/Qwen2.5-Coder-14B-Instruct-bnb-4bit"
EMBED_MODEL = "sentence-transformers/multi-qa-mpnet-base-dot-v1"
STORE_FILE = f"{PastaEsquema}store_tickets.pkl"
RAG_TOP_K = 6

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="",
    timeout=300.0
)


# ══════════════════════════════════════════════════════════════════════════════
#  Prompt SQL
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_SQL = """
Você é um especialista em MySQL para o sistema LiteraWeb.
Sua missão é gerar consultas SQL seguras e precisas para o painel administrativo da biblioteca.

CONTEXTO:
- O banco é MySQL 8.
- Use apenas tabelas e colunas presentes no contexto fornecido.
- O objetivo é responder perguntas administrativas sobre livros, exemplares, usuários, reservas, políticas e fórum.

REGRAS DE OURO:
1. USE SOMENTE SELECT OU WITH:
   - Nunca gere INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER, CREATE, EXEC ou MERGE.
   - Nunca troque o gênero gramatical do nome das colunas. Use exatamente o nome que aparece no schema.

2. SINTAXE MYSQL:
    Use somente sintaxe compatível com MySQL 8.
   - Use LIMIT N no final da consulta para rankings/top N.
   - LIMIT N significa "até N registros", não exatamente N registros.
   - Nunca use TOP.
   - Nunca use dbo.
   - Nunca use colchetes [coluna].
   - Use CURDATE() para data atual.
   - Use NOW() para data e hora atual.
   - Use DATE_FORMAT(campo, '%Y-%m') para agrupamento mensal.
   - Use IFNULL() ou COALESCE() para valores nulos.
   - Use crase `coluna` apenas quando necessário.

   SINTAXES PROIBIDAS POR NÃO SEREM MYSQL:
   - Nunca use COUNT(...) FILTER (WHERE ...), pois isso é sintaxe de PostgreSQL.
   - Para contagens condicionais no MySQL, use sempre:
     SUM(CASE WHEN condição THEN 1 ELSE 0 END).
   - Nunca use ILIKE.
   - Nunca use DATE_TRUNC.
   - Nunca use RETURNING.
   - Nunca use conversão com ::tipo.

   EXEMPLO CORRETO EM MYSQL:
   SUM(CASE WHEN r.status = 'pendente' THEN 1 ELSE 0 END) AS reservas_pendentes

   EXEMPLO PROIBIDO:
   COUNT(r.id) FILTER (WHERE r.status = 'pendente')

3. REGRAS DO LITERAWEB:
   - Para livros ativos, use livros.ativo = 1, salvo pedido contrário.
   - Para exemplares ativos, use exemplares.ativo = 1, salvo pedido contrário.
   - Não existe tabela emprestimos.
   - Não existe coluna reservas.reserva_id.
   - A tabela reservas usa id como chave primária e exemplar_id como FK para exemplares.id.
   - Empréstimos são representados por reservas.emprestado_em IS NOT NULL.
   - Para livros mais emprestados, use livros -> exemplares -> reservas.
   - Para contar empréstimos, use COUNT(reservas.id), nunca COUNT(reservas.reserva_id).
   - Não filtre exemplares.status = 'disponivel' ao contar empréstimos históricos.
   - Exemplar disponível normalmente significa exemplares.ativo = 1 AND exemplares.status = 'disponivel', se essa coluna existir no contexto.
   - Use somente status, tipos, categorias e regras que estejam documentados no contexto.
   - Nunca consulte ou exponha senha, hash_senha, google_id ou campos sensíveis.

4. PERGUNTAS SOBRE ESTADO ATUAL:
   - Se o usuário perguntar por algo atual, ativo, vigente, em vigor, configurado, habilitado, pendente, disponível ou em atraso, gere uma consulta que retorne os dados reais atuais.
   - Não responda apenas explicando como encontrar o dado.
   - Use filtros de status/ativo/data somente quando as colunas existirem no contexto.
   - Para configurações atuais, retorne os campos administrativos relevantes.

5. POLÍTICA DE EMPRÉSTIMO:
   - A política atual do sistema é o registro de politicas_emprestimo onde ativa = 1, se essa tabela e coluna existirem no contexto.
   - Para perguntas sobre política atual, política ativa, política vigente ou política em vigor, filtre ativa = 1.
   - Retorne campos como nome, prazo_devolucao_dias, prazo_retirada_horas, limite_reservas_ativas, max_renovacoes, prazo_renovacao_dias, tolerancia_atraso_dias, tipo_bloqueio_atraso e valor_bloqueio_atraso, desde que existam no contexto.
   - Não use SELECT *.

6. SQL SEGURO:
   - O SQL final deve começar com SELECT ou WITH.
   - Não use comandos de alteração de dados.
   - Não use SELECT *.
   - Retorne apenas colunas necessárias.
   - Para consultas grandes, use LIMIT quando fizer sentido.

7. REGRAS CONTRA SQL INVENTADO:
   - Use somente tabelas e colunas explicitamente presentes no contexto fornecido.
   - Nunca invente nomes de tabelas.
   - Nunca invente nomes de colunas.
   - Nunca use colunas comuns de outros sistemas se elas não aparecerem no contexto.
   - Se a pergunta exigir uma coluna que não está no contexto, não gere SQL inválido.
   - Se não houver informação suficiente para montar a consulta, gere uma consulta segura apenas se ela puder responder parcialmente; caso contrário, deixe claro na revisão que não há base suficiente.
   - Não use JOIN com relacionamentos não informados no contexto.
   - Não assuma chaves estrangeiras que não estejam descritas.
   - Não use status, tipos ou categorias que não estejam documentados.
   - Para rankings, use ORDER BY na métrica principal em ordem decrescente.
   - Para "top N", use LIMIT N.
   - Não force a consulta a gerar linhas artificiais para completar ranking.
   - Não use UNION com valores fixos para completar resultados.
   - Não gere dados fictícios com SELECT 'Nome de exemplo'.
   - Nunca use SELECT com valores literais para criar livros, usuários, autores, categorias ou status fictícios.
   - A consulta deve retornar apenas registros existentes no banco.

8. REGRAS PARA DATAS E PRAZOS:
   - Não invente datas.
   - Não calcule prazos se os campos necessários não estiverem no contexto.
   - Não diga que algo está atrasado sem comparar com a data de devolução/prazo e a data atual.
   - Use CURDATE() ou NOW() quando a pergunta depender da data atual.

9. FORMATO:
   - Primeiro escreva o SQL inicial em <rascunho>.
   - Depois revise.
   - Por fim coloque o SQL final entre <sql> e </sql>.
   - O conteúdo dentro de <sql> deve ser SQL puro, sem markdown.
   REGRAS PARA WITH / CTE:
    - Se usar WITH no rascunho, o SQL final em <sql> também deve conter o WITH completo.
    - O conteúdo dentro de <sql> deve ser uma consulta completa e executável sozinha.
    - Nunca coloque em <sql> apenas o SELECT final que depende de uma CTE criada no rascunho.
    - Nunca use FROM nome_temporario, FROM genero_stats, FROM livro_demandas ou qualquer CTE se ela não estiver definida no mesmo <sql>.
    - Se a consulta usar CTE, o <sql> final deve começar com WITH.

FORMATO OBRIGATÓRIO:

<rascunho>
SELECT ...
</rascunho>

REVISÃO:
- Verifique se todas as tabelas e colunas existem no contexto.
- Verifique se a sintaxe é MySQL.
- Se houver COUNT(...) FILTER (WHERE ...), corrija para SUM(CASE WHEN ... THEN 1 ELSE 0 END).
- Se houver ILIKE, DATE_TRUNC, RETURNING ou ::tipo, corrija para sintaxe MySQL.
- Verifique se a consulta é somente leitura.
- Verifique se não há campos sensíveis.
- Verifique se não há SELECT *.
- Verifique se não há dados fictícios ou valores literais usados para completar resultado.
- Verifique se perguntas sobre estado atual retornam dados atuais, não instruções.
- Se a consulta usa CTE, verifique se o WITH completo está dentro de <sql>.
- Verifique se toda tabela usada em FROM ou JOIN é uma tabela real do contexto ou uma CTE definida no próprio SQL final.
- Não aprove o SQL final se ele depender de algo definido apenas no rascunho.

<sql>
SELECT ...
</sql>
"""


# ══════════════════════════════════════════════════════════════════════════════
#  Inicialização
# ══════════════════════════════════════════════════════════════════════════════

Box("⚡ QWEN2.5-CODER-14B | vLLM Server Mode | MySQL/SQLAlchemy | LiteraWeb")

GerarLog("🔢 Carregando embedder RAG...")
embedder = SentenceTransformer(EMBED_MODEL, device="cpu")
GerarLog("✅ Embedder pronto!")

try:
    models = client.models.list()
    modelo_disponivel = models.data[0].id if models.data else "modelo não informado"
    GerarLog(f"✅ Conexão com vLLM OK — Modelo disponível: {modelo_disponivel}")
except Exception as e:
    GerarLog(f"⚠️  Erro ao conectar com vLLM: {e}")
    GerarLog("   Certifique-se de que o servidor vLLM está rodando em outro terminal.")


# ══════════════════════════════════════════════════════════════════════════════
#  Warmup
# ══════════════════════════════════════════════════════════════════════════════

Box("🔥 Warmup...")

try:
    sql_w, _, _ = GerarSQL(
        "Qual o total de livros ativos?",
        """
CREATE TABLE livros (
  id INT,
  titulo VARCHAR(255),
  ativo TINYINT
);
"""
    )
    GerarLog(f"✅ Warmup OK — {sql_w[:80]}...")
except Exception as e:
    GerarLog(f"⚠️  Warmup: {e}")

Box("🎯 Pronto para uso com MySQL via SQLAlchemy")
