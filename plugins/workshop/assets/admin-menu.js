/* Fold this plugin's admin entries into one "Workshop" dropdown.
 *
 * `register_admin_plugin_menu_bar` can only make flat entries, and this plugin
 * has nine of them. Nine top-level items push CTFd's own Config off the end of
 * the bar and bury the two that get used during a session.
 *
 * Why JS and not a template override: the markup lives in `admin/base.html`,
 * which also carries `{{ Plugins.scripts }}`, the nonce island and every future
 * upstream change to the admin shell. Owning it to group nine links would be the
 * trade shell.py already refuses for the participant base.html — a large surface
 * taken over for a small gain.
 *
 * Shape copied from the theme's own Pages and Submissions dropdowns
 * (Bootstrap 4, `data-toggle`), so it matches whatever the theme does with them.
 *
 * Every step is guarded. If an upgrade moves any of this the entries simply stay
 * flat, which is where they are today: this file tidies the bar, it is not what
 * makes the pages reachable.
 */
(function () {
  var LABEL = "Workshop";
  var PREFIX = "/admin/workshop/";

  function start() {
    var bar = document.querySelector("#base-navbars .navbar-nav");
    if (!bar) return;

    // Ours are the plugin entries pointing into /admin/workshop/. Read from the
    // href rather than the label: the labels are ours to rename, the routes are
    // what the blueprint actually registers.
    var mine = [];
    Array.prototype.forEach.call(bar.querySelectorAll("li.nav-item > a.nav-link"),
      function (a) {
        var href = a.getAttribute("href") || "";
        if (href.indexOf(PREFIX) !== -1 && !a.classList.contains("dropdown-toggle")) {
          mine.push(a);
        }
      });
    // One entry is not a menu. With the workshop switched off (toggle.py) that
    // is exactly the case, and a dropdown holding a single item would be worse
    // than the link it replaced.
    if (mine.length < 2) return;

    var first = mine[0].parentNode;
    var item = document.createElement("li");
    // Put the dropdown where the first entry is **before** emptying the list.
    // Removing `first` inside the loop below and then reading `first.parentNode`
    // gives null, which is how the first version deleted all nine entries and
    // inserted nothing.
    first.parentNode.insertBefore(item, first);
    item.className = "nav-item dropdown";

    var toggle = document.createElement("a");
    toggle.href = "#";
    toggle.className = "nav-link dropdown-toggle";
    toggle.setAttribute("data-toggle", "dropdown");
    toggle.setAttribute("role", "button");
    toggle.setAttribute("aria-haspopup", "true");
    toggle.setAttribute("aria-expanded", "false");
    toggle.textContent = LABEL;
    item.appendChild(toggle);

    var menu = document.createElement("div");
    menu.className = "dropdown-menu";
    mine.forEach(function (a) {
      var link = document.createElement("a");
      link.className = "dropdown-item";
      link.href = a.getAttribute("href");
      link.textContent = (a.textContent || "").trim();
      if (a.getAttribute("target")) link.setAttribute("target", a.getAttribute("target"));
      menu.appendChild(link);
      var li = a.parentNode;
      if (li && li.parentNode) li.parentNode.removeChild(li);
    });
    item.appendChild(menu);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
