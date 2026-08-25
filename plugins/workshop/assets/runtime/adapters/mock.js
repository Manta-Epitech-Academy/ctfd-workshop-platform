/* WRP adapter for the mock runtime — the smallest complete example.
 * Injected into the frame by the host, exactly like adapters/tic80.js.
 * A new runtime needs a file this size and nothing more.
 */
(function () {
  var PROTOCOL = 1;
  var step = null;

  function send(msg) {
    msg.wrp = PROTOCOL;
    parent.postMessage(msg, window.location.origin);
  }

  function log(text) {
    var el = document.getElementById("log");
    if (el) el.textContent = text;
  }

  window.addEventListener("message", function (ev) {
    if (ev.source !== parent || ev.origin !== window.location.origin) return;
    var msg = ev.data;
    if (!msg || msg.wrp !== PROTOCOL) return;
    if (msg.type === "init" || msg.type === "step") {
      step = msg.step || null;
      log("active step: " + (step ? step.name : "none"));
    }
  });

  var result = document.getElementById("emit-result");
  if (result) {
    result.addEventListener("click", function () {
      // Advisory only — the host must never turn this into a solve.
      send({ type: "result", step: step, ok: true });
    });
  }
  var propose = document.getElementById("emit-propose");
  if (propose) {
    propose.addEventListener("click", function () {
      send({ type: "propose", step: step, submission: "mock-answer" });
    });
  }

  send({ type: "ready", runtime: "mock", protocol: PROTOCOL,
         capabilities: ["result", "propose"] });
})();
