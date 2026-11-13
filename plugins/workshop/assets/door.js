/* The token door (templates/workshop_token_door.html, tokendoor.py,
 * PLAN.md §61).
 *
 * A participant's account is a token: 128 random bits made here, in this
 * browser, and never sent anywhere. What it looks like, what is derived from
 * it and where the encryption key waits are in assets/token.js, which this
 * file needs loaded before it. A token with a typo in it is never sent to
 * anybody: its checksum is read here.
 *
 * The one box takes a token or a registration code, and this file tells them
 * apart: a token is found by its shape and its checksum; anything else that
 * is 6 to 24 letters and digits is a code (codes.py KEY_MIN, KEY_MAX) and is
 * sent to be checked; the rest is refused here. Whichever it is, the box
 * writes it in groups of four as it is typed (`group`).
 *
 * The token is handed out as a PDF made here (`filePdf`, assets/pdf.js), and
 * read back out of that file as text.
 *
 * Every sentence comes from the template's data attributes, translated.
 */
(function () {
  var root = document.querySelector("[data-ws-door]");
  if (!root) return;

  // The token itself, its text and what is derived from it: assets/token.js,
  // shared with the page that encrypts saved code (assets/runtime.js).
  var T = window.wsToken;
  var TOKEN_BYTES = T.TOKEN_BYTES, TOKEN_CHARS = T.TOKEN_CHARS;
  var tokenText = T.tokenText, fold = T.fold, findToken = T.findToken;
  var derive = T.derive;
  // What a registration code is compared by, once folded (codes.py).
  var KEY_MIN = 6, KEY_MAX = 24;
  // A token file is one page with its fonts in it, some 120 KB. Anything much
  // bigger is not one, and is not read into memory to find that out.
  var FILE_MAX = 262144;

  var text = root.dataset;
  var entry = root.querySelector("[data-ws-door-entry]");
  var form = root.querySelector("[data-ws-door-form]");
  var box = root.querySelector("#ws-door-code");
  var entryError = root.querySelector("#ws-door-error");
  var submit = form.querySelector("[type=submit]");
  var pick = root.querySelector("[data-ws-door-pick]");
  var fileInput = root.querySelector("[data-ws-door-file]");
  var handout = root.querySelector("[data-ws-door-handout]");
  var handoutError = root.querySelector("[data-ws-door-handout-error]");
  var tokenLine = root.querySelector("[data-ws-door-token]");
  var saved = root.querySelector("[data-ws-door-saved]");
  var enter = root.querySelector("[data-ws-door-enter]");
  var page = document.querySelector(".epi-login");
  var title = document.querySelector("[data-ws-login-title]");
  var sub = document.querySelector("[data-ws-login-sub]");

  // The handout's state: the code that was accepted and the token made for
  // it. Gone with the page, on purpose.
  var pending = null;
  var busy = false;
  // What the box held after its last edit (`group`).
  var shown = "";

  // An anonymous visitor at the door means nobody is signed in on this
  // browser, so a key left by whoever was has no owner any more.
  var cleared = root.hasAttribute("data-authed")
    ? Promise.resolve(true)
    : T.forget();

  function keepKey(made) {
    return cleared.then(function () { return T.keep(made); });
  }

  // -------------------------------------------------------------------------
  // The page
  // -------------------------------------------------------------------------

  function say(where, sentence) {
    where.textContent = sentence || "";
    where.hidden = !sentence;
  }

  function working(on) {
    busy = on;
    submit.disabled = on;
    pick.disabled = on;
    enter.disabled = on || !saved.checked;
  }

  // One JSON request. Resolves to the answer's body, or to null when what
  // came back is not this plugin's: core's CSRF check answers with a page,
  // which means this page's nonce is stale (signed in or out in another
  // tab), and only a fresh page has a fresh one.
  function post(path, body) {
    return fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "CSRF-Token": text.nonce },
      body: JSON.stringify(body),
    }).then(function (response) {
      return response.json().catch(function () { return null; });
    }).then(function (answer) {
      if (answer === null || typeof answer.success !== "boolean") {
        window.location.reload();
        return new Promise(function () {});
      }
      return answer;
    });
  }

  // The sentence of a refusal. They are all one sentence but one: a code that
  // is not open yet comes with the instant it opens (codes.py `refusal`),
  // written here in this reader's own time zone, which the server cannot
  // know. `{when}` is where the template's sentence wants it.
  function refusalText(answer) {
    var when = answer.opens ? new Date(answer.opens) : null;
    if (!when || isNaN(when) || !text.early) return answer.message || text.invalid;
    var style = { dateStyle: "long", timeStyle: "short" }, written;
    try { written = when.toLocaleString(text.locale || undefined, style); }
    catch (e) { written = when.toLocaleString(undefined, style); }
    return text.early.replace("{when}", written);
  }

  function go(answer, made) {
    return keepKey(made).then(function () {
      window.location.assign(answer.data.next);
      return new Promise(function () {});
    });
  }

  function signIn(bytes) {
    return derive(bytes).then(function (made) {
      return post("/api/v1/workshop/door/signin", { signin: made.signin })
        .then(function (answer) {
          if (!answer.success) { say(entryError, answer.message || text.invalid); return null; }
          return go(answer, made);
        });
    });
  }

  function showEntry(sentence) {
    pending = null;
    handout.hidden = true;
    entry.hidden = false;
    page.classList.remove("ws-door-handing");
    title.textContent = title.dataset.entry || title.textContent;
    sub.textContent = sub.dataset.entry || sub.textContent;
    say(handoutError, "");
    say(entryError, sentence);
    box.focus();
  }

  function showHandout(code) {
    var bytes = crypto.getRandomValues(new Uint8Array(TOKEN_BYTES));
    return tokenText(bytes).then(function (token) {
      pending = { code: code, bytes: bytes, token: token };
      tokenLine.textContent = "";
      token.split("-").forEach(function (group) {
        var span = document.createElement("span");
        span.textContent = group;
        tokenLine.appendChild(span);
      });
      tokenLine.setAttribute("data-token", token);
      drawQr(token);
      loadBrand();
      saved.checked = false;
      enter.disabled = true;
      title.dataset.entry = title.textContent;
      sub.dataset.entry = sub.textContent;
      title.textContent = text.handoutTitle;
      sub.textContent = text.handoutSub;
      page.classList.add("ws-door-handing");
      entry.hidden = true;
      handout.hidden = false;
      say(entryError, "");
      box.value = shown = "";
      root.querySelector("[data-ws-door-download]").focus();
    });
  }

  // The box writes the dashes: what is typed is shown in groups of four, the
  // way the personal code is printed, so nobody has to count characters or
  // wonder whether the dashes matter. A dash or a space the person types is
  // theirs to type and changes nothing: where a group ends it is the dash
  // (and brings it up before the next character does), anywhere else it is
  // dropped. Only a text that can still be a code is touched, which is
  // letters, digits, dashes and spaces, 28 characters at most: anything
  // else (a pasted sentence with the code in it) is left as it came, and
  // `findToken` reads it as it is.
  //
  // The box cannot know which code is being typed, so the one on the slide is
  // grouped too. Dashes never count (`fold`), so it is only a matter of looks.
  function group(event) {
    if (event.isComposing) return;
    var raw = box.value, caret = box.selectionStart;
    if (caret === null || caret === undefined) caret = raw.length;
    if (/[^0-9A-Za-z -]/.test(raw)) { shown = raw; return; }
    var chars = raw.replace(/[ -]/g, "").toUpperCase();
    if (chars.length > TOKEN_CHARS) { shown = raw; return; }
    var head = raw.slice(0, caret);
    var before = head.replace(/[ -]/g, "").length;
    var value = dashed(chars);
    // Backspace or Delete on a dash alone would be undone by the line above,
    // and the key would look dead: it takes the character next to it.
    var erase = /^deleteContent(Back|For)ward$/.exec(event.inputType || "");
    if (erase && value === shown) {
      var gone = erase[1] === "Back" ? before - 1 : before;
      if (gone >= 0 && gone < chars.length) {
        chars = chars.slice(0, gone) + chars.slice(gone + 1);
        value = dashed(chars);
        before = gone;
      }
    }
    var at = before + (before ? Math.floor((before - 1) / 4) : 0);
    // A dash or a space typed where a group ends.
    var asked = (event.data === "-" || event.data === " ")
      && /[ -]$/.test(head) && before > 0 && before % 4 === 0;
    if (asked && before < chars.length) at += 1;
    else if (asked && chars.length < TOKEN_CHARS) { value += "-"; at += 1; }
    shown = value;
    if (value === raw) return;
    box.value = value;
    box.setSelectionRange(at, at);
  }

  function dashed(chars) {
    return (chars.match(/.{1,4}/g) || []).join("-");
  }

  // What was typed, or read from a file. `fromFile` only changes the sentence
  // for a text with nothing in it: a file is never taken for a code.
  function take(string, fromFile) {
    if (busy) return;
    say(entryError, "");
    working(true);
    findToken(string).then(function (found) {
      if (found.bytes) return signIn(found.bytes);
      if (fromFile) { say(entryError, found.bad ? text.invalid : text.noCode); return null; }
      var key = fold(string);
      // Shaped like a token and wrong, or not shaped like a code either:
      // refused here, and nothing leaves the browser.
      if (found.bad || key.length < KEY_MIN || key.length > KEY_MAX) {
        say(entryError, text.invalid);
        return null;
      }
      var code = string.trim();
      return post("/api/v1/workshop/door/code", { code: code })
        .then(function (answer) {
          if (!answer.success) { say(entryError, refusalText(answer)); return null; }
          return showHandout(code);
        });
    }).catch(function () {
      say(entryError, text.failed);
    }).then(function () {
      working(false);
      if (!entryError.hidden) box.focus();
    });
  }

  function register() {
    if (busy || !pending || !saved.checked) return;
    var mine = pending;
    say(handoutError, "");
    working(true);
    derive(mine.bytes).then(function (made) {
      return post("/api/v1/workshop/door/register",
                  { code: mine.code, signin: made.signin })
        .then(function (answer) {
          // The code closed between the two requests (the last place under a
          // cap went to somebody else). Back to the box, with the answer.
          if (!answer.success) { showEntry(refusalText(answer)); return null; }
          return go(answer, made);
        });
    }).catch(function () {
      say(handoutError, text.failed);
    }).then(function () {
      working(false);
    });
  }

  // -------------------------------------------------------------------------
  // Three ways to keep the token
  // -------------------------------------------------------------------------

  function address() {
    return window.location.origin + window.location.pathname;
  }

  // What the file is drawn with: the platform's typefaces and its wordmark,
  // as a PDF takes them (assets/pdf-brand.json). Asked for when the personal
  // code shows, so that it is here when the button is pressed. Null when it
  // cannot be had: the file is then plainer, and still handed out.
  var brand = null;
  function loadBrand() {
    if (!brand) {
      brand = fetch(text.fileBrand, { credentials: "omit" }).then(function (response) {
        return response.ok ? response.json() : null;
      }).catch(function () { return null; }).then(function (found) {
        if (!found) brand = null;
        return found;
      });
    }
    return brand;
  }

  // The platform's colours (assets/epitech-theme.css, the light theme: a
  // printed page is white). The three squares are white over the blue at
  // one half, one quarter and a third, as the header draws them.
  var INK = {
    blue: "#013afb", green: "#00ff97", white: "#ffffff", text: "#181818",
    muted: "#555b71", line: "#d9dce8", well: "#eceef5",
    squares: ["#809dfd", "#416bfc", "#5a7ffc"],
  };
  // A bank card, ISO 7810 ID-1: 85.60 by 53.98 mm, in points.
  var CARD = { wide: 242.65, high: 153.01, radius: 9, band: 47, pad: 14 };

  function upper(sentence) {
    return String(sentence || "").toLocaleUpperCase(text.locale || undefined);
  }

  // One card, (x, y) its bottom left corner: the platform's blue header with
  // the workshop's name, then the code, the address and the QR code. It is
  // drawn at a bank card's size and takes its place as arguments, so a sheet
  // of them is this function in a loop.
  function drawCard(pdf, x, y, token) {
    var w = CARD.wide, h = CARD.high, pad = CARD.pad, inner = w - 2 * pad;
    var band = y + h - CARD.band;
    pdf.box(x, y, w, h, { fill: INK.white, radius: CARD.radius });
    pdf.clip(x, y, w, h, CARD.radius);
    pdf.box(x, band, w, CARD.band, { fill: INK.blue });
    pdf.box(x + w - 14, y + h - 14, 14, 14, { fill: INK.squares[0] });
    pdf.box(x + w - 23, y + h - 14, 7, 14, { fill: INK.squares[1] });
    pdf.box(x + w - 7, y + h - 23, 7, 7, { fill: INK.squares[2] });
    pdf.unclip();
    pdf.box(x, y, w, h, { stroke: INK.line, line: 0.6, radius: CARD.radius });
    pdf.logo(x + pad, y + h - 10 - 10.5, 10.5, INK.white);

    // The workshop's name, as the header writes it: capitals, and the green
    // cursor after it. A long one is set smaller, then cut.
    var name = upper(text.fileTitle), size = 15;
    function across(string) { return pdf.measure(string + "_", "display", size); }
    while (size > 10 && across(name) > inner) size -= 0.5;
    if (across(name) > inner) {
      while (name.length > 1 && across(name + "\u2026") > inner) name = name.slice(0, -1);
      name = name.replace(/\s+$/, "") + "\u2026";
    }
    pdf.text(name, x + pad, band + 9, "display", size, INK.white);
    pdf.text("_", x + pad + pdf.measure(name, "display", size), band + 9, "display", size,
             INK.green);

    // The code, on one line: it is what `readFile` looks for.
    var label = 5.5, tracking = 0.6;
    pdf.text(upper(text.fileCode), x + pad, band - 12.5, "bold", label, INK.muted, tracking);
    var well = band - 41;
    pdf.box(x + pad, well, inner, 23, { fill: INK.well, stroke: INK.line, radius: 4 });
    var mono = 9.5;
    pdf.text(token, x + (w - pdf.measure(token, "mono", mono)) / 2, well + 8.2, "mono", mono,
             INK.text);

    // The QR code on the right, the address beside it.
    var side = 50, qr = qrOf(token), room = inner;
    if (qr) {
      pdf.cells(x + w - pad - side, y + 10, side, qr.getModuleCount(),
                function (r, c) { return qr.isDark(r, c); });
      room = inner - side - 10;
    }
    // Beside it, the address from its top and the warning up from its foot.
    pdf.text(upper(text.fileAddress), x + pad, y + 56, "bold", label, INK.muted, tracking);
    // The address is a link: "open the address above" is then one click.
    var here = address(), small = 7.5;
    while (small > 5 && pdf.measure(here, "bold", small) > room) small -= 0.25;
    var at = y + 46;
    pdf.wrap(here, "bold", small, room).slice(0, 2).forEach(function (line) {
      pdf.text(line, x + pad, at, "bold", small, INK.blue);
      pdf.link(x + pad, at - 2, pdf.measure(line, "bold", small), small + 3, here);
      at -= small + 1.5;
    });
    var lines = pdf.wrap(text.fileLost, "regular", label, room).slice(0, 3);
    lines.forEach(function (line, i) {
      pdf.text(line, x + pad, y + 11 + (lines.length - 1 - i) * (label + 1.5), "regular", label,
               INK.muted);
    });
  }

  // The file is a PDF, made here because the token is nowhere else
  // (assets/pdf.js). Not a text file: that opens in an editor, where one
  // stray key changes a character of the code and saves it. The token is
  // written in it as plain text, which is how `readFile` finds it again.
  //
  // One page: the card at the top, at the size it would be cut out at, and
  // under it what to do with it.
  function filePdf(token, found) {
    var pdf = window.wsPdf(found);
    var left = (pdf.width - CARD.wide) / 2, y = pdf.height - 72 - CARD.high;
    drawCard(pdf, left, y, token);
    y -= 34;
    [text.fileBack, text.fileKeep, text.fileLost].forEach(function (sentence) {
      pdf.wrap(sentence, "regular", 10.5, CARD.wide).forEach(function (line) {
        pdf.text(line, left, y, "regular", 10.5, INK.text);
        y -= 15.5;
      });
      y -= 8;
    });
    return pdf.build();
  }

  function download() {
    if (!pending) return;
    var token = pending.token;
    loadBrand().then(function (found) {
      var blob = new Blob([filePdf(token, found)], { type: "application/pdf" });
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = text.fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
    });
  }

  function selectToken() {
    var range = document.createRange();
    range.selectNodeContents(tokenLine);
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function copy(button) {
    if (!pending) return;
    var label = button.querySelector("[data-ws-door-copy-label]");
    var before = label.dataset.before || label.textContent;
    label.dataset.before = before;
    // No clipboard (an old browser, a refused permission): the code is
    // selected instead, which is the half of copying a person can finish.
    if (!navigator.clipboard || !navigator.clipboard.writeText) { selectToken(); return; }
    navigator.clipboard.writeText(pending.token).then(function () {
      label.textContent = text.copied;
      setTimeout(function () { label.textContent = before; }, 2000);
    }, selectToken);
  }

  // The address of this page with the token as its fragment: a fragment is
  // not sent to the server, so scanning the picture signs the phone in and
  // the token still never crosses the network. Null without the library.
  function qrOf(token) {
    if (typeof window.qrcode !== "function") return null;
    try {
      var qr = window.qrcode(0, "M");
      qr.addData(address() + "#" + token);
      qr.make();
      return qr;
    } catch (e) { return null; }
  }

  // Drawn as SVG through the DOM, dark on white whatever the theme (see the
  // template).
  function drawQr(token) {
    var figure = root.querySelector("[data-ws-door-qr]");
    var holder = root.querySelector("[data-ws-door-qr-code]");
    holder.textContent = "";
    var qr = qrOf(token);
    if (!qr) { figure.hidden = true; return; }
    var count = qr.getModuleCount(), quiet = 4, size = count + 2 * quiet;
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 " + size + " " + size);
    svg.setAttribute("shape-rendering", "crispEdges");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "QR code");
    var back = document.createElementNS(ns, "rect");
    back.setAttribute("width", size);
    back.setAttribute("height", size);
    back.setAttribute("fill", "#ffffff");
    svg.appendChild(back);
    var d = "";
    for (var r = 0; r < count; r++) {
      for (var c = 0; c < count; c++) {
        if (qr.isDark(r, c)) d += "M" + (c + quiet) + " " + (r + quiet) + "h1v1h-1z";
      }
    }
    var dots = document.createElementNS(ns, "path");
    dots.setAttribute("d", d);
    dots.setAttribute("fill", "#000000");
    svg.appendChild(dots);
    holder.appendChild(svg);
    figure.hidden = false;
  }

  // -------------------------------------------------------------------------
  // Wiring
  // -------------------------------------------------------------------------

  function readFile(file) {
    if (!file) return;
    if (file.size > FILE_MAX) { say(entryError, text.noCode); return; }
    var reader = new FileReader();
    reader.onload = function () { take(String(reader.result || ""), true); };
    reader.onerror = function () { say(entryError, text.failed); };
    reader.readAsText(file);
  }

  // Web Crypto exists only on https and on localhost. Without it there is
  // nothing this page can do, and saying so beats a button that does nothing.
  if (!window.isSecureContext || !window.crypto || !window.crypto.subtle) {
    say(entryError, text.insecure);
    box.disabled = true;
    submit.disabled = true;
    pick.disabled = true;
    return;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    take(box.value, false);
  });
  box.addEventListener("input", function (event) {
    say(entryError, "");
    group(event);
  });
  // A keyboard that composes (a phone's) is left to finish its word first.
  box.addEventListener("compositionend", group);

  pick.addEventListener("click", function () { fileInput.click(); });
  fileInput.addEventListener("change", function () {
    readFile(fileInput.files && fileInput.files[0]);
    // So that choosing the same file again fires `change` again.
    fileInput.value = "";
  });

  // The file can be dropped anywhere on the page: nobody aims at a box.
  function carriesFile(event) {
    var types = event.dataTransfer && event.dataTransfer.types;
    return !!types && Array.prototype.indexOf.call(types, "Files") >= 0;
  }
  document.addEventListener("dragover", function (event) {
    if (!carriesFile(event)) return;
    event.preventDefault();
    if (!entry.hidden) root.classList.add("ws-door-dropping");
  });
  document.addEventListener("dragleave", function (event) {
    if (!event.relatedTarget) root.classList.remove("ws-door-dropping");
  });
  document.addEventListener("drop", function (event) {
    if (!carriesFile(event)) return;
    // Always: a file dropped beside the box must not replace the page.
    event.preventDefault();
    root.classList.remove("ws-door-dropping");
    if (!entry.hidden) readFile(event.dataTransfer.files[0]);
  });

  root.querySelector("[data-ws-door-download]").addEventListener("click", download);
  root.querySelector("[data-ws-door-copy]").addEventListener("click", function (event) {
    copy(event.currentTarget);
  });
  saved.addEventListener("change", function () {
    enter.disabled = busy || !saved.checked;
  });
  enter.addEventListener("click", register);

  // Arriving with a token in the fragment: the QR code, scanned. It is taken
  // out of the address bar before anything else, so it is neither left on
  // screen nor kept in the history of a browser that may not be theirs.
  if (window.location.hash.length > 1) {
    var carried;
    try { carried = decodeURIComponent(window.location.hash.slice(1)); }
    catch (e) { carried = ""; }
    window.history.replaceState(
      null, "", window.location.pathname + window.location.search);
    findToken(carried).then(function (found) {
      if (found.bytes || found.bad) take(carried, true);
    });
  }
})();
