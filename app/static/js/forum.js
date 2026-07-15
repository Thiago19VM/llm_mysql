// app/static/js/forum.js
(function () {
  function toggleHidden(el, show) {
    if (!el) return;
    if (show) el.classList.remove("is-hidden");
    else el.classList.add("is-hidden");
  }

  // Resposta a comentário
  document.querySelectorAll("[data-toggle-reply]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-toggle-reply");
      const form = document.querySelector(`[data-reply-form="${id}"]`);
      if (!form) return;

      const isHidden = form.classList.contains("is-hidden");

      // Fecha outros forms de resposta
      document.querySelectorAll("[data-reply-form]").forEach((f) => f.classList.add("is-hidden"));

      if (isHidden) {
        form.classList.remove("is-hidden");
        const ta = form.querySelector("textarea");
        if (ta) ta.focus();
      }
    });
  });

  document.querySelectorAll("[data-cancel-reply]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-cancel-reply");
      const form = document.querySelector(`[data-reply-form="${id}"]`);
      if (!form) return;
      form.classList.add("is-hidden");
    });
  });

  // Edição de comentário
  document.querySelectorAll("[data-toggle-edit-comment]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-toggle-edit-comment");
      const form = document.querySelector(`[data-edit-comment-form="${id}"]`);
      if (!form) return;

      const isHidden = form.classList.contains("is-hidden");

      // fecha outros forms de edição
      document.querySelectorAll("[data-edit-comment-form]").forEach((f) => f.classList.add("is-hidden"));

      if (isHidden) {
        form.classList.remove("is-hidden");
        const ta = form.querySelector("textarea");
        if (ta) ta.focus();
      }
    });
  });

  document.querySelectorAll("[data-cancel-edit-comment]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.getAttribute("data-cancel-edit-comment");
      const form = document.querySelector(`[data-edit-comment-form="${id}"]`);
      if (!form) return;
      form.classList.add("is-hidden");
    });
  });
})();