let ragChartInstance = null;
let ragTempoInterval = null;
let ragInicioProcessamento = null;
let ragHistorico = [];

const RAG_HISTORICO_MAX = 10;

function salvarNoHistorico(role, content) {
  const texto = String(content || "").trim();
  if (!texto) return;

  ragHistorico.push({
    role,
    content: texto
  });

  if (ragHistorico.length > RAG_HISTORICO_MAX) {
    ragHistorico = ragHistorico.slice(-RAG_HISTORICO_MAX);
  }
}

function limparHistoricoRag() {
  ragHistorico = [];
}

const form = document.getElementById("ragChatForm");
const perguntaEl = document.getElementById("ragPergunta");
const enviarEl = document.getElementById("ragEnviar");
const statusEl = document.getElementById("ragStatus");
const messagesEl = document.getElementById("ragChatMessages");

const emptyStateEl = document.getElementById("ragEmptyState");
const chartWrapEl = document.getElementById("ragChartWrap");
const tableWrapEl = document.getElementById("ragTableWrap");
const tabelaEl = document.getElementById("ragTabela");
const sqlBoxEl = document.getElementById("ragSqlBox");
const sqlEl = document.getElementById("ragSql");

const llmMetricsEl = document.getElementById("ragLlmMetrics");
const metricaStatusEl = document.getElementById("ragMetricaStatus");
const tempoRespostaEl = document.getElementById("ragTempoResposta");
const modeloEl = document.getElementById("ragModelo");
const tokensEl = document.getElementById("ragTokens");
const docsEl = document.getElementById("ragDocs");

inicializarMetricas();

if (form) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const pergunta = perguntaEl.value.trim();
    if (!pergunta) return;

    addMessage("user", pergunta);

    perguntaEl.value = "";
    perguntaEl.style.height = "";

    setLoading(true);
    iniciarMetricasTempoReal();

    try {
      const resp = await fetch("/funcionario/rag/perguntar", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          pergunta,
          historico: ragHistorico.slice(-6)
        })
      });

      let data = {};

      try {
        data = await resp.json();
      } catch (jsonError) {
        data = {
          ok: false,
          tipo: "erro",
          erro: "O servidor respondeu, mas não retornou JSON válido."
        };
      }

      const tempoFinalClienteMs = calcularTempoAtualMs();

      pararMetricasTempoReal();

      atualizarMetricasLLM(
        data,
        tempoFinalClienteMs,
        resp.ok && data.ok ? "Concluído" : "Erro"
      );

      const respostaErro = data.erro || "Não consegui processar sua pergunta.";

      if (!resp.ok || !data.ok) {
        addMessage("assistant", respostaErro);

        salvarNoHistorico("user", pergunta);
        salvarNoHistorico("assistant", respostaErro);

        if (data.sql) {
          showSql(data.sql);
        }

        marcarMetricasComoErro();
        return;
      }

      const respostaAssistente = data.resposta || "Consulta concluída.";

      addMessage("assistant", respostaAssistente);

      salvarNoHistorico("user", pergunta);
      salvarNoHistorico("assistant", respostaAssistente);

      if (data.linhas && data.linhas.length > 0) {
        renderTabela(data.colunas || [], data.linhas);
        renderGrafico(data.grafico || {}, data.linhas);
      } else {
        clearVisualizacao();
      }

      if (data.sql && !data.sql.toLowerCase().includes("sql não gerado")) {
        showSql(data.sql);
      } else {
        hideSql();
      }

      marcarMetricasComoConcluidas();

    } catch (err) {
      console.error(err);

      const tempoFinalClienteMs = calcularTempoAtualMs();

      pararMetricasTempoReal();

      atualizarMetricasLLM(
        {
          estatisticas: {
            tempo_ms: tempoFinalClienteMs,
            modelo: null,
            prompt_tokens: null,
            completion_tokens: null,
            total_tokens: null,
            documentos_usados: null
          }
        },
        tempoFinalClienteMs,
        "Erro"
      );

      marcarMetricasComoErro();
      addMessage("assistant", "Erro de comunicação com o servidor. Tente novamente.");
    } finally {
      setLoading(false);
      rolarChatParaBaixo();
    }
  });
}

if (perguntaEl) {
  perguntaEl.addEventListener("input", () => {
    perguntaEl.style.height = "auto";
    perguntaEl.style.height = `${Math.min(perguntaEl.scrollHeight, 140)}px`;
  });
}

function inicializarMetricas() {
  if (!llmMetricsEl) return;

  llmMetricsEl.hidden = false;
  llmMetricsEl.classList.remove("is-processing", "is-done", "is-error");

  if (metricaStatusEl) metricaStatusEl.textContent = "Aguardando";
  if (tempoRespostaEl) tempoRespostaEl.textContent = "0 ms";
  if (modeloEl) modeloEl.textContent = "Não retornado";
  if (tokensEl) tokensEl.textContent = "Não retornado";
  if (docsEl) docsEl.textContent = "Não retornado";
}

function iniciarMetricasTempoReal() {
  ragInicioProcessamento = performance.now();

  if (!llmMetricsEl) return;

  llmMetricsEl.hidden = false;
  llmMetricsEl.classList.remove("is-done", "is-error");
  llmMetricsEl.classList.add("is-processing");

  if (metricaStatusEl) metricaStatusEl.textContent = "Processando";
  if (tempoRespostaEl) tempoRespostaEl.textContent = "0 ms";
  if (modeloEl) modeloEl.textContent = "Consultando...";
  if (tokensEl) tokensEl.textContent = "Calculando...";
  if (docsEl) docsEl.textContent = "Buscando...";

  if (ragTempoInterval) {
    clearInterval(ragTempoInterval);
  }

  ragTempoInterval = setInterval(() => {
    const tempoAtualMs = calcularTempoAtualMs();

    if (tempoRespostaEl) {
      tempoRespostaEl.textContent = formatarTempo(tempoAtualMs);
    }
  }, 100);
}

function pararMetricasTempoReal() {
  if (ragTempoInterval) {
    clearInterval(ragTempoInterval);
    ragTempoInterval = null;
  }
}

function calcularTempoAtualMs() {
  if (!ragInicioProcessamento) return 0;
  return performance.now() - ragInicioProcessamento;
}

function marcarMetricasComoConcluidas() {
  if (!llmMetricsEl) return;

  llmMetricsEl.classList.remove("is-processing", "is-error");
  llmMetricsEl.classList.add("is-done");

  if (metricaStatusEl) {
    metricaStatusEl.textContent = "Concluído";
  }
}

function marcarMetricasComoErro() {
  if (!llmMetricsEl) return;

  llmMetricsEl.classList.remove("is-processing", "is-done");
  llmMetricsEl.classList.add("is-error");

  if (metricaStatusEl) {
    metricaStatusEl.textContent = "Erro";
  }
}

function atualizarMetricasLLM(payload, tempoClienteMs, statusTexto = "Concluído") {
  if (!llmMetricsEl) return;

  llmMetricsEl.hidden = false;

  const estatisticas =
    payload?.estatisticas ||
    payload?.stats ||
    payload?.metrics ||
    payload?.usage ||
    {};

  const tempoServidorMs = primeiroValorValido(
    estatisticas.tempo_ms,
    estatisticas.llm_tempo_ms,
    estatisticas.response_time_ms,
    estatisticas.tempo_resposta_ms,
    payload?.tempo_ms,
    payload?.llm_tempo_ms,
    payload?.response_time_ms
  );

  const tempoServidorNumero = Number(tempoServidorMs);
  const tempoClienteNumero = Number(tempoClienteMs);

  const tempoFinalMs =
    Number.isFinite(tempoServidorNumero) && tempoServidorNumero > 0
      ? tempoServidorNumero
      : Number.isFinite(tempoClienteNumero) && tempoClienteNumero > 0
        ? tempoClienteNumero
        : calcularTempoAtualMs();

  const promptTokens = primeiroValorValido(
    estatisticas.prompt_tokens,
    estatisticas.input_tokens,
    estatisticas.tokens_prompt,
    estatisticas.promptTokens,
    estatisticas.inputTokens,
    payload?.prompt_tokens,
    payload?.input_tokens,
    payload?.tokens_prompt,
    payload?.promptTokens,
    payload?.inputTokens
  );

  const completionTokens = primeiroValorValido(
    estatisticas.completion_tokens,
    estatisticas.output_tokens,
    estatisticas.tokens_completion,
    estatisticas.completionTokens,
    estatisticas.outputTokens,
    payload?.completion_tokens,
    payload?.output_tokens,
    payload?.tokens_completion,
    payload?.completionTokens,
    payload?.outputTokens
  );

  const totalTokensRetornado = primeiroValorValido(
    estatisticas.total_tokens,
    estatisticas.tokens_total,
    estatisticas.totalTokens,
    payload?.total_tokens,
    payload?.tokens_total,
    payload?.totalTokens
  );

  const totalTokens =
    totalTokensRetornado !== null && totalTokensRetornado !== undefined
      ? totalTokensRetornado
      : calcularTotalTokens(promptTokens, completionTokens);

  const modelo = primeiroValorValido(
    estatisticas.modelo,
    estatisticas.model,
    estatisticas.llm_model,
    estatisticas.modelo_llm,
    payload?.modelo,
    payload?.model,
    payload?.llm_model,
    payload?.modelo_llm
  );

  const documentosUsadosRetornado = primeiroValorValido(
    estatisticas.documentos_usados,
    estatisticas.docs_usados,
    estatisticas.contextos_usados,
    estatisticas.fontes_usadas,
    estatisticas.documentos_recuperados,
    payload?.documentos_usados,
    payload?.docs_usados,
    payload?.contextos_usados,
    payload?.fontes_usadas,
    payload?.documentos_recuperados
  );

  const documentosUsados =
    documentosUsadosRetornado !== null && documentosUsadosRetornado !== undefined
      ? normalizarQuantidade(documentosUsadosRetornado)
      : calcularDocumentosUsados(payload);

  if (metricaStatusEl) {
    metricaStatusEl.textContent = statusTexto;
  }

  if (tempoRespostaEl) {
    tempoRespostaEl.textContent = formatarTempo(tempoFinalMs);
  }

  if (modeloEl) {
    modeloEl.textContent = valorOuNaoRetornado(modelo);
  }

  if (tokensEl) {
    tokensEl.textContent = valorOuNaoRetornado(totalTokens);
  }

  if (docsEl) {
    docsEl.textContent = valorOuNaoRetornado(documentosUsados);
  }
}

function primeiroValorValido(...valores) {
  for (const valor of valores) {
    if (valor !== null && valor !== undefined && valor !== "") {
      return valor;
    }
  }

  return null;
}

function normalizarQuantidade(valor) {
  if (Array.isArray(valor)) {
    return valor.length;
  }

  const numero = Number(valor);

  if (Number.isFinite(numero)) {
    return numero;
  }

  return valor;
}

function calcularTotalTokens(promptTokens, completionTokens) {
  const prompt = Number(promptTokens);
  const completion = Number(completionTokens);

  const temPrompt = Number.isFinite(prompt);
  const temCompletion = Number.isFinite(completion);

  if (temPrompt || temCompletion) {
    return (temPrompt ? prompt : 0) + (temCompletion ? completion : 0);
  }

  return null;
}

function calcularDocumentosUsados(payload) {
  const possiveisListas = [
    payload?.documentos,
    payload?.docs,
    payload?.fontes,
    payload?.contextos,
    payload?.sources,
    payload?.chunks,
    payload?.documentos_recuperados
  ];

  for (const lista of possiveisListas) {
    if (Array.isArray(lista)) {
      return lista.length;
    }
  }

  return null;
}

function formatarTempo(ms) {
  const valor = Number(ms);

  if (!Number.isFinite(valor)) {
    return "—";
  }

  if (valor < 1000) {
    return `${Math.round(valor)} ms`;
  }

  return `${(valor / 1000).toFixed(2)} s`;
}

function valorOuNaoRetornado(value) {
  if (value === null || value === undefined || value === "") {
    return "Não retornado";
  }

  return String(value);
}

function setLoading(isLoading) {
  if (enviarEl) enviarEl.disabled = isLoading;
  if (perguntaEl) perguntaEl.disabled = isLoading;

  if (statusEl) {
    statusEl.textContent = isLoading ? "Consultando..." : "Pronto";
    statusEl.classList.toggle("is-loading", isLoading);
  }
}

function addMessage(role, text) {
  if (!messagesEl) return;

  const msg = document.createElement("div");
  msg.className = `rag-msg rag-msg--${role}`;

  const avatar = document.createElement("div");
  avatar.className = "rag-msg__avatar";
  avatar.textContent = role === "user" ? "👤" : "🤖";

  const bubble = document.createElement("div");
  bubble.className = "rag-msg__bubble";

  if (role === "assistant") {
    const markdown = text || "";

    if (window.marked && window.DOMPurify) {
      const html = marked.parse(markdown);
      bubble.innerHTML = DOMPurify.sanitize(html);
    } else {
      bubble.innerHTML = escapeHtml(markdown).replace(/\n/g, "<br>");
    }
  } else {
    bubble.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
  }

  msg.appendChild(avatar);
  msg.appendChild(bubble);

  messagesEl.appendChild(msg);
  rolarChatParaBaixo();
}

function rolarChatParaBaixo() {
  if (!messagesEl) return;
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderTabela(colunas, linhas) {
  if (!tableWrapEl || !tabelaEl) return;

  if (!colunas.length || !linhas.length) {
    tableWrapEl.hidden = true;
    tabelaEl.innerHTML = "";
    return;
  }

  let html = "<thead><tr>";

  for (const col of colunas) {
    html += `<th>${escapeHtml(formatarNomeColuna(col))}</th>`;
  }

  html += "</tr></thead><tbody>";

  for (const row of linhas) {
    html += "<tr>";

    for (const col of colunas) {
      html += `<td>${escapeHtml(formatarValorTabela(row[col]))}</td>`;
    }

    html += "</tr>";
  }

  html += "</tbody>";

  tabelaEl.innerHTML = html;
  tableWrapEl.hidden = false;

  if (emptyStateEl) {
    emptyStateEl.hidden = true;
  }
}

function renderGrafico(viz, linhas) {
  if (!chartWrapEl || !emptyStateEl) return;

  const tipo = viz?.tipo || "table_only";

  if (!viz || tipo === "table_only" || !linhas || !linhas.length) {
    chartWrapEl.hidden = true;
    return;
  }

  const x = viz.x;
  const y = viz.y;

  if (!x || !y) {
    chartWrapEl.hidden = true;
    return;
  }

  const canvas = document.getElementById("ragChart");

  if (!canvas) {
    chartWrapEl.hidden = true;
    emptyStateEl.hidden = false;
    return;
  }

  const labels = linhas.map(row => formatarLabelGrafico(row[x]));
  const valores = linhas.map(row => Number(row[y] ?? 0));

  if (!labels.length || !valores.length) {
    chartWrapEl.hidden = true;
    emptyStateEl.hidden = false;
    return;
  }

  if (ragChartInstance) {
    ragChartInstance.destroy();
    ragChartInstance = null;
  }

  let chartType = "bar";

  let data = {
    labels,
    datasets: [
      {
        label: formatarLabelGrafico(viz.titulo || formatarNomeColuna(y)),
        data: valores
      }
    ]
  };

  if (tipo === "line") {
    chartType = "line";
  }

  if (tipo === "pie") {
    chartType = "pie";
    data = {
      labels,
      datasets: [
        {
          data: valores
        }
      ]
    };
  }

  if (tipo === "scatter") {
    chartType = "scatter";
    data = {
      datasets: [
        {
          label: formatarLabelGrafico(viz.titulo || "Dispersão"),
          data: linhas.map(row => ({
            x: Number(row[x] ?? 0),
            y: Number(row[y] ?? 0)
          }))
        }
      ]
    };
  }

  emptyStateEl.hidden = true;
  chartWrapEl.hidden = false;

  ragChartInstance = new Chart(canvas, {
    type: chartType,
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: tipo === "pie" || tipo === "scatter"
        },
        title: {
          display: true,
          text: formatarLabelGrafico(viz.titulo || "Resultado")
        },
        tooltip: {
          callbacks: {
            title: function(context) {
              if (!context || !context.length) return "";
              return formatarLabelGrafico(context[0].label);
            }
          }
        }
      },
      scales: tipo === "pie"
        ? {}
        : {
            x: {
              ticks: {
                callback: function(value) {
                  const label = this.getLabelForValue(value);
                  return formatarLabelGrafico(label);
                }
              }
            },
            y: {
              beginAtZero: true,
              ticks: {
                precision: 0
              }
            }
          }
    }
  });
}

function clearVisualizacao() {
  if (ragChartInstance) {
    ragChartInstance.destroy();
    ragChartInstance = null;
  }

  if (chartWrapEl) {
    chartWrapEl.hidden = true;
  }

  if (tableWrapEl) {
    tableWrapEl.hidden = true;
  }

  if (tabelaEl) {
    tabelaEl.innerHTML = "";
  }

  if (emptyStateEl) {
    emptyStateEl.hidden = false;
  }
}

function showSql(sql) {
  if (!sqlEl || !sqlBoxEl) return;

  sqlEl.textContent = sql || "";
  sqlBoxEl.hidden = false;
}

function hideSql() {
  if (!sqlEl || !sqlBoxEl) return;

  sqlEl.textContent = "";
  sqlBoxEl.hidden = true;
}

function formatarNomeColuna(valor) {
  if (valor === null || valor === undefined) return "";

  const mapa = {
    "genero": "Gênero",
    "gênero": "Gênero",
    "titulo": "Título",
    "título": "Título",
    "autor": "Autor",
    "total": "Total",
    "quantidade": "Quantidade",
    "emprestimos": "Empréstimos",
    "emprestimos_total": "Total de Empréstimos",
    "total_emprestimos": "Total de Empréstimos",
    "livro": "Livro",
    "categoria": "Categoria",
    "mes": "Mês",
    "mês": "Mês",
    "ano": "Ano",
    "usuario": "Usuário",
    "usuário": "Usuário",
    "status": "Status"
  };

  const texto = String(valor).trim();
  const chave = texto.toLowerCase();

  if (mapa[chave]) {
    return mapa[chave];
  }

  return texto
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, letra => letra.toUpperCase());
}

function formatarValorTabela(valor) {
  if (valor === null || valor === undefined) return "";

  const texto = String(valor).trim();

  return formatarLabelGrafico(texto);
}

function formatarLabelGrafico(valor) {
  if (valor === null || valor === undefined) return "";

  const texto = String(valor).trim();

  if (!texto) return "";

  const chave = normalizarTexto(texto);

  const mapa = {
    "ficcao cientifica": "Ficção Científica",
    "ficção cientifica": "Ficção Científica",
    "ficcao científica": "Ficção Científica",
    "ficção científica": "Ficção Científica",
    "filosofia": "Filosofia",
    "ciencia": "Ciência",
    "ciência": "Ciência",
    "tecnologia": "Tecnologia",
  };

  if (mapa[chave]) {
    return mapa[chave];
  }

  return texto
    .replace(/_/g, " ")
    .replace(/-/g, " ")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, letra => letra.toUpperCase());
}

function normalizarTexto(valor) {
  return String(valor || "")
    .trim()
    .replace(/_/g, " ")
    .replace(/-/g, " ")
    .replace(/\s+/g, " ")
    .toLowerCase();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function carregarMetricasServidorRag() {
  const serverStatusEl = document.getElementById("ragServerStatus");
  const serverDotEl = document.getElementById("ragServerDot");

  const gpuUsoEl = document.getElementById("ragGpuUso");
  const gpuMemoriaEl = document.getElementById("ragGpuMemoria");
  const gpuTempEl = document.getElementById("ragGpuTemp");
  const cpuUsoEl = document.getElementById("ragCpuUso");
  const cpuTempEl = document.getElementById("ragCpuTemp");
  const ramUsoEl = document.getElementById("ragRamUso");

  if (!serverStatusEl) return;

  try {
    const resp = await fetch("/funcionario/rag/metricas", {
      method: "GET",
      headers: {
        "Accept": "application/json"
      }
    });

    const data = await resp.json();

    if (!resp.ok || !data.ok) {
      throw new Error(data.erro || "Servidor RAG indisponível.");
    }

    if (serverStatusEl) serverStatusEl.textContent = "Online";

    if (serverDotEl) {
      serverDotEl.classList.remove("is-offline");
      serverDotEl.classList.add("is-online");
    }

    if (gpuUsoEl) {
      gpuUsoEl.textContent = formatarPercentual(data?.gpu?.uso_percent);
    }

    if (gpuMemoriaEl) {
      gpuMemoriaEl.textContent = formatarMemoriaGpu(
        data?.gpu?.memoria_usada_mb,
        data?.gpu?.memoria_total_mb
      );
    }

    if (gpuTempEl) {
      gpuTempEl.textContent = formatarTemperatura(data?.gpu?.temperatura_c);
    }

    if (cpuUsoEl) {
      cpuUsoEl.textContent = formatarPercentual(data?.cpu?.uso_percent);
    }

    if (cpuTempEl) {
      cpuTempEl.textContent = formatarTemperatura(data?.cpu?.temperatura_c);
    }

    if (ramUsoEl) {
      ramUsoEl.textContent = formatarMemoriaRam(
        data?.ram?.usada_gb,
        data?.ram?.total_gb,
        data?.ram?.uso_percent
      );
    }

  } catch (err) {
    console.error("Erro ao carregar métricas do servidor RAG:", err);

    if (serverStatusEl) serverStatusEl.textContent = "Offline";

    if (serverDotEl) {
      serverDotEl.classList.remove("is-online");
      serverDotEl.classList.add("is-offline");
    }

    if (gpuUsoEl) gpuUsoEl.textContent = "—";
    if (gpuMemoriaEl) gpuMemoriaEl.textContent = "—";
    if (gpuTempEl) gpuTempEl.textContent = "—";
    if (cpuUsoEl) cpuUsoEl.textContent = "—";
    if (cpuTempEl) cpuTempEl.textContent = "—";
    if (ramUsoEl) ramUsoEl.textContent = "—";
  }
}

function formatarPercentual(valor) {
  const numero = Number(valor);

  if (!Number.isFinite(numero)) {
    return "—";
  }

  return `${numero.toFixed(0)}%`;
}

function formatarTemperatura(valor) {
  const numero = Number(valor);

  if (!Number.isFinite(numero)) {
    return "—";
  }

  return `${numero.toFixed(0)}°C`;
}

function formatarMemoriaGpu(usadaMb, totalMb) {
  const usada = Number(usadaMb);
  const total = Number(totalMb);

  if (!Number.isFinite(usada) || !Number.isFinite(total) || total <= 0) {
    return "—";
  }

  const usadaGb = usada / 1024;
  const totalGb = total / 1024;

  return `${usadaGb.toFixed(1)}/${totalGb.toFixed(1)} GB`;
}

function formatarMemoriaRam(usadaGb, totalGb, usoPercent) {
  const usada = Number(usadaGb);
  const total = Number(totalGb);
  const percentual = Number(usoPercent);

  if (Number.isFinite(usada) && Number.isFinite(total) && total > 0) {
    if (Number.isFinite(percentual)) {
      return `${usada.toFixed(1)}/${total.toFixed(1)} GB (${percentual.toFixed(0)}%)`;
    }

    return `${usada.toFixed(1)}/${total.toFixed(1)} GB`;
  }

  if (Number.isFinite(percentual)) {
    return `${percentual.toFixed(0)}%`;
  }

  return "—";
}

document.addEventListener("DOMContentLoaded", () => {
  const sqlTrigger = document.getElementById("ragSqlTrigger");
  const sqlFloating = document.getElementById("ragSqlFloating");
  const sqlClose = document.getElementById("ragSqlClose");
  const sqlHead = document.getElementById("ragSqlFloatingHead");

  if (!sqlTrigger || !sqlFloating || !sqlClose || !sqlHead) return;

  sqlTrigger.addEventListener("click", () => {
    sqlFloating.hidden = false;
  });

  sqlClose.addEventListener("click", () => {
    sqlFloating.hidden = true;
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      sqlFloating.hidden = true;
    }
  });

  let arrastando = false;
  let offsetX = 0;
  let offsetY = 0;

  sqlHead.addEventListener("mousedown", (event) => {
    if (event.target.closest("button")) return;

    arrastando = true;

    const rect = sqlFloating.getBoundingClientRect();
    offsetX = event.clientX - rect.left;
    offsetY = event.clientY - rect.top;

    sqlFloating.style.left = `${rect.left}px`;
    sqlFloating.style.top = `${rect.top}px`;
    sqlFloating.style.right = "auto";
    sqlFloating.style.bottom = "auto";

    document.body.style.userSelect = "none";
  });

  document.addEventListener("mousemove", (event) => {
    if (!arrastando) return;

    const larguraJanela = window.innerWidth;
    const alturaJanela = window.innerHeight;
    const rect = sqlFloating.getBoundingClientRect();

    let novoX = event.clientX - offsetX;
    let novoY = event.clientY - offsetY;

    novoX = Math.max(8, Math.min(novoX, larguraJanela - rect.width - 8));
    novoY = Math.max(8, Math.min(novoY, alturaJanela - rect.height - 8));

    sqlFloating.style.left = `${novoX}px`;
    sqlFloating.style.top = `${novoY}px`;
  });

  document.addEventListener("mouseup", () => {
    arrastando = false;
    document.body.style.userSelect = "";
  });
});

carregarMetricasServidorRag();

setInterval(carregarMetricasServidorRag, 2000);