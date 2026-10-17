// Hover layer for server-rendered SVG charts: any element with data-tip shows a tooltip;
// line-chart hit bands (with data-x) also move the crosshair.
(function () {
  const tip = document.createElement("div");
  tip.id = "viz-tip";
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(tip));
  document.addEventListener("pointerover", (e) => {
    const el = e.target.closest && e.target.closest("[data-tip]");
    if (!el) return;
    tip.textContent = el.getAttribute("data-tip");
    tip.style.display = "block";
    const svg = el.ownerSVGElement;
    const cross = svg && svg.querySelector(".crosshair");
    if (cross && el.dataset.x) {
      cross.setAttribute("x1", el.dataset.x);
      cross.setAttribute("x2", el.dataset.x);
      cross.classList.add("on");
    }
  });
  document.addEventListener("pointermove", (e) => {
    if (tip.style.display !== "block") return;
    const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = x + "px";
    tip.style.top = (e.clientY + 14) + "px";
  });
  document.addEventListener("pointerout", (e) => {
    const el = e.target.closest && e.target.closest("[data-tip]");
    if (!el) return;
    tip.style.display = "none";
    const cross = el.ownerSVGElement && el.ownerSVGElement.querySelector(".crosshair");
    if (cross) cross.classList.remove("on");
  });
})();
