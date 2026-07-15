document.addEventListener("DOMContentLoaded", function () {
  const reactionRequestsInFlight = new Set();

  let pendingConfirmForm = null;
  let forumActionInFlight = false;
  let forumAutoRefreshRodando = false;

  setupConfirmModal();

  // ============================================================
  // TOGGLE GENÉRICO: resposta, edição, cancelar etc.
  // ============================================================

  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-toggle-target]");
    if (!btn) return;

    const id = btn.getAttribute("data-toggle-target");
    const el = document.getElementById(id);
    if (!el) return;

    el.classList.toggle("hidden");
  });

  // ============================================================
  // THREADS / RESPOSTAS ANINHADAS
  // ============================================================

  function setChevron(btn, expanded) {
    const chev = btn.querySelector(".chev");

    if (chev) {
      chev.textContent = expanded ? "▾" : "▸";
    }

    btn.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  function setThreadState(targetId, expanded) {
    if (!targetId) return;

    const threadEl = document.getElementById(targetId);
    if (!threadEl) return;

    const btn = document.querySelector(
      `[data-thread-toggle][data-thread-target="${targetId}"]`
    );

    threadEl.classList.toggle("hidden", !expanded);

    if (btn) {
      setChevron(btn, expanded);
    }
  }

  function getOpenThreadIds() {
    return Array.from(
      document.querySelectorAll('[data-thread-toggle][aria-expanded="true"]')
    )
      .map((btn) => btn.getAttribute("data-thread-target"))
      .filter(Boolean);
  }

  function restoreOpenThreadIds(ids) {
    (ids || []).forEach((id) => {
      setThreadState(id, true);
    });
  }

  function openThread(threadEl) {
    if (!threadEl) return;
    setThreadState(threadEl.id, true);
  }

  function expandAncestorsForHash(options = {}) {
    const scroll = options.scroll !== false;
    const highlight = options.highlight !== false;

    const hash = window.location.hash;
    if (!hash) return;

    const el = document.querySelector(hash);
    if (!el) return;

    let p = el.parentElement;

    while (p) {
      if (p.classList && p.classList.contains("thread-replies")) {
        openThread(p);
      }

      p = p.parentElement;
    }

    if (highlight) {
      el.classList.add("ring-2", "ring-sky-300", "ring-offset-2");

      setTimeout(() => {
        el.classList.remove("ring-2", "ring-sky-300", "ring-offset-2");
      }, 1600);
    }

    if (scroll) {
      el.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
    }
  }

  window.expandAncestorsForHash = expandAncestorsForHash;
  window.__forumGetOpenThreadIds = getOpenThreadIds;
  window.__forumRestoreOpenThreadIds = restoreOpenThreadIds;

  window.addEventListener("load", function () {
    expandAncestorsForHash({
      scroll: true,
      highlight: true
    });
  });

  window.addEventListener("hashchange", function () {
    expandAncestorsForHash({
      scroll: true,
      highlight: true
    });
  });

  document.addEventListener("click", function (e) {
    const btn = e.target.closest("[data-thread-toggle]");
    if (!btn) return;

    e.preventDefault();
    e.stopPropagation();

    const targetId = btn.getAttribute("data-thread-target");
    if (!targetId) return;

    const isOpen = btn.getAttribute("aria-expanded") === "true";
    setThreadState(targetId, !isOpen);
  });

  // ============================================================
  // MENU DE REAÇÕES
  // ============================================================

  function closeAllReactionMenus() {
    document.querySelectorAll("[data-reaction-menu]").forEach((menu) => {
      menu.classList.add("hidden");
    });

    document.querySelectorAll("[data-reaction-toggle]").forEach((btn) => {
      btn.setAttribute("aria-expanded", "false");
    });
  }

  document.addEventListener("click", function (e) {
    const toggleBtn = e.target.closest("[data-reaction-toggle]");

    if (toggleBtn) {
      e.stopPropagation();

      const targetId = toggleBtn.getAttribute("data-reaction-toggle");
      const menu = document.getElementById(targetId);

      if (!menu) return;

      const isOpen = !menu.classList.contains("hidden");

      closeAllReactionMenus();

      if (!isOpen) {
        menu.classList.remove("hidden");
        toggleBtn.setAttribute("aria-expanded", "true");
      }

      return;
    }

    const clickedInsideMenu = e.target.closest("[data-reaction-menu]");

    if (!clickedInsideMenu) {
      closeAllReactionMenus();
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      closeAllReactionMenus();
    }
  });

  // ============================================================
  // HELPERS VISUAIS
  // ============================================================

  function setButtonClasses(button, isActive, activeClasses, inactiveClasses) {
    if (!button) return;

    [...activeClasses, ...inactiveClasses].forEach((cls) => {
      button.classList.remove(cls);
    });

    (isActive ? activeClasses : inactiveClasses).forEach((cls) => {
      button.classList.add(cls);
    });
  }

  function applyTopicUpvoteState(button, upvoted, count) {
    const activeClasses = [
      "border-sky-300",
      "bg-sky-50",
      "text-sky-700",
      "hover:bg-sky-100"
    ];

    const inactiveClasses = [
      "border-slate-200",
      "bg-white",
      "text-slate-700",
      "hover:bg-slate-50"
    ];

    setButtonClasses(button, upvoted, activeClasses, inactiveClasses);

    button.dataset.upvoted = upvoted ? "true" : "false";

    const countEl = button.querySelector("[data-topic-upvote-count]");

    if (countEl) {
      countEl.textContent = String(count ?? 0);
    }

    const meta = document.querySelector("[data-topic-upvotes-meta]");

    if (meta) {
      const metaCount = meta.querySelector("[data-topic-upvotes-meta-count]");
      const metaLabel = meta.querySelector("[data-topic-upvotes-meta-label]");

      if (metaCount) {
        metaCount.textContent = String(count ?? 0);
      }

      if (metaLabel) {
        metaLabel.textContent =
          (count ?? 0) === 1
            ? (meta.dataset.singular || "upvote")
            : (meta.dataset.plural || "upvotes");
      }
    }
  }

  function applyCommentUpvoteState(button, upvoted, count) {
    const activeClasses = [
      "border-sky-300",
      "bg-sky-50",
      "text-sky-700",
      "hover:bg-sky-100"
    ];

    const inactiveClasses = [
      "border-slate-200",
      "bg-white",
      "text-slate-700",
      "hover:bg-slate-50"
    ];

    setButtonClasses(button, upvoted, activeClasses, inactiveClasses);

    button.dataset.upvoted = upvoted ? "true" : "false";

    const countEl = button.querySelector("[data-comment-upvote-count]");

    if (countEl) {
      countEl.textContent = String(count ?? 0);
    }
  }

  // ============================================================
  // HELPERS DE COMENTÁRIOS / REAÇÕES
  // ============================================================

  function getReactionContainer(commentId) {
    return document.querySelector(`[data-reactions-container="${commentId}"]`);
  }

  function getReactionMenuButtons(commentId) {
    return Array.from(
      document.querySelectorAll(
        `form[data-ajax-react-comment][data-comment-id="${commentId}"] [data-reaction-menu-btn]`
      )
    );
  }

  function getReactionToggleButton(commentId) {
    return document.querySelector(
      `[data-reaction-toggle="reaction-menu-${commentId}"]`
    );
  }

  function setReactionControlsDisabled(commentId, disabled) {
    const container = getReactionContainer(commentId);

    if (container) {
      container.querySelectorAll("[data-reaction-chip-btn]").forEach((btn) => {
        btn.disabled = disabled;
      });
    }

    getReactionMenuButtons(commentId).forEach((btn) => {
      btn.disabled = disabled;
    });

    const toggleBtn = getReactionToggleButton(commentId);

    if (toggleBtn) {
      toggleBtn.disabled = disabled;
    }
  }

  function getCommentElement(commentId) {
    return document.getElementById(`comentario-${commentId}`);
  }

  function extractTargetHashFromForm(form, fallbackCommentId = "") {
    let targetHash = "";

    if (form instanceof HTMLFormElement) {
      const nextInput = form.querySelector('input[name="next"]');

      if (nextInput && nextInput.value) {
        try {
          const nextUrl = new URL(nextInput.value, window.location.origin);
          targetHash = nextUrl.hash || "";
        } catch (e) {
          const raw = nextInput.value || "";
          const idx = raw.indexOf("#");

          if (idx >= 0) {
            targetHash = raw.slice(idx);
          }
        }
      }
    }

    if (!targetHash && fallbackCommentId) {
      targetHash = `#comentario-${fallbackCommentId}`;
    }

    return targetHash;
  }

  function parseHTMLToDoc(html) {
    return new DOMParser().parseFromString(html, "text/html");
  }

  async function fetchCurrentPageDoc() {
    const res = await fetch(window.location.href, {
      method: "GET",
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html"
      },
      cache: "no-store"
    });

    if (!res.ok) {
      throw new Error("Não foi possível atualizar o fórum.");
    }

    const html = await res.text();
    return parseHTMLToDoc(html);
  }

  // ============================================================
  // ATUALIZAÇÃO DE BLOCOS DA PÁGINA
  // ============================================================

  function replaceSectionsFromDoc(doc, options = {}) {
    const silent = options.silent === true;

    const openThreadIds =
      typeof window.__forumGetOpenThreadIds === "function"
        ? window.__forumGetOpenThreadIds()
        : [];

    const scrollX = window.scrollX;
    const scrollY = window.scrollY;

    const map = [
      "[data-topic-section]",
      "[data-locked-wrap]",
      "[data-comment-composer]",
      "[data-comments-section]"
    ];

    map.forEach((selector) => {
      const currentEl = document.querySelector(selector);
      const nextEl = doc.querySelector(selector);

      if (currentEl && nextEl) {
        currentEl.replaceWith(nextEl);
      }
    });

    if (typeof window.__forumRestoreOpenThreadIds === "function") {
      window.__forumRestoreOpenThreadIds(openThreadIds);
    }

    if (silent) {
      requestAnimationFrame(function () {
        window.scrollTo(scrollX, scrollY);
      });
    }
  }

  async function refreshCommentsUI(targetHash = "", options = {}) {
    const silent = options.silent === true;

    const doc = await fetchCurrentPageDoc();

    replaceSectionsFromDoc(doc, {
      silent: silent
    });

    if (!silent) {
      closeAllReactionMenus();

      if (targetHash) {
        try {
          history.replaceState(null, "", targetHash);
        } catch (e) {
          window.location.hash = targetHash;
        }
      }

      if (typeof window.expandAncestorsForHash === "function") {
        window.expandAncestorsForHash({
          scroll: true,
          highlight: true
        });
      }
    }
  }

  async function refreshSingleCommentFromServer(commentId, form = null) {
    const openThreadIds =
      typeof window.__forumGetOpenThreadIds === "function"
        ? window.__forumGetOpenThreadIds()
        : [];

    const doc = await fetchCurrentPageDoc();

    const currentComment = getCommentElement(commentId);
    const nextComment = doc.getElementById(`comentario-${commentId}`);

    if (currentComment && nextComment) {
      currentComment.replaceWith(nextComment);
    } else {
      const oldSection = document.querySelector("[data-comments-section]");
      const newSection = doc.querySelector("[data-comments-section]");

      if (oldSection && newSection) {
        oldSection.replaceWith(newSection);
      }
    }

    if (typeof window.__forumRestoreOpenThreadIds === "function") {
      window.__forumRestoreOpenThreadIds(openThreadIds);
    }

    closeAllReactionMenus();

    const targetHash = extractTargetHashFromForm(form, commentId);

    if (targetHash) {
      try {
        history.replaceState(null, "", targetHash);
      } catch (e) {
        window.location.hash = targetHash;
      }
    }

    if (typeof window.expandAncestorsForHash === "function") {
      window.expandAncestorsForHash({
        scroll: true,
        highlight: true
      });
    }
  }

  function afterPartialRefresh(form) {
    closeAllReactionMenus();

    const afterHash = form?.dataset?.afterHash || "";

    if (afterHash) {
      try {
        history.replaceState(null, "", afterHash);
      } catch (e) {
        window.location.hash = afterHash;
      }
    }

    if (typeof window.expandAncestorsForHash === "function") {
      window.expandAncestorsForHash({
        scroll: true,
        highlight: true
      });
    }
  }

  // ============================================================
  // POLLING SILENCIOSO
  // ============================================================

  function usuarioEstaInteragindoNoForum() {
    const ativo = document.activeElement;

    const digitando =
      ativo &&
      (
        ativo.tagName === "TEXTAREA" ||
        ativo.tagName === "INPUT" ||
        ativo.isContentEditable
      );

    const modal = document.getElementById("confirm-modal");

    const modalAberto =
      modal &&
      !modal.classList.contains("hidden");

    const menuReacaoAberto =
      !!document.querySelector("[data-reaction-menu]:not(.hidden)");

    const formularioRespostaOuEdicaoAberto =
      !!document.querySelector(
        "[id^='reply-form-']:not(.hidden), [id^='edit-form-']:not(.hidden)"
      );

    return (
      digitando ||
      modalAberto ||
      menuReacaoAberto ||
      formularioRespostaOuEdicaoAberto ||
      pendingConfirmForm ||
      forumActionInFlight ||
      reactionRequestsInFlight.size > 0
    );
  }

  async function atualizarForumAutomaticamente() {
    if (forumAutoRefreshRodando) return;
    if (document.hidden) return;
    if (usuarioEstaInteragindoNoForum()) return;

    forumAutoRefreshRodando = true;

    try {
      await refreshCommentsUI("", {
        silent: true
      });
    } catch (error) {
      console.error("Erro ao atualizar fórum automaticamente:", error);
    } finally {
      forumAutoRefreshRodando = false;
    }
  }

  // Atualiza silenciosamente a cada 10 segundos.
  setInterval(atualizarForumAutomaticamente, 10000);

  // Quando o usuário volta para a aba, tenta sincronizar sem mexer na tela.
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) {
      setTimeout(atualizarForumAutomaticamente, 600);
    }
  });

  // ============================================================
  // POST INTELIGENTE
  // ============================================================

  async function postSmart(form) {
    const res = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/html"
      }
    });

    const url = new URL(res.url, window.location.href);
    const currentUrl = new URL(window.location.href);
    const contentType = res.headers.get("content-type") || "";

    if (contentType.includes("application/json")) {
      const data = await res.json();

      return {
        kind: "json",
        ok: res.ok,
        data: data,
        url: url,
        currentUrl: currentUrl
      };
    }

    const html = await res.text();

    return {
      kind: "html",
      ok: res.ok,
      html: html,
      url: url,
      currentUrl: currentUrl
    };
  }

  function getErrorMessage(data) {
    if (!data) return "Falha na requisição.";

    return (
      data.message ||
      data.mensagem ||
      data.error ||
      data.erro ||
      "Falha na requisição."
    );
  }

  // ============================================================
  // SUBMIT AJAX
  // ============================================================

  document.addEventListener("submit", async function (e) {
    const form = e.target;

    if (!(form instanceof HTMLFormElement)) return;

    if (
      form.matches("[data-confirm-delete]") &&
      form.dataset.confirmApproved !== "true"
    ) {
      e.preventDefault();
      openConfirmModal(form);
      return;
    }

    if (form.dataset.confirmApproved === "true") {
      delete form.dataset.confirmApproved;
    }

    if (e.defaultPrevented) return;

    const isEditComment = form.matches("[data-ajax-edit-comment]");
    const isCreateComment = form.matches("[data-ajax-create-comment]");
    const isReplyComment = form.matches("[data-ajax-reply-comment]");
    const isTopicUpvote = form.matches("[data-ajax-upvote-topic]");
    const isCommentUpvote = form.matches("[data-ajax-upvote-comment]");
    const isReact = form.matches("[data-ajax-react-comment]");
    const isConfirmDelete = form.matches("[data-confirm-delete]");
    const isRefresh = form.matches("[data-ajax-refresh]") || isConfirmDelete;

    const isAjaxForm =
      isEditComment ||
      isCreateComment ||
      isReplyComment ||
      isTopicUpvote ||
      isCommentUpvote ||
      isReact ||
      isRefresh;

    if (!isAjaxForm) return;

    e.preventDefault();

    const nextInput = form.querySelector('input[name="next"]');

    if (nextInput) {
      if (isReact) {
        const id = String(form.dataset.commentId || "");

        if (!nextInput.value && id) {
          nextInput.value =
            `${window.location.pathname}${window.location.search}#comentario-${id}`;
        }
      } else {
        nextInput.value =
          window.location.pathname +
          window.location.search +
          window.location.hash;
      }
    }

    const commentId = isReact ? String(form.dataset.commentId || "") : "";

    if (isReact && commentId) {
      if (reactionRequestsInFlight.has(commentId)) return;

      reactionRequestsInFlight.add(commentId);
      setReactionControlsDisabled(commentId, true);
    }

    const btn = form.querySelector("button[type='submit']");

    if (btn && btn.dataset.loading === "true") {
      if (isReact && commentId) {
        reactionRequestsInFlight.delete(commentId);
        setReactionControlsDisabled(commentId, false);
      }

      return;
    }

    if (btn) {
      btn.dataset.loading = "true";
      btn.disabled = true;
    }

    forumActionInFlight = true;

    try {
      const result = await postSmart(form);

      if (result.kind === "json") {
        const data = result.data;

        if (!result.ok || !data || data.ok === false) {
          throw new Error(getErrorMessage(data));
        }

        if (isTopicUpvote) {
          const button = form.querySelector("[data-topic-upvote-btn]");

          if (button) {
            applyTopicUpvoteState(button, !!data.upvoted, data.count);
          }

          return;
        }

        if (isCommentUpvote) {
          const button = form.querySelector("[data-comment-upvote-btn]");

          if (button) {
            applyCommentUpvoteState(button, !!data.upvoted, data.count);
          }

          return;
        }

        if (isReact) {
          if (!commentId) {
            throw new Error("Comentário inválido para reação.");
          }

          await refreshSingleCommentFromServer(commentId, form);
          return;
        }

        if (isEditComment) {
          const editedCommentId = String(form.dataset.commentId || "");

          if (!editedCommentId) {
            throw new Error("Comentário inválido para edição.");
          }

          await refreshSingleCommentFromServer(editedCommentId, form);

          const editWrap = document.getElementById(`edit-form-${editedCommentId}`);

          if (editWrap) {
            editWrap.classList.add("hidden");
          }

          return;
        }

        if (isCreateComment || isReplyComment) {
          const targetHash = data && data.hash ? data.hash : "";

          await refreshCommentsUI(targetHash, {
            silent: false
          });

          return;
        }

        if (isRefresh) {
          const targetHash = form?.dataset?.afterHash || "";

          await refreshCommentsUI(targetHash, {
            silent: false
          });

          return;
        }

        return;
      }

      if (result.kind === "html") {
        if (
          result.url &&
          result.currentUrl &&
          result.url.href !== result.currentUrl.href
        ) {
          window.location.href = result.url.href;
          return;
        }

        const doc = parseHTMLToDoc(result.html);

        replaceSectionsFromDoc(doc, {
          silent: false
        });

        afterPartialRefresh(form);
        return;
      }
    } catch (err) {
      console.error("Erro AJAX:", err);
      alert(err.message || "Erro ao executar a ação no fórum.");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.dataset.loading = "false";
      }

      if (isReact && commentId) {
        reactionRequestsInFlight.delete(commentId);
        setReactionControlsDisabled(commentId, false);
      }

      forumActionInFlight = false;
    }
  });

  // ============================================================
  // MODAL DE CONFIRMAÇÃO
  // ============================================================

  function getConfirmModalElements() {
    return {
      modal: document.getElementById("confirm-modal"),
      title: document.getElementById("confirm-modal-title"),
      message: document.getElementById("confirm-modal-message"),
      confirmBtn: document.getElementById("confirm-modal-confirm"),
      cancelBtn: document.getElementById("confirm-modal-cancel"),
      closeEls: document.querySelectorAll("[data-confirm-close]")
    };
  }

  function openConfirmModal(form) {
    const { modal, title, message, confirmBtn } = getConfirmModalElements();

    if (!modal) return;

    pendingConfirmForm = form;

    if (title) {
      title.textContent = form.dataset.confirmTitle || "Confirmar ação";
    }

    if (message) {
      message.textContent = form.dataset.confirmMessage || "Tem certeza?";
    }

    if (confirmBtn) {
      confirmBtn.textContent = form.dataset.confirmButton || "Confirmar";
    }

    modal.classList.remove("hidden");
    document.body.classList.add("overflow-hidden");
  }

  function closeConfirmModal() {
    const { modal } = getConfirmModalElements();

    if (!modal) return;

    modal.classList.add("hidden");
    document.body.classList.remove("overflow-hidden");
    pendingConfirmForm = null;
  }

  function setupConfirmModal() {
    const { confirmBtn, cancelBtn, closeEls, modal } = getConfirmModalElements();

    if (!modal) return;

    cancelBtn?.addEventListener("click", closeConfirmModal);

    closeEls.forEach((el) => {
      el.addEventListener("click", closeConfirmModal);
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && pendingConfirmForm) {
        closeConfirmModal();
      }
    });

    confirmBtn?.addEventListener("click", function () {
      if (!pendingConfirmForm) return;

      const form = pendingConfirmForm;

      form.dataset.confirmApproved = "true";

      closeConfirmModal();

      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.dispatchEvent(
          new Event("submit", {
            bubbles: true,
            cancelable: true
          })
        );
      }
    });
  }
});