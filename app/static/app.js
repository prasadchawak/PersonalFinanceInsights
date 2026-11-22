// Tiny progressive enhancement. Everything works without it (except select-all).
document.addEventListener("change", (e) => {
  const t = e.target;
  if (t.classList && t.classList.contains("period")) {
    const range = t.form.querySelector(".custom-range");
    if (t.value === "custom") { range && range.classList.remove("hidden"); return; }
  }
  if (t.classList && t.classList.contains("autosubmit")) t.form.submit();
  if (t.classList && (t.classList.contains("rowcb") || t.classList.contains("selall"))) {
    if (t.classList.contains("selall")) document.querySelectorAll(".rowcb").forEach((cb) => (cb.checked = t.checked));
    updateCount();
  }
});
function updateCount() {
  const n = document.querySelectorAll(".rowcb:checked").length;
  const el = document.querySelector(".bulkcount");
  if (el) el.textContent = n + " selected";
  const bar = document.querySelector(".bulkbar");
  if (bar) bar.classList.toggle("active", n > 0);
}
document.addEventListener("submit", (e) => {
  const f = e.target;
  if (f.classList.contains("confirm") && !window.confirm(f.dataset.confirm || "Are you sure?")) { e.preventDefault(); return; }
  if (f.id === "bulk") {
    const scope = f.querySelector("input[name=scope]:checked").value;
    const n = scope === "filter" ? f.querySelector(".bulkcount").dataset.total : document.querySelectorAll(".rowcb:checked").length;
    if (scope === "selected" && n == 0) { e.preventDefault(); alert("Tick at least one transaction first."); return; }
    const sel = f.querySelector("select[name=category_id]");
    const label = sel.options[sel.selectedIndex].title || sel.options[sel.selectedIndex].text.trim();
    if (!window.confirm(`Set category to “${label}” on ${n} transaction(s)?`)) e.preventDefault();
  }
});

// Split editor: live "remaining" and fill-the-rest helper.
(function () {
  const form = document.getElementById("splitform");
  if (!form) return;
  const total = parseInt(form.dataset.total, 10);
  const cents = (v) => { const n = parseFloat(String(v).replace(/[$,\s]/g, "")); return isNaN(n) ? 0 : Math.round(n * 100); };
  const out = form.querySelector(".splitleft");
  const fmt = (c) => (c < 0 ? "-$" : "$") + (Math.abs(c) / 100).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  function recalc() {
    const used = [...form.querySelectorAll(".splitamt")].reduce((s, i) => s + cents(i.value), 0);
    const left = total - used;
    out.textContent = left === 0 ? "✓ $0.00" : fmt(left) + (left < 0 ? " (over)" : "");
    out.className = "splitleft " + (left === 0 ? "pos" : "neg");
  }
  form.addEventListener("input", recalc);
  document.getElementById("fillrest").addEventListener("click", () => {
    const inputs = [...form.querySelectorAll(".splitamt")];
    const others = (skip) => inputs.reduce((s, i) => (i === skip ? s : s + cents(i.value)), 0);
    const target = inputs.filter((i) => i.value.trim()).pop() || inputs[0];
    target.value = ((total - others(target)) / 100).toFixed(2);
    recalc();
  });
  recalc();
})();
