/* Shared behaviour: confirmation dialogs, double-submit protection, loading state. */
(function () {
  "use strict";
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute("data-confirm") ||
      (e.submitter && e.submitter.getAttribute("data-confirm"));
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    if (form.method && form.method.toLowerCase() === "post") {
      if (form.dataset.submitted === "1") { e.preventDefault(); return; }   // blocks double click / double Enter
      form.dataset.submitted = "1";
      var btns = form.querySelectorAll("button[type=submit], input[type=submit]");
      btns.forEach(function (b) { if (b === e.submitter) { b.classList.add("loading"); } });
      window.setTimeout(function () { form.dataset.submitted = ""; btns.forEach(function (b) { b.classList.remove("loading"); }); }, 8000);
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { var t = document.getElementById("nav-toggle"); if (t) { t.checked = false; } }
  });
  document.querySelectorAll("[data-select-all]").forEach(function (box) {
    box.addEventListener("change", function () {
      document.querySelectorAll(box.getAttribute("data-select-all")).forEach(function (c) { c.checked = box.checked; });
    });
  });
  document.querySelectorAll("[data-print]").forEach(function (b) { b.addEventListener("click", function () { window.print(); }); });
})();
