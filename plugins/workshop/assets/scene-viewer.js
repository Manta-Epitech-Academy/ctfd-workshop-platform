/* The 3D scene viewer (convention §3.8b), loaded on demand by scene.js.
 *
 * A scene is a glTF binary (.glb): the open Khronos format, so this viewer
 * knows nothing about voxels, Lua or any subject. Whatever a subject exports —
 * today a World Generator world meshed by its runtime's export_scene.py,
 * tomorrow something that is not blocks at all — it shows the same way, with
 * three's own loader and orbit controls.
 *
 * An ES module so it can import three.js and its loader from the vendored
 * module builds (tools/build_vendor.sh rewrites their bare `three` imports to
 * relative paths, so no import map is needed on a page we do not own).
 *
 * The controls live INSIDE the view, drawn by this module:
 *
 * - top left, the cut slider and « Reset view ». The slider runs from 0 %, no
 *   cut, to 100 %, the scene removed up to its far edge, along the marker's
 *   axis. The world is clipped at the slider's position; a clipped mesh is
 *   hollow, so a file may carry **caps** — top-level nodes named
 *   `cap:<axis>:<at>`, the cross-section at that position, baked by the
 *   exporter every few blocks — and the slider then snaps to the positions
 *   that have one and shows it. A file with no caps is simply clipped.
 * - bottom right, soft controls for a touchpad: a rotate pad, a move pad and
 *   zoom buttons, held down to keep going. The mouse keeps dragging and the
 *   wheel, which zooms only once the scene has been clicked, so a scene in the
 *   middle of a page never captures the page's scroll.
 * - top right, « Enlarge »: the view full screen, through the Fullscreen API
 *   where there is one, and pinned over the page where there is none (an
 *   iPhone). Escape, or the same button, brings it back.
 *
 * The controls fade to a quarter when the pointer is elsewhere, so the scene
 * stays the thing on screen; they come back as the pointer reaches the view,
 * fully as it reaches them (workshop.css).
 *
 * `camera: {from, to}` in the marker is the view the scene opens with and the
 * one « Reset view » returns to; without it the scene is framed from its
 * bounding box, from the corner the subject's own captures use.
 *
 * Rendering is on demand, never a loop: ten scenes on a page cost nothing
 * while nobody moves.
 */
import * as THREE from "./vendor/three.module.min.js";
import { GLTFLoader } from "./vendor/GLTFLoader.js";
import { OrbitControls } from "./vendor/OrbitControls.js";

const SKY = 0x9dd2fa;
const AXES = { x: 0, y: 1, z: 2 };
const loader = new GLTFLoader();

export function load(url) {
  return new Promise((resolve, reject) => {
    loader.load(url, (gltf) => resolve(gltf), undefined, (err) => reject(err));
  });
}

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

export function createViewer(stage, canvas, spec, gltf) {
  const features = new Set(spec.features || ["orbit", "zoom", "pan", "reset"]);
  // preserveDrawingBuffer: rendering is on demand, so the last frame has to
  // stay in the canvas between two renders — without it Chrome may discard it
  // after compositing, and a screenshot (a test, a participant saving the
  // page) shows a black band where the buffer was dropped.
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: "low-power",
                                             preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(SKY);
  const camera = new THREE.PerspectiveCamera(60, 16 / 10, 0.5, 4000);

  /* ---------- the file's scene: main nodes, and the caps ---------- */
  const root = gltf.scene;
  scene.add(root);
  const cutAxis = spec.cut && AXES[spec.cut.axis] !== undefined ? spec.cut.axis : null;
  const caps = new Map();          // at -> node, for the marker's axis
  const mainNodes = [];
  for (const n of root.children) {
    const m = /^cap:([xyz]):(-?\d+)$/.exec(n.name || "");
    if (m) {
      n.visible = false;
      if (m[1] === cutAxis) caps.set(Number(m[2]), n);
    } else if (n.name === "cut") {
      // A file from before the slider: a pre-cut copy of the whole scene.
      // Shown with the world it would fight with it on every shared face.
      n.visible = false;
    } else {
      mainNodes.push(n);
    }
  }
  const bbox = new THREE.Box3();
  for (const n of mainNodes) bbox.expandByObject(n);
  if (bbox.isEmpty()) bbox.set(new THREE.Vector3(0, 0, 0), new THREE.Vector3(1, 1, 1));
  const size = bbox.getSize(new THREE.Vector3()), center = bbox.getCenter(new THREE.Vector3());
  const span = Math.max(size.x, size.z) || 1;

  // The world's frame, as in the pane: a faint grid on the floor and the box.
  const grid = new THREE.GridHelper(span, Math.max(2, Math.round(span / 8)), 0x335577, 0x5b7e9e);
  grid.position.set(center.x, bbox.min.y + 0.01, center.z);
  grid.material.transparent = true; grid.material.opacity = 0.5;
  scene.add(grid);
  const frame = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(size.x, size.y, size.z)),
    new THREE.LineBasicMaterial({ color: 0x335577, transparent: true, opacity: 0.25 }));
  frame.position.copy(center);
  scene.add(frame);

  // Lights for a file that is lit (PBR materials). Unlit materials ignore them.
  scene.add(new THREE.HemisphereLight(0xffffff, 0x667788, 1.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.5);
  sun.position.set(bbox.max.x + span, bbox.max.y + size.y, bbox.min.z - span);
  scene.add(sun);

  /* ---------- camera and orbit ---------- */
  const cam = spec.camera || {};
  const home = {
    from: new THREE.Vector3(...(cam.from || [bbox.min.x - 0.23 * span, bbox.max.y + 0.5 * size.y + 0.2 * span, bbox.min.z - 0.23 * span])),
    to: new THREE.Vector3(...(cam.to || [center.x, bbox.min.y + 0.19 * size.y, center.z])),
  };
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = false;
  controls.enableRotate = features.has("orbit");
  controls.enablePan = features.has("pan");
  controls.enableZoom = false;             // armed by a click, below
  controls.minDistance = 2;
  controls.maxDistance = span * 6;
  controls.addEventListener("change", request);

  let pending = false;
  function request() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; renderer.render(scene, camera); });
  }
  function goHome() {
    camera.position.copy(home.from);
    controls.target.copy(home.to);
    controls.update();
    request();
  }
  function resize() {
    const w = stage.clientWidth, h = stage.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    request();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(stage);

  // The soft controls and the keyboard move the camera around the target the
  // same way a drag does: spherical coordinates for turning, the camera's own
  // axes for moving, the distance for zooming. OrbitControls.update() then
  // reads the new position back, so the two never disagree.
  const sph = new THREE.Spherical();
  const offset = new THREE.Vector3();
  function rotate(dTheta, dPhi) {
    if (!features.has("orbit")) return;
    offset.copy(camera.position).sub(controls.target);
    sph.setFromVector3(offset);
    sph.theta -= dTheta;
    sph.phi = Math.max(0.05, Math.min(Math.PI - 0.05, sph.phi - dPhi));
    camera.position.copy(controls.target).add(offset.setFromSpherical(sph));
    controls.update(); request();
  }
  function pan(dx, dy) {
    if (!features.has("pan")) return;
    const k = camera.position.distanceTo(controls.target) * 0.012;
    const right = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0);
    const up = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1);
    const move = right.multiplyScalar(-dx * k).add(up.multiplyScalar(dy * k));
    camera.position.add(move); controls.target.add(move);
    controls.update(); request();
  }
  function zoom(factor) {
    if (!features.has("zoom")) return;
    offset.copy(camera.position).sub(controls.target);
    const d = Math.max(controls.minDistance, Math.min(controls.maxDistance, offset.length() * factor));
    camera.position.copy(controls.target).add(offset.setLength(d));
    controls.update(); request();
  }

  // Wheel zoom only after a click on the scene; a click elsewhere disarms it.
  function arm() { if (features.has("zoom")) controls.enableZoom = true; stage.classList.add("ws-scene-armed"); }
  function disarm(e) { if (!stage.contains(e.target)) { controls.enableZoom = false; stage.classList.remove("ws-scene-armed"); } }
  canvas.addEventListener("pointerdown", arm);
  document.addEventListener("pointerdown", disarm, true);
  canvas.style.touchAction = "none";
  canvas.addEventListener("keydown", (e) => {
    const step = 0.08;
    const map = { ArrowLeft: () => (e.shiftKey ? pan(-1, 0) : rotate(-step, 0)), ArrowRight: () => (e.shiftKey ? pan(1, 0) : rotate(step, 0)),
                  ArrowUp: () => (e.shiftKey ? pan(0, 1) : rotate(0, -step)), ArrowDown: () => (e.shiftKey ? pan(0, -1) : rotate(0, step)),
                  "+": () => zoom(0.9), "=": () => zoom(0.9), "-": () => zoom(1.1) };
    if (map[e.key]) { e.preventDefault(); map[e.key](); }
  });

  /* ---------- the cut: a clipping plane, and the baked caps ---------- */
  const materials = new Set();
  for (const n of mainNodes) n.traverse((o) => { if (o.isMesh) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => materials.add(m)); });
  // The caps sit exactly on the clipping plane, so they get their own copies
  // of the materials, which are never clipped.
  for (const n of caps.values()) n.traverse((o) => { if (o.isMesh) o.material = Array.isArray(o.material) ? o.material.map((m) => m.clone()) : o.material.clone(); });
  let plane = null, cutPct = 0;
  const keepAbove = !(spec.cut && spec.cut.keep === "below");
  if (cutAxis) {
    renderer.localClippingEnabled = true;
    const n = new THREE.Vector3(); n[cutAxis] = keepAbove ? 1 : -1;
    plane = new THREE.Plane(n, 0);
  }
  const capPositions = [...caps.keys()].sort((a, b) => a - b);
  let shownCap = null;
  function setCutPercent(pct) {
    cutPct = Math.max(0, Math.min(100, Number(pct) || 0));
    if (!plane) return;
    const lo = bbox.min[cutAxis], hi = bbox.max[cutAxis];
    let at = keepAbove ? lo + (cutPct / 100) * (hi - lo) : hi - (cutPct / 100) * (hi - lo);
    if (capPositions.length && cutPct > 0 && cutPct < 100) {
      // Snap to the nearest baked cross-section, so the cut always shows one.
      at = capPositions.reduce((best, p) => (Math.abs(p - at) < Math.abs(best - at) ? p : best), capPositions[0]);
    }
    plane.constant = keepAbove ? -at : at;
    const on = cutPct > 0;
    for (const m of materials) { m.clippingPlanes = on ? [plane] : null; m.needsUpdate = true; }
    if (shownCap) shownCap.visible = false;
    shownCap = on && cutPct < 100 ? caps.get(at) || null : null;
    if (shownCap) shownCap.visible = true;
    if (slider) { slider.value = String(cutPct); sliderValue.textContent = Math.round(cutPct) + " %"; }
    request();
  }

  /* ---------- the controls, inside the view ---------- */
  const tools = el("div", "ws-scene-tools");
  let slider = null, sliderValue = null;
  if (features.has("cut") && plane) {
    const row = el("label", "ws-scene-cut");
    row.appendChild(el("span", "ws-scene-cut-label", "Cut"));
    slider = el("input");
    slider.type = "range"; slider.min = "0"; slider.max = "100"; slider.step = "1"; slider.value = "0";
    slider.setAttribute("aria-label", "Cut away");
    slider.addEventListener("input", () => setCutPercent(slider.value));
    sliderValue = el("span", "ws-scene-cut-value", "0 %");
    row.append(slider, sliderValue);
    tools.appendChild(row);
  }
  if (features.has("reset")) {
    const reset = el("button", "ws-scene-btn", "Reset view");
    reset.type = "button";
    reset.addEventListener("click", () => { goHome(); if (slider) setCutPercent(0); });
    tools.appendChild(reset);
  }
  if (tools.children.length) stage.appendChild(tools);

  const pads = el("div", "ws-scene-pads");
  const held = new Set();
  let holdFrame = null;
  function tick() {
    for (const fn of held) fn();
    holdFrame = held.size ? requestAnimationFrame(tick) : null;
  }
  function holdButton(label, title, fn) {
    const b = el("button", "ws-scene-pad-btn", label);
    b.type = "button"; b.title = title; b.setAttribute("aria-label", title);
    const start = (e) => { e.preventDefault(); b.setPointerCapture(e.pointerId); held.add(fn); fn(); if (!holdFrame) holdFrame = requestAnimationFrame(tick); };
    const stop = () => held.delete(fn);
    b.addEventListener("pointerdown", start);
    b.addEventListener("pointerup", stop); b.addEventListener("pointercancel", stop); b.addEventListener("lostpointercapture", stop);
    return b;
  }
  function pad(name, title, actions) {   // actions: {up, down, left, right}
    const p = el("div", "ws-scene-pad");
    p.setAttribute("role", "group"); p.setAttribute("aria-label", title);
    const cells = [null, actions.up, null, actions.left, el("span", "ws-scene-pad-name", name), actions.right, null, actions.down, null];
    for (const c of cells) p.appendChild(c || el("span"));
    return p;
  }
  const r = 0.035;
  if (features.has("orbit")) {
    pads.appendChild(pad("Turn", "Turn the view", {
      up: holdButton("▲", "Tilt up", () => rotate(0, -r)), down: holdButton("▼", "Tilt down", () => rotate(0, r)),
      left: holdButton("◀", "Turn left", () => rotate(-r, 0)), right: holdButton("▶", "Turn right", () => rotate(r, 0)),
    }));
  }
  if (features.has("pan")) {
    pads.appendChild(pad("Move", "Move the view", {
      up: holdButton("▲", "Move up", () => pan(0, 1)), down: holdButton("▼", "Move down", () => pan(0, -1)),
      left: holdButton("◀", "Move left", () => pan(-1, 0)), right: holdButton("▶", "Move right", () => pan(1, 0)),
    }));
  }
  if (features.has("zoom")) {
    const z = el("div", "ws-scene-zoom");
    z.append(holdButton("+", "Zoom in", () => zoom(0.97)), holdButton("−", "Zoom out", () => zoom(1.03)));
    pads.appendChild(z);
  }
  if (pads.children.length) stage.appendChild(pads);

  // Enlarge: full screen on the stage. The ResizeObserver above does the rest.
  const enlarge = el("button", "ws-scene-enlarge", "⛶");
  enlarge.type = "button";
  const canFullscreen = !!(stage.requestFullscreen || stage.webkitRequestFullscreen);
  function isLarge() { return document.fullscreenElement === stage || stage.classList.contains("ws-scene-large"); }
  function labelEnlarge() {
    const on = isLarge();
    enlarge.title = on ? "Exit full screen" : "Enlarge";
    enlarge.setAttribute("aria-label", enlarge.title);
    enlarge.setAttribute("aria-pressed", on ? "true" : "false");
    enlarge.textContent = on ? "✕" : "⛶";
    requestAnimationFrame(resize);
  }
  function toggleEnlarge() {
    if (document.fullscreenElement === stage) { document.exitFullscreen(); return; }
    if (stage.classList.contains("ws-scene-large")) { stage.classList.remove("ws-scene-large"); labelEnlarge(); return; }
    const req = canFullscreen ? (stage.requestFullscreen || stage.webkitRequestFullscreen).call(stage) : Promise.reject();
    Promise.resolve(req).catch(() => { stage.classList.add("ws-scene-large"); labelEnlarge(); });
  }
  enlarge.addEventListener("click", toggleEnlarge);
  document.addEventListener("fullscreenchange", labelEnlarge);
  function escLarge(e) { if (e.key === "Escape" && stage.classList.contains("ws-scene-large")) { stage.classList.remove("ws-scene-large"); labelEnlarge(); } }
  document.addEventListener("keydown", escLarge);
  stage.appendChild(enlarge);
  labelEnlarge();

  const viewer = {
    features,
    reset: () => { goHome(); if (slider) setCutPercent(0); },
    get cut() { return cutPct; },
    setCut: setCutPercent,
    rotate, pan, zoom,
    dispose() {
      ro.disconnect();
      controls.dispose();
      held.clear();
      if (holdFrame) cancelAnimationFrame(holdFrame);
      canvas.removeEventListener("pointerdown", arm);
      document.removeEventListener("pointerdown", disarm, true);
      document.removeEventListener("fullscreenchange", labelEnlarge);
      document.removeEventListener("keydown", escLarge);
      if (document.fullscreenElement === stage) document.exitFullscreen();
      stage.classList.remove("ws-scene-large");
      tools.remove(); pads.remove(); enlarge.remove();
      scene.remove(root);
      grid.geometry.dispose(); frame.geometry.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
    },
  };
  goHome();
  resize();
  return viewer;
}
