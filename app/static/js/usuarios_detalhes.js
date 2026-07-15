// static/js/usuarios_detalhes.js
(function () {
  const cache = new Map();
  let currentAbort = null;

  function escapeHTML(s) {
    return String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function getEls() {
    const backdrop = document.getElementById("udBackdrop");
    const modal = document.getElementById("udModal");
    if (!backdrop || !modal) return null;

    return {
      backdrop,
      modal,
      closeBtn: document.getElementById("udClose"),
      closeBtn2: document.getElementById("udFecharBtn"),
      elAvatar: document.getElementById("udAvatar"),
      elTitle: document.getElementById("udTitle"),
      elSub: document.getElementById("udSub"),
      elFoot: document.getElementById("udFootInfo"),
      elEndereco: document.getElementById("udEndereco"),
      elLivros: document.getElementById("udLivros"),
      elForum: document.getElementById("udForum"),
      tabs: Array.from(modal.querySelectorAll(".ud-tab")),
      panels: Array.from(modal.querySelectorAll(".ud-panel")),
    };
  }

  function openModal(els) {
    els.backdrop.setAttribute("aria-hidden", "false");
    els.modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  }

  function closeModal(els) {
    els.backdrop.setAttribute("aria-hidden", "true");
    els.modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
  }

  function setTab(els, name) {
    els.tabs.forEach((t) => {
      const active = t.dataset.tab === name;
      t.classList.toggle("is-active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });
    els.panels.forEach((p) => {
      const active = p.dataset.panel === name;
      p.classList.toggle("is-active", active);
    });
  }

  function renderLoading(els) {
    els.elAvatar.innerHTML = `<div class="ud-avatar-skel"></div>`;
    els.elTitle.textContent = "Carregando…";
    els.elSub.textContent = "";
    els.elFoot.textContent = "";
    els.elEndereco.innerHTML = `<div class="ud-skel">Carregando endereço…</div>`;
    els.elLivros.innerHTML = `<div class="ud-skel">Carregando livros…</div>`;
    els.elForum.innerHTML = `<div class="ud-skel">Carregando fórum…</div>`;
    setTab(els, "endereco");
  }

  function renderAvatar(els, nome, avatarUrl) {
    if (avatarUrl) {
      els.elAvatar.innerHTML = `
        <img class="ud-avatar-img"
             src="${escapeHTML(avatarUrl)}"
             alt=""
             onerror="this.remove();"
        >
      `;
      setTimeout(() => {
        if (!els.elAvatar.querySelector("img")) {
          const ini =
            (nome || "U")
              .trim()
              .split(/\s+/)
              .slice(0, 2)
              .map((x) => (x[0] ? x[0].toUpperCase() : ""))
              .join("") || "U";
          els.elAvatar.innerHTML = `<div class="ud-avatar-fallback">${escapeHTML(ini)}</div>`;
        }
      }, 250);
      return;
    }

    const ini =
      (nome || "U")
        .trim()
        .split(/\s+/)
        .slice(0, 2)
        .map((x) => (x[0] ? x[0].toUpperCase() : ""))
        .join("") || "U";
    els.elAvatar.innerHTML = `<div class="ud-avatar-fallback">${escapeHTML(ini)}</div>`;
  }

  function renderUsuarioHeader(els, u) {
    const nome = u?.nome || `Usuário #${u?.id ?? "—"}`;
    const email = u?.email || "";
    const online = u?.online ? "Online" : "Offline";
    const last = u?.ultimo_ativo_em ? `Última atividade: ${u.ultimo_ativo_em}` : "Sem atividade registrada";
    const statusConta = u?.status_conta ? u.status_conta : "—";

    els.elTitle.textContent = nome;
    els.elSub.innerHTML = `
      <div class="ud-chips">
        ${email ? `<span class="ud-chip">${escapeHTML(email)}</span>` : ""}
        <span class="ud-chip">${escapeHTML(statusConta)}</span>
        <span class="ud-chip ${u?.online ? "is-online" : "is-offline"}">${online}</span>
      </div>
      <div class="ud-mini">${escapeHTML(last)}</div>
    `;

    renderAvatar(els, nome, u?.avatar_url);
    els.elFoot.textContent = u?.criado_em ? `Criado em: ${u.criado_em}` : "";
  }

  function renderEndereco(els, end) {
    const hasAny = Object.values(end || {}).some((v) => (v ?? "").toString().trim().length > 0);

    if (!hasAny) {
      els.elEndereco.innerHTML = `
        <div class="ud-empty">
          <div class="ud-empty-title">Endereço não cadastrado</div>
          <div class="ud-empty-sub">Nenhuma informação de endereço foi encontrada para este usuário.</div>
        </div>
      `;
      return;
    }

    els.elEndereco.innerHTML = `
      <div class="ud-grid">
        <div class="ud-kv"><span>CEP</span><strong>${escapeHTML(end.cep || "—")}</strong></div>
        <div class="ud-kv"><span>UF</span><strong>${escapeHTML(end.uf || "—")}</strong></div>
        <div class="ud-kv"><span>Cidade</span><strong>${escapeHTML(end.cidade || "—")}</strong></div>
        <div class="ud-kv"><span>Bairro</span><strong>${escapeHTML(end.bairro || "—")}</strong></div>
        <div class="ud-kv ud-kv-wide"><span>Rua</span><strong>${escapeHTML(end.rua || "—")}</strong></div>
        <div class="ud-kv"><span>Número</span><strong>${escapeHTML(end.numero || "—")}</strong></div>
        <div class="ud-kv ud-kv-wide"><span>Complemento</span><strong>${escapeHTML(end.complemento || "—")}</strong></div>
      </div>
    `;
  }

  function renderLivros(els, livros) {
    const atuais = livros?.atuais || [];
    const hist = livros?.historico || [];

    const cardLivro = (x) => `
      <div class="ud-item">
        <div class="ud-item-media">
          ${
            x.capa_url
              ? `<img class="ud-cover" src="${escapeHTML(x.capa_url)}" alt="" onerror="this.remove();">`
              : `<div class="ud-cover ud-cover-fallback" aria-hidden="true"></div>`
          }
        </div>

        <div class="ud-item-main">
          <div class="ud-item-title">
            ${
              x.livro_url
                ? `<a href="${escapeHTML(x.livro_url)}" target="_blank" rel="noopener">${escapeHTML(x.livro_titulo)}</a>`
                : escapeHTML(x.livro_titulo)
            }
          </div>

          <div class="ud-item-sub">
            <span class="ud-badge">${escapeHTML(x.status || "—")}</span>
            ${x.devolver_ate ? `<span class="ud-muted">Devolver até: <strong>${escapeHTML(x.devolver_ate)}</strong></span>` : ""}
            ${x.devolvido_em ? `<span class="ud-muted">Devolvido: <strong>${escapeHTML(x.devolvido_em)}</strong></span>` : ""}
          </div>

          <div class="ud-item-mini">
            ${x.emprestado_em ? `Emprestado: ${escapeHTML(x.emprestado_em)}` : ""}
            ${!x.emprestado_em && x.criado_em ? `Criado: ${escapeHTML(x.criado_em)}` : ""}
          </div>
        </div>
      </div>
    `;

    const bloco = (titulo, arr, emptyText) => `
      <section class="ud-block">
        <div class="ud-block-head">
          <h3>${escapeHTML(titulo)}</h3>
          <span class="ud-count">${arr.length}</span>
        </div>
        <div class="ud-block-body">
          ${
            arr.length
              ? arr.map(cardLivro).join("")
              : `<div class="ud-empty"><div class="ud-empty-sub">${escapeHTML(emptyText)}</div></div>`
          }
        </div>
      </section>
    `;

    els.elLivros.innerHTML =
      bloco("Livros atuais (em posse do usuário)", atuais, "Nenhum livro atual encontrado.") +
      bloco("Histórico completo", hist, "Nenhum histórico encontrado.");
  }

  function renderForum(els, forum) {
    const topicos = forum?.topicos || [];
    const comentarios = forum?.comentarios || [];

    const topicoItem = (t) => `
      <div class="ud-item">
        <div class="ud-item-main">
          <div class="ud-item-title">
            ${t.url ? `<a href="${escapeHTML(t.url)}" target="_blank" rel="noopener">${escapeHTML(t.titulo)}</a>` : escapeHTML(t.titulo)}
          </div>

          <div class="ud-item-sub">
            ${t.criado_em ? `<span class="ud-muted">${escapeHTML(t.criado_em)}</span>` : ""}
            <span class="ud-badge">upvotes: ${escapeHTML(t.upvotes ?? 0)}</span>
            ${t.oculto ? `<span class="ud-badge is-warn">oculto</span>` : ""}
            ${t.trancado ? `<span class="ud-badge is-warn">trancado</span>` : ""}
          </div>

          ${t.trecho ? `<div class="ud-item-mini">${escapeHTML(t.trecho)}</div>` : ""}
        </div>
      </div>
    `;

    const comentarioItem = (c) => `
      <div class="ud-item">
        <div class="ud-item-main">
          <div class="ud-item-title">
            ${
              c.topico_url
                ? `<a href="${escapeHTML(c.topico_url)}" target="_blank" rel="noopener">${escapeHTML(c.topico_titulo || "Tópico")}</a>`
                : escapeHTML(c.topico_titulo || "Tópico")
            }
          </div>

          <div class="ud-item-sub">
            ${c.criado_em ? `<span class="ud-muted">${escapeHTML(c.criado_em)}</span>` : ""}
            <span class="ud-badge">upvotes: ${escapeHTML(c.upvotes ?? 0)}</span>
          </div>

          ${c.trecho ? `<div class="ud-item-mini">${escapeHTML(c.trecho)}</div>` : ""}
        </div>
      </div>
    `;

    const bloco = (titulo, arr, renderer, emptyText) => `
      <section class="ud-block">
        <div class="ud-block-head">
          <h3>${escapeHTML(titulo)}</h3>
          <span class="ud-count">${arr.length}</span>
        </div>
        <div class="ud-block-body">
          ${
            arr.length
              ? arr.map(renderer).join("")
              : `<div class="ud-empty"><div class="ud-empty-sub">${escapeHTML(emptyText)}</div></div>`
          }
        </div>
      </section>
    `;

    els.elForum.innerHTML =
      bloco("Tópicos criados", topicos, topicoItem, "Este usuário não criou tópicos.") +
      bloco("Comentários feitos", comentarios, comentarioItem, "Este usuário não comentou em tópicos.");
  }

  //  FUNÇÃO GLOBAL (sempre existe)
  window.openUserDetailsModal = async function (url) {
    console.log("[openUserDetailsModal] chamado com url =", url);

    if (!url) return;

    const els = getEls();
    if (!els) {
      console.error("Modal udModal/udBackdrop não existe na página. Caindo para navegação.");
      window.location.href = url;
      return;
    }

    openModal(els);
    renderLoading(els);

    if (cache.has(url)) {
      const data = cache.get(url);
      renderUsuarioHeader(els, data.usuario);
      renderEndereco(els, data.endereco);
      renderLivros(els, data.livros);
      renderForum(els, data.forum);
      return;
    }

    if (currentAbort) currentAbort.abort();
    currentAbort = new AbortController();

    try {
      const res = await fetch(url, {
        method: "GET",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "Accept": "application/json"
        },
        signal: currentAbort.signal,
      });

      console.log("[openUserDetailsModal] HTTP", res.status);

      const ct = (res.headers.get("content-type") || "").toLowerCase();
      if (!res.ok) throw new Error("HTTP " + res.status);
      if (!ct.includes("application/json")) throw new Error("Resposta não é JSON: " + ct);

      const data = await res.json();
      if (!data || !data.ok) throw new Error("JSON inválido/ok=false");

      cache.set(url, data);

      renderUsuarioHeader(els, data.usuario);
      renderEndereco(els, data.endereco);
      renderLivros(els, data.livros);
      renderForum(els, data.forum);
    } catch (err) {
      if (String(err).includes("AbortError")) return;
      console.error("[openUserDetailsModal] erro:", err);

      els.elEndereco.innerHTML =
        `<div class="ud-empty">
          <div class="ud-empty-title">Erro ao abrir detalhes</div>
          <div class="ud-empty-sub">${escapeHTML(err?.message || "Não foi possível carregar os detalhes.")}</div>
        </div>`;
      els.elLivros.innerHTML = "";
      els.elForum.innerHTML = "";
    }
  };

  // listeners (tabs/fechar) só se modal existir
  const els0 = getEls();
  if (!els0) return;

  els0.modal.addEventListener("click", (e) => {
    const t = e.target.closest(".ud-tab");
    if (!t) return;
    setTab(els0, t.dataset.tab);
  });

  els0.backdrop.addEventListener("click", () => closeModal(els0));
  els0.closeBtn?.addEventListener("click", () => closeModal(els0));
  els0.closeBtn2?.addEventListener("click", () => closeModal(els0));

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && els0.modal.getAttribute("aria-hidden") === "false") {
      closeModal(els0);
    }
  });
})();