/* Click an image, see it whole — and read its caption.
 *
 * Two things this fixes, and they are the same click.
 *
 * Subject images are capped at 26rem tall (workshop.css, and the comment there
 * says why: a 750px screenshot pushed the first step of a part below the fold).
 * That cap is right for reading the page and wrong for the one moment the image
 * is actually doing its job — a participant holding their own screen against
 * it. A console with six lines of a Lua error is not readable at 26rem.
 *
 * And the `alt` text was on screen for nobody. The authoring convention treats
 * it as the caption — « une phrase disant ce que le lecteur doit voir » — and
 * the page rendered it into an attribute that only a screen reader ever reached.
 * Enlarging is the moment that sentence is worth showing, so it comes with it.
 *
 * A `<dialog>`, not a div: the top layer beats the runtime pane's z-index
 * without a number needing to know about it, and Escape, the backdrop and the
 * focus trap are the platform's job rather than ours.
 */
(function () {
  "use strict";

  // The same two containers workshop.css caps, so what is zoomable is exactly
  // what was shrunk. The hero's own media is deliberately not here: it is a
  // demo loop shown at the size it was made for, not a thing to inspect.
  var ZOOMABLE = ".challenge-desc img, .challenge-hints img";

  var dialog = null, big = null, cap = null;

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "ws-zoom";
    // Focusable so `showModal` has somewhere to put focus that is not the close
    // button. Left to itself it focuses the first control, and Chromium counts
    // that programmatic focus as `:focus-visible` — so opening with the mouse
    // drew a keyboard focus ring on the X. Tab still reaches the button.
    dialog.tabIndex = -1;
    dialog.innerHTML =
      '<button type="button" class="ws-zoom-close" aria-label="Close">&times;</button>' +
      '<img class="ws-zoom-img" alt="">' +
      '<p class="ws-zoom-cap"></p>';
    big = dialog.querySelector(".ws-zoom-img");
    cap = dialog.querySelector(".ws-zoom-cap");

    // Anywhere but the caption closes — the caption is text somebody may want
    // to select, and a click that eats a selection reads as a bug. A backdrop
    // click targets the dialog element itself, so this covers that too.
    dialog.addEventListener("click", function (ev) {
      if (ev.target.closest(".ws-zoom-cap")) return;
      dialog.close();
    });
    document.body.appendChild(dialog);
  }

  function open(img) {
    if (!dialog) build();
    big.src = img.currentSrc || img.src;
    // The enlarged copy carries the same description, and the caption below it
    // is the same sentence again — announcing it twice is noise, so the visible
    // one is the one that talks.
    big.alt = "";
    var text = (img.getAttribute("alt") || "").trim();
    cap.textContent = text;
    cap.hidden = !text;
    dialog.showModal();
    dialog.focus();
  }

  document.addEventListener("click", function (ev) {
    var img = ev.target.closest(ZOOMABLE);
    if (!img) return;
    // A linked image belongs to its link. The author meant "go there".
    if (img.closest("a")) return;
    // No source, nothing to enlarge — a broken path should stay visibly broken
    // rather than open an empty dialog on top of the page.
    if (!(img.currentSrc || img.getAttribute("src"))) return;
    ev.preventDefault();
    open(img);
  });
})();
