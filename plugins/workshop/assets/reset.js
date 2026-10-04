/* « Reset my progress », in the account menu (PLAN.md §47).
 *
 * The entry is only rendered for an admin (templates/navbar.html) and the
 * endpoint refuses anybody else, so this file is the confirmation and nothing
 * more. It asks first, because the action cannot be undone, and it says what
 * it will not touch.
 */
(function () {
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("[data-ws-reset]");
    if (!link) return;
    event.preventDefault();
    if (!window.confirm(link.getAttribute("data-confirm"))) return;
    var nonce = (window.init && window.init.csrfNonce) || "";
    fetch("/api/v1/workshop/progress/reset", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "CSRF-Token": nonce },
      body: "{}",
    }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      // Back to the start of the subject, which is where a reset leaves you.
      window.location.href = link.getAttribute("href");
    }).catch(function () {
      window.alert(link.getAttribute("data-failed"));
    });
  });
})();
