/* A 3D scene in place of a still (convention §3.8b).
 *
 * scene.py turned the author's `<!-- ws:scene {...} -->` into a hidden
 * `<span class="ws-scene" data-scene="…">` right after the image it replaces.
 * This script finds those anchors, swaps the image for a viewer when it scrolls
 * into view, and keeps the image as the fallback: no WebGL 2, a file that will
 * not load, a browser without ES modules — the still stays, with the zoom and
 * caption it already has.
 *
 * Load on view, not on click (the author's call): a scene is the expected
 * result of a step, so it should be there when the step is read. Three things
 * keep that cheap. The viewer module and three.js are imported once, on the
 * first scene seen. Each scene is fetched only when within 300px of the
 * viewport. And at most LIVE viewers exist at a time: past that, the one
 * farthest from the viewport is disposed and its still comes back, since a
 * browser allows about a dozen WebGL contexts before it drops the oldest on its
 * own, which would leave a blank canvas. Only a scene that is out of view is
 * ever disposed: the observer keeps watching it, and brings it back the moment
 * it is scrolled into view again — a scene disposed while on screen would
 * stay a still until the reader scrolled it out and back, which is what the
 * first version did.
 *
 * Loading and failure both show on the figure, so a scene that is still
 * downloading or could not load never looks like a scene that is simply
 * missing.
 *
 * A failure is retried, because on a poor connection it is usually a download
 * that broke and a reload was the only cure. RETRY gives the waits before the
 * next tries; the still stays meanwhile. A try that falls due while the scene
 * is out of view waits for it to come back. After the last one the still
 * stays with a « Try again » button, which starts over without a reload.
 *
 * The viewer module needs one more step. A browser keeps a failed module
 * download for the life of the page: `import()` of the same file fails again
 * without asking the network, and one broken download used to turn every
 * scene of the page into a still until a reload. So the files of the module
 * (MODULE_FILES) are first downloaded with `fetch`, which can be tried again,
 * and imported only once they all arrived: the import then only revalidates
 * them (`Cache-Control: no-cache`, a 304 each). If the import fails anyway,
 * nothing but a reload can load it, and « Try again » reloads the page.
 *
 * Registered globally like zoom.js: `.challenge-desc` is core's own container,
 * so the statements, the hints and the documents' pages are all covered.
 */
(function () {
  "use strict";

  var SELECTOR = ".challenge-desc .ws-scene, .challenge-hints .ws-scene, .ws-summary-body .ws-scene";
  var LIVE = 3;
  var RETRY = [2000, 5000, 10000];
  var MODULE = "/plugins/workshop/assets/scene-viewer.js";
  // MODULE and every file it imports, directly or not. Keep in step with the
  // imports of scene-viewer.js and of the vendored three.js addons.
  var VENDOR = "/plugins/workshop/assets/vendor/";
  var MODULE_FILES = [MODULE, VENDOR + "three.module.min.js", VENDOR + "GLTFLoader.js",
                      VENDOR + "BufferGeometryUtils.js", VENDOR + "OrbitControls.js"];

  var supported = null;
  function webgl2() {
    if (supported === null) {
      try {
        var c = document.createElement("canvas");
        supported = !!(window.WebGL2RenderingContext && c.getContext("webgl2"));
      } catch (e) { supported = false; }
    }
    return supported;
  }

  var modulePromise = null;
  var moduleBroken = false;   // the import itself failed: only a reload helps
  function download(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error(url + " answered " + r.status);
      return r.arrayBuffer();   // read to the end, so the cache holds all of it
    });
  }
  function viewerModule() {
    if (!modulePromise) {
      modulePromise = Promise.all(MODULE_FILES.map(download)).catch(function (err) {
        modulePromise = null;   // a download failed: the next try starts again
        throw err;
      }).then(function () {
        return import(MODULE).catch(function (err) {
          moduleBroken = true;
          throw err;
        });
      });
    }
    return modulePromise;
  }

  // The page's translated strings (navbar.html), English where there are none.
  var strings = null;
  function t(key, fallback) {
    if (strings === null) {
      try { strings = JSON.parse(document.getElementById("ws-i18n").textContent); }
      catch (e) { strings = {}; }
    }
    return strings[key] || fallback;
  }

  var live = [];   // {figure, viewer}

  function posterOf(anchor) {
    // `<p><img></p>` then the anchor, as cmark renders an image line followed
    // by a comment line; or the image itself when the paragraph was unwrapped.
    var prev = anchor.previousElementSibling;
    if (!prev) return null;
    if (prev.tagName === "IMG") return prev;
    if (prev.tagName === "P" && prev.children.length === 1 && prev.children[0].tagName === "IMG") {
      return prev.children[0];
    }
    return null;
  }

  function label(text) {
    var s = document.createElement("span");
    s.textContent = text;
    return s;
  }

  function build(anchor) {
    var spec;
    try { spec = JSON.parse(anchor.getAttribute("data-scene") || ""); } catch (e) { return; }
    if (!spec || typeof spec.src !== "string") return;
    var img = posterOf(anchor);
    if (!img) return;
    var wrapper = img.parentElement.tagName === "P" ? img.parentElement : img;

    var figure = document.createElement("figure");
    figure.className = "ws-scene-figure";
    var stage = document.createElement("div");
    stage.className = "ws-scene-stage";
    var canvas = document.createElement("canvas");
    canvas.className = "ws-scene-canvas";
    canvas.setAttribute("aria-label", img.alt || "");
    canvas.tabIndex = 0;
    img.classList.add("ws-scene-poster");
    stage.appendChild(img);
    stage.appendChild(canvas);
    var hint = document.createElement("div");
    hint.className = "ws-scene-hint";
    hint.appendChild(label("Drag to turn · click, then scroll to zoom · or use the pads"));
    stage.appendChild(hint);

    // The controls themselves — the cut slider, « Reset view », the pads for
    // a touchpad — are drawn inside the view by the viewer module.
    var caption = document.createElement("figcaption");
    caption.className = "ws-scene-caption";
    caption.textContent = img.alt || "";

    figure.appendChild(stage);
    if (caption.textContent) figure.appendChild(caption);
    wrapper.parentNode.insertBefore(figure, wrapper);
    if (wrapper !== img) wrapper.remove();
    anchor.remove();

    var status = document.createElement("div");
    status.className = "ws-scene-status";
    stage.appendChild(status);
    var state = { figure: figure, stage: stage, canvas: canvas, spec: spec, viewer: null, loading: false,
                  model: null, visible: false, status: status, failures: 0, retry: null };

    observer.observe(stage);
    stage._wsScene = state;
  }

  function activate(state) {
    if (state.viewer || state.loading) return;
    if (state.retry) { clearTimeout(state.retry); state.retry = null; }
    state.loading = true;
    state.figure.classList.remove("ws-scene-failed");
    state.figure.classList.add("ws-scene-loading");
    // A retry keeps its own message rather than flashing back to this one.
    if (!state.failures) state.status.textContent = "Loading the 3D scene…";
    viewerModule().then(function (mod) {
      return (state.model ? Promise.resolve(state.model) : mod.load(state.spec.src)).then(function (model) {
        state.model = model;
        state.viewer = mod.createViewer(state.stage, state.canvas, state.spec, model);
        state.failures = 0;
        state.figure.classList.add("ws-scene-live");
        live.push(state);
        trim();
      });
    }).catch(function (err) {
      // The still is already there; say why in the console, and try again.
      console.warn("workshop: scene not shown, the still stays:", state.spec.src, err);
      state.failures += 1;
      if (state.failures <= RETRY.length && !moduleBroken) {
        state.status.textContent = t("sceneRetry", "The 3D scene did not load. Trying again…");
        state.retry = setTimeout(function () {
          state.retry = null;
          // Out of view: the observer tries again when it is back.
          if (state.visible) activate(state);
        }, RETRY[state.failures - 1]);
        return;
      }
      failed(state);
    }).then(function () {
      state.loading = false;
      state.figure.classList.remove("ws-scene-loading");
      if (state.viewer) state.status.textContent = "";
    });
  }

  function failed(state) {
    state.figure.classList.add("ws-scene-failed");
    state.status.textContent = "";
    state.status.appendChild(label(t("sceneFailed", "The 3D scene could not be loaded.")));
    var again = document.createElement("button");
    again.type = "button";
    again.className = "ws-scene-retry";
    again.textContent = t("sceneTryAgain", "Try again");
    again.addEventListener("click", function () {
      if (moduleBroken) { window.location.reload(); return; }
      state.failures = 0;
      activate(state);
    });
    state.status.appendChild(again);
  }

  function deactivate(state) {
    if (!state.viewer) return;
    state.viewer.dispose();
    state.viewer = null;
    state.figure.classList.remove("ws-scene-live");
    var i = live.indexOf(state);
    if (i >= 0) live.splice(i, 1);
  }

  function distance(state) {
    var r = state.stage.getBoundingClientRect();
    var mid = (r.top + r.bottom) / 2, center = window.innerHeight / 2;
    return Math.abs(mid - center);
  }

  function trim() {
    // Dispose the farthest scenes that are out of view, never one on screen:
    // the observer only fires on a change, so a scene disposed while visible
    // would not come back until scrolled away and back.
    var hidden = live.filter(function (s) { return !s.visible; });
    hidden.sort(function (a, b) { return distance(b) - distance(a); });
    while (live.length > LIVE && hidden.length) deactivate(hidden.shift());
  }

  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      var state = entry.target._wsScene;
      if (!state) return;
      state.visible = entry.isIntersecting;
      if (entry.isIntersecting) activate(state);
    });
  }, { rootMargin: "300px 0px" });

  function scan() {
    if (!webgl2()) return;
    document.querySelectorAll(SELECTOR).forEach(function (anchor) {
      if (anchor._wsSeen) return;
      anchor._wsSeen = true;
      build(anchor);
    });
  }

  function start() {
    scan();
    // Step bodies are filled in place after a solve (workshop_page.js), so new
    // anchors appear later; the same pattern as hints.js.
    new MutationObserver(scan).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
