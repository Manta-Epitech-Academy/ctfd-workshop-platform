/* Workshop hint UX (plugin-only, no CTFd core/theme edit).
 * Injected on every page via {{ Plugins.scripts }} in base.html.
 *
 * Goal: free hints should not read as a paid transaction, while still
 * recording deliberate reveals. CTFd already tracks reveals in the
 * HintUnlocks table (a row is written when the user opens a hint); we keep
 * that. We only change the framing for cost-0 hints:
 *   1. relabel the summary "Unlock Hint for 0 points" -> "View hint"
 *   2. skip the "unlock for 0 points?" confirmation dialog
 * The unlock request still fires on reveal, so HintUnlocks still records
 * who used which hint (deliberate-reveal semantics).
 *
 * Paid hints (cost > 0), if any admin ever adds them, are left untouched.
 */
(function () {
  var FREE_LABEL = "View hint";
  // Matches a cost-0 summary only: "... for 0 point(s)" or "(Cost: 0 point(s))".
  var FREE_RE = /(for 0 point|cost:\s*0\b)/i;

  function relabelFreeHints() {
    var summaries = document.querySelectorAll(
      ".challenge-hints summary:not([data-ws-hint])"
    );
    summaries.forEach(function (s) {
      s.setAttribute("data-ws-hint", "1");
      if (FREE_RE.test((s.textContent || "").trim())) {
        s.textContent = FREE_LABEL;
      }
    });
  }

  // Skip the unlock confirmation for free hints. The reveal still calls
  // loadUnlock() -> POST /unlocks, so the HintUnlocks row is recorded.
  function patchUnlockConfirm() {
    if (!window.CTFd || !CTFd.pages || !CTFd.pages.challenge) return false;
    var chal = CTFd.pages.challenge;
    if (chal._wsHintPatched) return true;
    var orig = chal.displayUnlock.bind(chal);
    chal.displayUnlock = async function (id) {
      try {
        var resp = await chal.loadHint(id); // locked view still includes cost
        if (resp && resp.data && resp.data.cost === 0) return true;
      } catch (e) {
        /* fall through to the normal confirmation */
      }
      return orig(id);
    };
    chal._wsHintPatched = true;
    return true;
  }

  function start() {
    patchUnlockConfirm();
    relabelFreeHints();
    new MutationObserver(function () {
      patchUnlockConfirm(); // CTFd object may appear after first paint
      relabelFreeHints();
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
