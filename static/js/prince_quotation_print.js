/* Print from a direct user gesture. No automatic customer sharing. */
(() => {
  "use strict";
  document.getElementById("print-quotation")?.addEventListener("click", () => window.print());
})();
