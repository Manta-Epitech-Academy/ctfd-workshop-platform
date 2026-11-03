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
 * Two things the marker can ask for beyond looking:
 *
 * - `cut`: a cut-away. If the file holds a top-level node named `cut`, that
 *   node is the author's own cut-away version (a voxel world re-meshed with
 *   half removed, so the layers and caves show) and the viewer swaps to it;
 *   otherwise the viewer clips every material along the marker's `cut` plane,
 *   which works on any mesh but shows a hollow shell.
 * - `camera`: where to look from and at, in the file's units; without it the
 *   scene is framed from its bounding box, from the corner the subject's own
 *   captures use.
 *
 * Wheel zoom only once the viewer has been clicked, so a scene in the middle of
 * a page never captures the scroll. Rendering is on demand, never a loop: ten
 * scenes on a page cost nothing while nobody moves.
 */
import * as THREE from "./vendor/three.module.min.js";
import { GLTFLoader } from "./vendor/GLTFLoader.js";
import { OrbitControls } from "./vendor/OrbitControls.js";

const SKY = 0x9dd2fa;
const loader = new GLTFLoader();

export function load(url) {
  return new Promise((resolve, reject) => {
    loader.load(url, (gltf) => resolve(gltf), undefined, (err) => reject(err));
  });
}

export function createViewer(stage, canvas, spec, gltf) {
  const features = new Set(spec.features || ["orbit", "zoom", "reset"]);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: "low-power" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(SKY);
  const camera = new THREE.PerspectiveCamera(60, 16 / 10, 0.5, 4000);

  // The file's scene, cloned so a second viewer of the same file (a disposed
  // one coming back) starts from untouched geometry.
  const root = gltf.scene;
  scene.add(root);
  const cutNode = root.children.find((n) => n.name === "cut") || null;
  const mainNodes = root.children.filter((n) => n !== cutNode);
  if (cutNode) cutNode.visible = false;

  // Bounding box of the main nodes: the frame, the floor grid, the default camera.
  const bbox = new THREE.Box3();
  for (const n of mainNodes) bbox.expandByObject(n);
  if (bbox.isEmpty()) bbox.set(new THREE.Vector3(0, 0, 0), new THREE.Vector3(1, 1, 1));
  const size = bbox.getSize(new THREE.Vector3()), center = bbox.getCenter(new THREE.Vector3());
  const span = Math.max(size.x, size.z) || 1;
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

  // Wheel zoom only after a click on the scene; a click elsewhere disarms it.
  function arm() { if (features.has("zoom")) controls.enableZoom = true; stage.classList.add("ws-scene-armed"); }
  function disarm(e) { if (!stage.contains(e.target)) { controls.enableZoom = false; stage.classList.remove("ws-scene-armed"); } }
  canvas.addEventListener("pointerdown", arm);
  document.addEventListener("pointerdown", disarm, true);
  canvas.style.touchAction = "none";

  // The cut-away: the author's node when there is one, a clipping plane otherwise.
  const materials = new Set();
  root.traverse((o) => { if (o.isMesh) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => materials.add(m)); });
  let plane = null;
  if (spec.cut && !cutNode) {
    const normal = new THREE.Vector3(spec.cut.axis === "x" ? -1 : 0, spec.cut.axis === "y" ? -1 : 0, spec.cut.axis === "z" ? -1 : 0);
    plane = new THREE.Plane(normal, Number(spec.cut.at) || 0);
    renderer.localClippingEnabled = true;
  }
  let cutOn = false;
  function setCut(on) {
    cutOn = !!on && features.has("cut") && !!(cutNode || plane);
    if (cutNode) {
      cutNode.visible = cutOn;
      for (const n of mainNodes) n.visible = !cutOn;
    } else if (plane) {
      for (const m of materials) { m.clippingPlanes = cutOn ? [plane] : null; m.needsUpdate = true; }
    }
    request();
  }

  const viewer = {
    features,
    reset: goHome,
    get cut() { return cutOn; },
    setCut,
    dispose() {
      ro.disconnect();
      controls.dispose();
      canvas.removeEventListener("pointerdown", arm);
      document.removeEventListener("pointerdown", disarm, true);
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
