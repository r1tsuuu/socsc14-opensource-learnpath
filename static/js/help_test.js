// Sends the test form to /help/ask/ as JSON and prints the reply.
(function () {
  var form = document.getElementById("help-test-form");
  var output = document.getElementById("help-test-output");
  if (!form) return;

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    output.textContent = "Asking…";
    fetch(form.dataset.url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": form.querySelector("[name=csrfmiddlewaretoken]").value,
      },
      body: JSON.stringify({
        topic: form.topic.value,
        hint_level: Number(form.hint_level.value),
        question: form.question.value,
        screen: "plan",
      }),
    })
      .then(function (response) { return response.json(); })
      .then(function (data) { output.textContent = JSON.stringify(data, null, 2); })
      .catch(function (error) { output.textContent = "Request failed: " + error; });
  });
})();
