// Forms marked data-loading: show a waiting message and block a second submit,
// so a slow plan build or AI call never gets sent twice.
document.addEventListener("submit", function (event) {
  var form = event.target;
  var message = form.getAttribute("data-loading");
  if (!message) return;
  if (form.dataset.submitting) {
    event.preventDefault();
    return;
  }
  form.dataset.submitting = "true";
  var status = document.createElement("p");
  status.className = "form-loading";
  status.setAttribute("role", "status");
  status.textContent = message;
  form.appendChild(status);
  // Disable buttons after the browser has read the clicked button's value.
  setTimeout(function () {
    form.querySelectorAll("button").forEach(function (button) { button.disabled = true; });
  }, 0);
});

// Coming back with the browser's Back button can restore a disabled form; reset it.
window.addEventListener("pageshow", function () {
  document.querySelectorAll("form[data-submitting]").forEach(function (form) {
    delete form.dataset.submitting;
    form.querySelectorAll("button").forEach(function (button) { button.disabled = false; });
    form.querySelectorAll(".form-loading").forEach(function (status) { status.remove(); });
  });
});
