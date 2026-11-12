/* An animation with controls (convention §3.8d).
 *
 * The page carries, for every animation, the animated image the author wrote
 * and `<span class="ws-anim" data-anim="…">` right after it (anim.py). This
 * swaps the image for a <video> of the same frames under our own controls:
 * play/pause, a progress bar with a tick at each chapter, and ‹ › buttons that
 * jump to the previous or next state the author marked in the scene (manim
 * sections, written as WebVTT by the capture skill). The browser's native
 * controls are not used: they have no chapters, and they look different in
 * every browser.
 *
 * Fallback: when the video cannot load, the image comes back as it was. The
 * chapters file is fetched and parsed here (a handful of lines) rather than
 * through <track>, whose cues only appear once the track is loaded and whose
 * timing of that differs per browser.
 */
(function () {
  "use strict";

  var SELECTOR = ".challenge-desc .ws-anim, .challenge-hints .ws-anim, .ws-summary-body .ws-anim";

  function posterOf(anchor) {
    var prev = anchor.previousElementSibling;
    if (!prev) return null;
    if (prev.tagName === "IMG") return prev;
    if (prev.tagName === "P" && prev.children.length === 1 && prev.children[0].tagName === "IMG") {
      return prev.children[0];
    }
    return null;
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function parseTime(s) {
    var parts = s.trim().split(":").map(parseFloat);
    while (parts.length < 3) parts.unshift(0);
    return parts[0] * 3600 + parts[1] * 60 + parts[2];
  }

  function parseVtt(text) {
    var cues = [];
    var blocks = text.replace(/\r/g, "").split(/\n\n+/);
    blocks.forEach(function (block) {
      var lines = block.split("\n").filter(Boolean);
      for (var i = 0; i < lines.length; i++) {
        var m = /^(\S+)\s+-->\s+(\S+)/.exec(lines[i]);
        if (m) {
          cues.push({ start: parseTime(m[1]), end: parseTime(m[2]), name: lines.slice(i + 1).join(" ") });
          break;
        }
      }
    });
    return cues;
  }

  function fmt(t) {
    t = Math.max(0, Math.floor(t || 0));
    return Math.floor(t / 60) + ":" + ("0" + (t % 60)).slice(-2);
  }

  function build(anchor) {
    var spec;
    try { spec = JSON.parse(anchor.getAttribute("data-anim") || ""); } catch (e) { return; }
    if (!spec || typeof spec.src !== "string") return;
    var img = posterOf(anchor);
    if (!img) return;
    var wrapper = img.parentElement.tagName === "P" ? img.parentElement : img;

    var figure = el("figure", "ws-anim-figure");
    var stage = el("div", "ws-anim-stage");
    var video = document.createElement("video");
    video.className = "ws-anim-video";
    video.preload = "metadata";
    video.playsInline = true;
    video.muted = true;
    video.loop = true;
    video.setAttribute("aria-label", img.alt || "");
    video.src = spec.src;
    img.classList.add("ws-anim-poster");
    stage.appendChild(img);
    stage.appendChild(video);

    var bar = el("div", "ws-anim-bar");
    var btnPlay = el("button", "ws-anim-btn ws-anim-play", "▶");
    btnPlay.type = "button";
    btnPlay.title = "Lecture / pause (Espace)";
    var btnPrev = el("button", "ws-anim-btn", "‹");
    btnPrev.type = "button";
    btnPrev.title = "État précédent (←)";
    var btnNext = el("button", "ws-anim-btn", "›");
    btnNext.type = "button";
    btnNext.title = "État suivant (→)";
    var progress = el("div", "ws-anim-progress");
    progress.setAttribute("role", "slider");
    progress.tabIndex = 0;
    var fill = el("div", "ws-anim-fill");
    progress.appendChild(fill);
    var ticks = el("div", "ws-anim-ticks");
    progress.appendChild(ticks);
    var chapter = el("span", "ws-anim-chapter", "");
    var time = el("span", "ws-anim-time", "0:00");
    var btnFull = el("button", "ws-anim-btn ws-anim-full", "⛶");
    btnFull.type = "button";
    btnFull.title = "Plein écran (F)";
    bar.appendChild(btnPlay);
    bar.appendChild(btnPrev);
    bar.appendChild(btnNext);
    bar.appendChild(progress);
    bar.appendChild(time);
    bar.appendChild(btnFull);
    var line2 = el("div", "ws-anim-line");
    line2.appendChild(chapter);

    var caption = el("figcaption", "ws-anim-caption", img.alt || "");
    figure.appendChild(stage);
    figure.appendChild(bar);
    figure.appendChild(line2);
    if (caption.textContent) figure.appendChild(caption);
    wrapper.parentNode.insertBefore(figure, wrapper);
    if (wrapper !== img) wrapper.remove();
    anchor.remove();

    var cues = [];
    var duration = 0;

    function current() {
      var t = video.currentTime;
      for (var i = cues.length - 1; i >= 0; i--) if (t >= cues[i].start - 0.05) return i;
      return -1;
    }

    function refresh() {
      var d = duration || video.duration || 0;
      fill.style.width = d ? (100 * video.currentTime / d) + "%" : "0";
      time.textContent = fmt(video.currentTime) + " / " + fmt(d);
      var i = current();
      chapter.textContent = i >= 0 && cues[i].name ? ((i + 1) + "/" + cues.length + " · " + cues[i].name) : "";
      btnPlay.textContent = video.paused ? "▶" : "❚❚";
      figure.classList.toggle("ws-anim-playing", !video.paused);
    }

    function seek(t) {
      video.currentTime = Math.max(0, Math.min(duration || video.duration || 0, t));
      refresh();
    }

    function toggle() {
      if (video.paused) video.play().catch(function () {}); else video.pause();
      refresh();
    }

    function jump(delta) {
      if (!cues.length) { seek(video.currentTime + delta * 5); return; }
      var i = current();
      // Going back from inside a chapter restarts it; from its first second, the previous one.
      if (delta < 0 && i >= 0 && video.currentTime - cues[i].start > 1) delta = 0;
      var target = Math.max(0, Math.min(cues.length - 1, i + delta));
      seek(cues[target].start);
      video.pause();
      refresh();
    }

    // Full screen: the figure itself, so the bar and its chapters come along.
    // Where the API is missing (iPhone), the figure is pinned over the page.
    var canFullscreen = !!(figure.requestFullscreen || figure.webkitRequestFullscreen);
    function isLarge() {
      return document.fullscreenElement === figure || figure.classList.contains("ws-anim-large");
    }
    function labelFull() {
      btnFull.textContent = isLarge() ? "✕" : "⛶";
      btnFull.title = isLarge() ? "Quitter le plein écran (F, Échap)" : "Plein écran (F)";
    }
    function toggleFull() {
      if (document.fullscreenElement === figure) { document.exitFullscreen(); return; }
      if (figure.classList.contains("ws-anim-large")) { figure.classList.remove("ws-anim-large"); labelFull(); return; }
      var req = canFullscreen ? (figure.requestFullscreen || figure.webkitRequestFullscreen).call(figure) : Promise.reject();
      Promise.resolve(req).catch(function () { figure.classList.add("ws-anim-large"); labelFull(); });
    }
    btnFull.addEventListener("click", toggleFull);
    document.addEventListener("fullscreenchange", labelFull);
    btnPlay.addEventListener("click", toggle);
    btnPrev.addEventListener("click", function () { jump(-1); });
    btnNext.addEventListener("click", function () { jump(1); });
    video.addEventListener("click", toggle);
    progress.addEventListener("click", function (ev) {
      var r = progress.getBoundingClientRect();
      seek((ev.clientX - r.left) / r.width * (duration || video.duration || 0));
    });
    figure.addEventListener("keydown", function (ev) {
      if (ev.key === " ") { ev.preventDefault(); toggle(); }
      else if (ev.key === "ArrowLeft") { ev.preventDefault(); jump(-1); }
      else if (ev.key === "ArrowRight") { ev.preventDefault(); jump(1); }
      else if (ev.key === "f" || ev.key === "F") { ev.preventDefault(); toggleFull(); }
      else if (ev.key === "Escape" && figure.classList.contains("ws-anim-large")) { figure.classList.remove("ws-anim-large"); labelFull(); }
    });
    figure.tabIndex = 0;
    video.addEventListener("timeupdate", refresh);
    video.addEventListener("play", refresh);
    video.addEventListener("pause", refresh);
    video.addEventListener("loadedmetadata", function () {
      duration = video.duration;
      figure.classList.add("ws-anim-ready");
      refresh();
    });
    video.addEventListener("error", function () {
      // The animated image is still there, under the video: show it alone.
      console.warn("workshop: animation not played, the image stays:", spec.src);
      figure.classList.add("ws-anim-failed");
    });

    if (spec.chapters) {
      fetch(spec.chapters).then(function (r) { return r.ok ? r.text() : ""; }).then(function (text) {
        cues = parseVtt(text || "");
        ticks.replaceChildren();
        cues.forEach(function (c, i) {
          if (i === 0) return;
          var tick = el("span", "ws-anim-tick");
          tick.title = c.name;
          tick.dataset.at = c.start;
          ticks.appendChild(tick);
        });
        var place = function () {
          var d = duration || video.duration || 0;
          if (!d) return;
          Array.prototype.forEach.call(ticks.children, function (tick) {
            tick.style.left = (100 * parseFloat(tick.dataset.at) / d) + "%";
          });
        };
        place();
        video.addEventListener("loadedmetadata", place);
        refresh();
      }).catch(function () {});
    }
    refresh();
  }

  function init() {
    document.querySelectorAll(SELECTOR).forEach(build);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  // Content loaded later (a step opened, a hint revealed) gets the same treatment.
  new MutationObserver(function () {
    document.querySelectorAll(SELECTOR).forEach(build);
  }).observe(document.body, { childList: true, subtree: true });
})();
