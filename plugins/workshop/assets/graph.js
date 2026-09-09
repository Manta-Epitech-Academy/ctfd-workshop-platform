/* Workshop challenge-graph UI (plugin-only, injected via {{ Plugins.scripts }}).
 * Two features off one endpoint (GET /api/v1/workshop/graph):
 *   A. branching "next challenge" button(s) in the challenge modal on solve
 *   B. the "Parcours" page: a layered SVG of the DAG with the taken path
 * All strings English (i18n later, per CLAUDE.md). No CTFd core edit.
 */
(function () {
  var CACHE = null;
  async function fetchGraph(force) {
    if (CACHE && !force) return CACHE;
    var r = await window.CTFd.fetch("/api/v1/workshop/graph", {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    var j = await r.json();
    CACHE = j.data;
    return CACHE;
  }

  function byId(nodes) {
    var m = {};
    nodes.forEach(function (n) { m[n.id] = n; });
    return m;
  }

  /* ---------- A. next-challenge buttons in the modal ---------- */

  function goTo(id) {
    // Mirror the theme's nextChallenge(): close the modal, then ask the board
    // to load the target (the board listens on @load-challenge.window).
    var modalEl = document.querySelector("[x-ref='challengeWindow']");
    var done = function () {
      window.dispatchEvent(new CustomEvent("load-challenge", { detail: id }));
    };
    if (window.bootstrap && modalEl) {
      var m = window.bootstrap.Modal.getOrCreateInstance(modalEl);
      modalEl.addEventListener("hidden.bs.modal", done, { once: true });
      m.hide();
    } else {
      done();
    }
  }

  function currentChallengeId() {
    var el = document.querySelector("#challenge-id");
    return el ? parseInt(el.value, 10) : null;
  }

  function onChallengeBoard() {
    return !!document.querySelector("[x-ref='challengeWindow']");
  }

  function challengeUrl(node) {
    // The board reads window.location.hash on init and opens that challenge:
    // it splits on the LAST "-", so only the trailing id matters (themes/core/
    // assets/js/challenges.js). Keep the name in front for a readable URL —
    // that is exactly what CTFd itself writes back into the address bar.
    var root = (window.CTFd && window.CTFd.config && window.CTFd.config.urlRoot) || "";
    return root + "/challenges#" + encodeURIComponent(node.name) + "-" + node.id;
  }

  function openChallenge(node) {
    // Already on the board (graph embedded there): swap in place, no reload.
    if (onChallengeBoard()) {
      goTo(node.id);
      return;
    }
    window.location.href = challengeUrl(node);
  }

  function renderNextButtons(container, graph, currentId) {
    var map = byId(graph.nodes);
    var solved = new Set(graph.solved);
    var successors = (graph.next[currentId] || []).map(function (i) { return map[i]; })
      .filter(Boolean)
      // only steps that are actually reachable now and not already done
      .filter(function (n) { return n.unlocked && !solved.has(n.id); });

    container.innerHTML = "";
    if (successors.length === 0) return;

    if (successors.length > 1) {
      var h = document.createElement("div");
      h.className = "ws-next-heading";
      h.textContent = "Multiple paths ahead — pick one:";
      container.appendChild(h);
    }
    successors.forEach(function (n) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-info mt-2 ws-next-btn";
      b.textContent = (successors.length > 1 ? "" : "Next challenge: ") + n.name;
      b.addEventListener("click", function () { goTo(n.id); });
      container.appendChild(b);
    });
  }

  async function onSolved(alertEl) {
    var currentId = currentChallengeId();
    if (currentId == null) return;
    var host = alertEl.querySelector(".ws-next");
    if (!host) {
      host = document.createElement("div");
      host.className = "ws-next mt-3";
      // place it above the rating block if present, else at the end
      alertEl.appendChild(host);
    }
    var graph = await fetchGraph(true); // solve just changed the solved set
    renderNextButtons(host, graph, currentId);
  }

  function watchModal() {
    new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        m.addedNodes.forEach(function (nd) {
          if (nd.nodeType !== 1) return;
          var alertEl = nd.classList && nd.classList.contains("alert-success")
            ? nd : (nd.querySelector && nd.querySelector(".alert-success"));
          if (alertEl) onSolved(alertEl);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }

  /* ---------- B. Parcours page (layered SVG DAG) ---------- */

  function layout(nodes, map) {
    // Vertical roadmap: row = longest prerequisite depth (so every edge points
    // downward), column = branch lane within that row. A workshop is mostly a
    // long chain with a few forks, so going top-to-bottom keeps the graph the
    // width of its widest fork instead of the length of the whole chain.
    var depth = {};
    function d(n) {
      if (depth[n.id] != null) return depth[n.id];
      var pr = (n.prerequisites || []).filter(function (p) { return map[p]; });
      depth[n.id] = pr.length === 0 ? 0
        : 1 + Math.max.apply(null, pr.map(function (p) { return d(map[p]); }));
      return depth[n.id];
    }
    nodes.forEach(d);
    var rows = {};
    nodes.slice().sort(function (a, b) { return a.position - b.position; })
      .forEach(function (n) { (rows[depth[n.id]] = rows[depth[n.id]] || []).push(n); });
    var pos = {};
    Object.keys(rows).forEach(function (r) {
      rows[r].forEach(function (n, col) { pos[n.id] = { row: +r, col: col }; });
    });
    return pos;
  }

  function svgEl(tag, attrs) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function renderParcours(root, graph) {
    var nodes = graph.nodes, map = byId(nodes), solved = new Set(graph.solved);
    var pos = layout(nodes, map);
    var CW = 240, CH = 48, GX = 40, GY = 34; // cell/gap sizes
    var maxCol = 0, maxRow = 0;
    nodes.forEach(function (n) {
      maxCol = Math.max(maxCol, pos[n.id].col);
      maxRow = Math.max(maxRow, pos[n.id].row);
    });
    var W = (maxCol + 1) * CW + maxCol * GX + 40;
    var H = (maxRow + 1) * CH + maxRow * GY + 40;
    var xy = function (n) {
      return {
        x: 20 + pos[n.id].col * (CW + GX),
        y: 20 + pos[n.id].row * (CH + GY),
      };
    };

    var svg = svgEl("svg", { width: W, height: H, class: "ws-parcours-svg" });
    var defs = svgEl("defs", {});
    ["ws-arrow-taken #00ff97", "ws-arrow #8b90a3"].forEach(function (spec) {
      var parts = spec.split(" ");
      var mk = svgEl("marker", {
        id: parts[0], markerWidth: 8, markerHeight: 8, refX: 7, refY: 3,
        orient: "auto", markerUnits: "strokeWidth",
      });
      mk.appendChild(svgEl("path", { d: "M0,0 L7,3 L0,6 Z", fill: parts[1] }));
      defs.appendChild(mk);
    });
    svg.appendChild(defs);

    // edges (prereq -> dependent); taken = both ends solved
    nodes.forEach(function (n) {
      (graph.next[n.id] || []).forEach(function (depId) {
        var dep = map[depId]; if (!dep) return;
        var a = xy(n), b = xy(dep);
        var taken = solved.has(n.id) && solved.has(dep.id);
        // vertical flow: leave the bottom edge, enter the top edge
        svg.appendChild(svgEl("line", {
          x1: a.x + CW / 2, y1: a.y + CH, x2: b.x + CW / 2, y2: b.y,
          stroke: taken ? "#00ff97" : "#8b90a3",
          "stroke-width": taken ? 3 : 1.5,
          "stroke-opacity": taken ? 0.95 : 0.4,
          "marker-end": taken ? "url(#ws-arrow-taken)" : "url(#ws-arrow)",
        }));
      });
    });

    // nodes — each unlocked one is a real link to its challenge
    nodes.forEach(function (n) {
      var p = xy(n);
      var state = n.solved ? "solved" : (n.unlocked ? "open" : "locked");
      var fill = { solved: "#0b2318", open: "#0e1a33", locked: "#1b1f29" }[state];
      var stroke = { solved: "#00ff97", open: "#809dfd", locked: "#262b38" }[state];
      var g = svgEl("g", { class: "ws-node ws-" + state });
      g.appendChild(svgEl("rect", {
        x: p.x, y: p.y, width: CW, height: CH, rx: 0,
        fill: fill, stroke: stroke, "stroke-width": n.solved ? 2 : 1.5,
        "stroke-opacity": state === "locked" ? 0.5 : 1,
      }));
      var label = svgEl("text", {
        x: p.x + 12, y: p.y + CH / 2 + 4,
        fill: state === "locked" ? "#8b90a3" : "#f1f2f6",
        "font-size": 12, "font-family": "monospace",
      });
      var name = n.name.length > 26 ? n.name.slice(0, 25) + "…" : n.name;
      label.textContent = (n.solved ? "✓ " : "") + name;
      g.appendChild(label);

      // Tooltip: the label is truncated, and locked steps need an explanation.
      var tip = svgEl("title", {});
      tip.textContent = n.unlocked
        ? n.name + " — open this challenge"
        : "Locked — solve its prerequisites first";
      g.appendChild(tip);

      if (n.unlocked) {
        // <a> (not just a click handler) so the box behaves like a link:
        // middle-click / ctrl-click open it in a new tab, and it is focusable.
        var a = svgEl("a", { class: "ws-node-link" });
        a.setAttribute("href", challengeUrl(n));
        a.addEventListener("click", function (ev) {
          if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
          ev.preventDefault();
          openChallenge(n);
        });
        a.appendChild(g);
        svg.appendChild(a);
      } else {
        g.classList.add("ws-node-locked");
        svg.appendChild(g);
      }
    });

    root.innerHTML = "";
    var hint = document.createElement("div");
    hint.className = "ws-parcours-hint";
    hint.textContent = "Click a step to open it.";
    root.appendChild(hint);
    var legend = document.createElement("div");
    legend.className = "ws-parcours-legend";
    legend.innerHTML =
      "<span class='ws-lg ws-lg-solved'>done</span>" +
      "<span class='ws-lg ws-lg-open'>available</span>" +
      "<span class='ws-lg ws-lg-locked'>locked</span>" +
      "<span class='ws-lg ws-lg-path'>your path</span>";
    root.appendChild(legend);
    var scroll = document.createElement("div");
    scroll.className = "ws-parcours-scroll";
    scroll.appendChild(svg);
    root.appendChild(scroll);
  }

  async function maybeRenderParcours() {
    var root = document.querySelector("#ws-parcours");
    if (!root || root.dataset.wsRendered) return;
    root.dataset.wsRendered = "1";
    try {
      renderParcours(root, await fetchGraph(true));
    } catch (e) {
      root.textContent = "Could not load the path graph.";
    }
  }

  function start() {
    watchModal();
    maybeRenderParcours();
    // the page content may be injected after first paint
    new MutationObserver(maybeRenderParcours).observe(document.body, {
      childList: true, subtree: true,
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
