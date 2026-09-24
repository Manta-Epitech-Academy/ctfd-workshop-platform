/* Take the delete control away from the account the server will refuse to
 * delete (plugins/workshop/protect.py, PLAN.md §40).
 *
 * Why this file exists at all: CTFd's own handlers do
 * `.then(r => { if (r.success) { ...navigate... } })` with no else branch
 * (themes/admin/assets/js/pages/user.js, users.js). A refused delete is
 * therefore a button that closes its confirmation dialog and does nothing —
 * which reads as a bug, and invites the admin to try again.
 *
 * Every selector below is one CTFd's own code already depends on, and every
 * step is guarded: if an upgrade moves any of it, this file quietly does
 * nothing and the server still refuses. It is the explanation, never the
 * protection.
 */
(function () {
  function guardList(id, name) {
    // The users table: one checkbox per row, carrying the id CTFd reads back
    // for both the bulk delete and the bulk edit.
    var box = document.querySelector('input[data-user-id="' + id + '"]');
    if (!box) return;
    box.checked = false;
    box.disabled = true;
    box.title = name + " cannot be deleted: protected administrator";
  }

  function guardPage(id, name) {
    // The single user's page. CTFd puts the id it would delete on `window`,
    // and hangs the handler on `.delete-user` — an anchor, so there is nothing
    // to disable and the control is removed instead. Removing beats unbinding:
    // it holds whether this file runs before or after CTFd binds the click.
    if (window.USER_ID !== id) return;
    var control = document.querySelector("a.delete-user");
    if (!control) return;
    var note = document.createElement("span");
    note.className = "text-muted ws-protected-note";
    note.title = name + " is this instance's protected administrator";
    note.innerHTML =
      '<i class="btn-fa fas fa-shield-alt fa-2x px-2"></i>';
    control.parentNode.replaceChild(note, control);
  }

  function start() {
    if (!window.CTFd || !window.CTFd.fetch) return;
    window.CTFd.fetch("/api/v1/workshop/protected-user", {
      method: "GET",
      headers: { Accept: "application/json" },
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        var keeper = j && j.data;
        if (!keeper) return;
        guardList(keeper.id, keeper.name);
        guardPage(keeper.id, keeper.name);
      })
      .catch(function () { /* the server still refuses; nothing to say here */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
