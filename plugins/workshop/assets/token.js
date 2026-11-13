/* The token of the token door, and what is derived from it (PLAN.md §61, §68).
 *
 * Two pages need this and they must agree to the bit: the door
 * (assets/door.js), which makes a token and signs in with it, and the page
 * that holds a runtime (assets/runtime.js), which encrypts saved code with
 * the key the door left and asks for the token again when that key is gone.
 * So it is written once, here, as `window.wsToken`.
 *
 * A token is 128 random bits, never sent anywhere. Three values are derived
 * from it with HKDF-SHA-256:
 *
 *   signin   32 bytes, sent as hex. The server stores their SHA-256.
 *   key      an AES-GCM key that cannot be exported. It wraps the key saved
 *            code is encrypted with (runtime.js) and never leaves the browser.
 *   owner    the first 16 characters of that SHA-256, as hex. It says whose
 *            key this is: the server gives the same 16 characters to the
 *            account they belong to, so a key left by somebody else on this
 *            browser, or a token that is another account's, is told apart
 *            before anything is encrypted with it.
 *
 * The token as a person sees it is 28 characters in seven groups of four:
 * 26 of Crockford's base32 for the 128 bits (the last character carries two
 * padding bits, always zero) and 2 more that are a checksum, the first ten
 * bits of the SHA-256 of the 16 bytes. The checksum is not security. It is
 * what lets a page say "that is mistyped" without asking the server.
 *
 * The key and its owner wait in IndexedDB for as long as this browser is
 * signed in (`keep`, `kept`, `forget`).
 */
(function () {
  // Crockford's base32, the same string as codes.py ALPHABET.
  var ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  var TOKEN_BYTES = 16;
  var TOKEN_CHARS = 28;
  // Frozen: changing any of the three strings signs every account out for
  // good, because nobody holds the token to derive the new value from.
  var SALT = "ctfd-workshop-token-door-v1";
  var INFO_SIGNIN = "sign-in";
  var INFO_ENCRYPTION = "encryption";
  var OWNER_CHARS = 16;
  // Where the encryption key waits for the page that will use it.
  var DB_NAME = "ws-door", DB_STORE = "keys", DB_KEY = "encryption", DB_OWNER = "owner";

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
      // The server's own digest of the sign-in value (codes.py `signin_hash`).
      return crypto.subtle.digest("SHA-256", parts[0]).then(function (digest) {
        return {
          signin: hex(new Uint8Array(parts[0])),
          key: parts[1],
          owner: hex(new Uint8Array(digest)).slice(0, OWNER_CHARS),
        };
      });
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

  function keep(made) {
    return keyStore(function (store) {
      store.put(made.key, DB_KEY);
      store.put(made.owner, DB_OWNER);
    });
  }

  // The key waiting here and whose it is: `{key, owner}`, either one null
  // when it is not there.
  function kept() {
    var found = { key: null, owner: null };
    return keyStore(function (store) {
      store.get(DB_KEY).onsuccess = function (event) {
        found.key = event.target.result || null;
      };
      store.get(DB_OWNER).onsuccess = function (event) {
        found.owner = event.target.result || null;
      };
    }).then(function () { return found; });
  }

  function forget() {
    return keyStore(function (store) {
      store.delete(DB_KEY);
      store.delete(DB_OWNER);
    });
  }

  window.wsToken = {
    TOKEN_BYTES: TOKEN_BYTES, TOKEN_CHARS: TOKEN_CHARS,
    utf8: utf8, tokenText: tokenText, fold: fold, findToken: findToken,
    derive: derive, keep: keep, kept: kept, forget: forget,
  };
})();
