/* The token door (templates/workshop_token_door.html, tokendoor.py,
 * PLAN.md §60).
 *
 * A participant's account is a token: 128 random bits made here, in this
 * browser, and never sent anywhere. Two values are derived from it with
 * HKDF-SHA-256:
 *
 *   sign-in      32 bytes, sent as hex. The server stores their SHA-256.
 *   encryption   an AES-GCM key that cannot be exported, kept in IndexedDB
 *                for as long as this browser is signed in. Nothing reads it
 *                yet; it is what saved code will be encrypted with.
 *
 * The token as a person sees it is 28 characters in seven groups of four:
 * 26 of Crockford's base32 for the 128 bits (the last character carries two
 * padding bits, always zero) and 2 more that are a checksum, the first ten
 * bits of the SHA-256 of the 16 bytes. The checksum is not security. It is
 * what lets this page say "that is mistyped" without asking the server, so a
 * token with a typo in it is never sent to anybody.
 *
 * The one box takes a token or a registration code, and this file tells them
 * apart: a token is found by its shape and its checksum; anything else that
 * is 6 to 24 letters and digits is a code (codes.py KEY_MIN, KEY_MAX) and is
 * sent to be checked; the rest is refused here.
 *
 * Every sentence comes from the template's data attributes, translated.
 */
(function () {
  var root = document.querySelector("[data-ws-door]");
  if (!root) return;

  // Crockford's base32, the same string as codes.py ALPHABET.
  var ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  var TOKEN_BYTES = 16;
  var TOKEN_CHARS = 28;
  // What a registration code is compared by, once folded (codes.py).
  var KEY_MIN = 6, KEY_MAX = 24;
  // Frozen: changing any of the three strings signs every account out for
  // good, because nobody holds the token to derive the new value from.
  var SALT = "ctfd-workshop-token-door-v1";
  var INFO_SIGNIN = "sign-in";
  var INFO_ENCRYPTION = "encryption";
  // Where the encryption key waits for the page that will use it.
  var DB_NAME = "ws-door", DB_STORE = "keys", DB_KEY = "encryption";
  // A token file is a few lines. Anything much bigger is not one, and is not
  // read into memory to find that out.
  var FILE_MAX = 65536;

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

  // -------------------------------------------------------------------------
  // The token, as bytes and as text
  // -------------------------------------------------------------------------

  function utf8(string) {
    return new TextEncoder().encode(string);
  }

  function hex(bytes) {
    return Array.prototype.map.call(bytes, function (b) {
      return (b < 16 ? "0" : "") + b.toString(16);
    }).join("");
  }

  // Bytes to base32, most significant bit first, zero-padded at the end.
  function base32(bytes) {
    var out = "", acc = 0, bits = 0;
    for (var i = 0; i < bytes.length; i++) {
      acc = (acc << 8) | bytes[i];
      bits += 8;
      while (bits >= 5) {
        out += ALPHABET[(acc >>> (bits - 5)) & 31];
        bits -= 5;
      }
      acc &= (1 << bits) - 1;
    }
    if (bits) out += ALPHABET[(acc << (5 - bits)) & 31];
    return out;
  }

  // The two checksum characters of 16 bytes.
  function checksum(bytes) {
    return crypto.subtle.digest("SHA-256", bytes).then(function (digest) {
      var h = new Uint8Array(digest);
      return ALPHABET[h[0] >>> 3] + ALPHABET[((h[0] & 7) << 2) | (h[1] >>> 6)];
    });
  }

  function tokenText(bytes) {
    return checksum(bytes).then(function (check) {
      return (base32(bytes) + check).match(/.{4}/g).join("-");
    });
  }

  // What a hand-typed text is compared by: the same folding as codes.py
  // `code_key`, so the page and the server agree on what was typed.
  function fold(string) {
    return String(string || "").toUpperCase().replace(/[^0-9A-Z]/g, "")
      .replace(/O/g, "0").replace(/[IL]/g, "1");
  }

  // 28 folded characters to 16 bytes, or null: a character outside the
  // alphabet, padding bits that are not zero, or a checksum that is wrong.
  function parse(folded) {
    if (folded.length !== TOKEN_CHARS) return Promise.resolve(null);
    var bytes = new Uint8Array(TOKEN_BYTES), acc = 0, bits = 0, at = 0;
    for (var i = 0; i < TOKEN_CHARS - 2; i++) {
      var value = ALPHABET.indexOf(folded[i]);
      if (value < 0) return Promise.resolve(null);
      acc = (acc << 5) | value;
      bits += 5;
      if (bits >= 8) {
        if (at < TOKEN_BYTES) bytes[at++] = (acc >>> (bits - 8)) & 255;
        bits -= 8;
        acc &= (1 << bits) - 1;
      }
    }
    if (at !== TOKEN_BYTES || acc !== 0) return Promise.resolve(null);
    return checksum(bytes).then(function (check) {
      return check === folded.slice(TOKEN_CHARS - 2) ? bytes : null;
    });
  }

  // Find a token in a text: what was typed in the box, a file, the address's
  // fragment. Resolves to {bytes} when one is found, {bad: true} when the
  // text is shaped like a token and is not one (a typo), {} otherwise.
  function findToken(string) {
    var upper = String(string || "").toUpperCase();
    var shape = /(^|[^0-9A-Z])((?:[0-9A-Z]{4}[- ]?){6}[0-9A-Z]{4})(?![0-9A-Z])/g;
    var candidates = [], match;
    while ((match = shape.exec(upper))) {
      candidates.push(fold(match[2]));
      // The character before a candidate may be the one after the last.
      shape.lastIndex = match.index + match[0].length;
    }
    // The whole text, for a token typed with no dashes or with dots.
    var whole = fold(upper);
    if (whole.length === TOKEN_CHARS && candidates.indexOf(whole) < 0) {
      candidates.push(whole);
    }
    var shaped = candidates.length > 0;
    function next() {
      if (!candidates.length) return shaped ? { bad: true } : {};
      return parse(candidates.shift()).then(function (bytes) {
        return bytes ? { bytes: bytes } : next();
      });
    }
    return Promise.resolve(next());
  }

  // -------------------------------------------------------------------------
  // What is derived from it
  // -------------------------------------------------------------------------

  function derive(bytes) {
    var salt = utf8(SALT);
    return crypto.subtle.importKey(
      "raw", bytes, "HKDF", false, ["deriveBits", "deriveKey"]
    ).then(function (base) {
      return Promise.all([
        crypto.subtle.deriveBits(
          { name: "HKDF", hash: "SHA-256", salt: salt, info: utf8(INFO_SIGNIN) },
          base, 256),
        // Not extractable: a script on this origin can use the key and
        // cannot read it, so it cannot be posted anywhere either.
        crypto.subtle.deriveKey(
          { name: "HKDF", hash: "SHA-256", salt: salt, info: utf8(INFO_ENCRYPTION) },
          base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]),
      ]);
    }).then(function (parts) {
      return { signin: hex(new Uint8Array(parts[0])), key: parts[1] };
    });
  }

  // One transaction on the key store. Resolves to whether it went through and
  // never rejects: a browser with no IndexedDB (some private windows) still
  // signs in, it only has no key waiting.
  function keyStore(work) {
    return new Promise(function (resolve) {
      var open;
      try { open = indexedDB.open(DB_NAME, 1); } catch (e) { resolve(false); return; }
      open.onupgradeneeded = function () { open.result.createObjectStore(DB_STORE); };
      open.onerror = function () { resolve(false); };
      open.onsuccess = function () {
        var db = open.result, tx;
        try {
          tx = db.transaction(DB_STORE, "readwrite");
          work(tx.objectStore(DB_STORE));
        } catch (e) { db.close(); resolve(false); return; }
        tx.oncomplete = function () { db.close(); resolve(true); };
        tx.onerror = tx.onabort = function () { db.close(); resolve(false); };
      };
    });
  }

  // An anonymous visitor at the door means nobody is signed in on this
  // browser, so a key left by whoever was has no owner any more.
  var cleared = root.hasAttribute("data-authed")
    ? Promise.resolve(true)
    : keyStore(function (store) { store.delete(DB_KEY); });

  function keepKey(key) {
    return cleared.then(function () {
      return keyStore(function (store) { store.put(key, DB_KEY); });
    });
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

  function go(answer, key) {
    return keepKey(key).then(function () {
      window.location.assign(answer.data.next);
      return new Promise(function () {});
    });
  }

  function signIn(bytes) {
    return derive(bytes).then(function (made) {
      return post("/api/v1/workshop/door/signin", { signin: made.signin })
        .then(function (answer) {
          if (!answer.success) { say(entryError, answer.message || text.invalid); return null; }
          return go(answer, made.key);
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
      box.value = "";
      root.querySelector("[data-ws-door-download]").focus();
    });
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
          return go(answer, made.key);
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

  // CRLF: the room's computer is a Windows one as often as not, and its
  // Notepad used to draw a file with bare LF as a single line.
  function fileText(token) {
    return [
      text.fileWorkshop,
      text.fileAddress + " " + address(),
      "",
      text.fileCode,
      "",
      token,
      "",
      text.fileBack,
      text.fileKeep,
      text.fileLost,
      "",
    ].join("\r\n");
  }

  function download() {
    if (!pending) return;
    var blob = new Blob([fileText(pending.token)], { type: "text/plain;charset=utf-8" });
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = text.fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
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
  // the token still never crosses the network. Drawn as SVG through the DOM,
  // dark on white whatever the theme (see the template).
  function drawQr(token) {
    var figure = root.querySelector("[data-ws-door-qr]");
    var holder = root.querySelector("[data-ws-door-qr-code]");
    holder.textContent = "";
    if (typeof window.qrcode !== "function") { figure.hidden = true; return; }
    var qr;
    try {
      qr = window.qrcode(0, "M");
      qr.addData(address() + "#" + token);
      qr.make();
    } catch (e) { figure.hidden = true; return; }
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
  box.addEventListener("input", function () { say(entryError, ""); });

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
