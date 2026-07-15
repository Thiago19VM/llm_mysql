document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".js-confirm-form").forEach((form) => {
    form.addEventListener("submit", function (e) {
      e.preventDefault();

      const title = form.dataset.confirmTitle || "Confirmar ação?";
      const text = form.dataset.confirmText || "Deseja continuar?";
      const icon = form.dataset.confirmIcon || "warning";
      const confirmButtonText = form.dataset.confirmButton || "Confirmar";
      const cancelButtonText = form.dataset.cancelButton || "Cancelar";

      Swal.fire({
        title,
        text,
        icon,
        showCancelButton: true,
        confirmButtonText,
        cancelButtonText,
        reverseButtons: true,
        focusCancel: true,
        confirmButtonColor: "#1f6fb9",
        cancelButtonColor: "#94a3b8",
        background: "#ffffff",
        borderRadius: 18
      }).then((result) => {
        if (result.isConfirmed) {
          form.submit();
        }
      });
    });
  });

  // Toasts globais vindos do Flask
  if (window.FLASH_MESSAGES && Array.isArray(window.FLASH_MESSAGES)) {
    window.FLASH_MESSAGES.forEach((item) => {
      let icon = "info";

      if (item.category === "success") icon = "success";
      else if (item.category === "error" || item.category === "danger") icon = "error";
      else if (item.category === "warning") icon = "warning";

      Swal.fire({
        toast: true,
        position: "top-end",
        icon,
        title: item.message,
        showConfirmButton: false,
        timer: 3200,
        timerProgressBar: true,
        background: "#ffffff",
        borderRadius: 14
      });
    });
  }
});