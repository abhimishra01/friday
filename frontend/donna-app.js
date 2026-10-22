/* Starts the Friday page: the light or dark button, then the Friday screen. */
(function () {
  var U = window.BotsUtil, btn = document.querySelector('[data-k="theme-btn"]'), sec = document.querySelector(".donna");
  function sync() { var p = U.paper(); btn.textContent = p ? "Dark" : "Light"; btn.setAttribute("aria-pressed", p ? "true" : "false"); sec.setAttribute("data-theme", p ? "light" : "dark"); }
  btn.addEventListener("click", function () { U.setPaper(!U.paper()); sync(); });
  window.BotsDonna.init({ onChange: function () { sync(); } });
  window.BotsDonna.render();
  sync();
})();
