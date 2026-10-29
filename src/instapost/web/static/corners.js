// Page-corner editor for book photos (Backgrounds -> Corners). Four clicks set the corners.
(function () {
  var box = document.getElementById("corner-editor");
  if (!box) return;
  var img = document.getElementById("corner-photo");
  var svg = document.getElementById("corner-svg");
  var out = document.getElementById("corner-quad");
  var save = document.getElementById("corner-save");
  var count = document.getElementById("corner-count");
  var W = +box.dataset.w, H = +box.dataset.h;
  var pts = [];
  try { pts = JSON.parse(box.dataset.quad || "[]") || []; } catch (e) { pts = []; }
  var NS = "http://www.w3.org/2000/svg";

  function draw() {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (pts.length > 1) {
      var poly = document.createElementNS(NS, pts.length === 4 ? "polygon" : "polyline");
      poly.setAttribute("points", pts.map(function (p) { return p[0] + "," + p[1]; }).join(" "));
      poly.setAttribute("class", "corner-line");
      svg.appendChild(poly);
    }
    pts.forEach(function (p, i) {
      var c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", p[0]); c.setAttribute("cy", p[1]);
      c.setAttribute("r", Math.max(W, H) / 90); c.setAttribute("class", "corner-dot");
      svg.appendChild(c);
      var t = document.createElementNS(NS, "text");
      t.setAttribute("x", p[0] + Math.max(W, H) / 60); t.setAttribute("y", p[1]);
      t.setAttribute("class", "corner-num"); t.setAttribute("font-size", Math.max(W, H) / 30);
      t.textContent = String(i + 1);
      svg.appendChild(t);
    });
    count.textContent = String(pts.length);
    out.value = JSON.stringify(pts);
    save.disabled = pts.length !== 4;
  }

  svg.addEventListener("click", function (ev) {
    if (pts.length >= 4) pts = [];
    var r = img.getBoundingClientRect();
    pts.push([Math.round((ev.clientX - r.left) / r.width * W),
              Math.round((ev.clientY - r.top) / r.height * H)]);
    draw();
  });
  document.getElementById("corner-reset").addEventListener("click", function () {
    pts = []; draw();
  });
  draw();
})();
