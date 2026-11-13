/* The certificate, drawn here (certificate.py decides what it says; PLAN.md §63).
 *
 * The button asks the server for the facts, the sentences and the signed
 * text, then this draws one page with assets/pdf.js and hands it over as a
 * file. Nothing is sent anywhere else.
 *
 * The page is Epitech's "Certificat de participation", redrawn: A4 on its side,
 * the thin blue frame, the wordmark, the title in Anton over a green line,
 * the name in blue capitals, the sentence in italics, the two lines of the
 * footer. The original is 1123 by 794 CSS pixels and so are the numbers below:
 * `PX` turns them into points, and `down` counts from the top as CSS does.
 * Where the original has the signatures of the campus, this one has what stands
 * for a signature here: the QR code of the signed text, and who signed.
 *
 * The signed text is in the file twice. In the QR code, for a printed
 * certificate. And as one line of text that is not drawn, which a program
 * finds by reading the file as text: three runs of letters, digits, `-` and
 * `_` with a dot between them, starting with `eyJ`.
 *
 * This file holds no sentence: they come from the server, translated.
 */
(function () {
  var root = document.querySelector("[data-ws-cert]");
  if (!root || !window.wsPdf) return;
  var button = root.querySelector("button");
  var error = root.querySelector("[data-ws-cert-error]");

  // Not offered before a first step is done. A step solved on this page
  // moves the counter without reloading it, so that is what is watched.
  if (root.hidden) {
    var counter = document.querySelector(".ws-overall-num");
    if (counter && window.MutationObserver) {
      new MutationObserver(function () { root.hidden = false; })
        .observe(counter, { childList: true, characterData: true, subtree: true });
    }
  }

  function json(url, credentials) {
    return fetch(url, { credentials: credentials, headers: { Accept: "application/json" } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .catch(function () { return null; });
  }

  // The typefaces and the wordmark (assets/pdf-brand.json), and the light
  // weight only this page uses. Without them the page is set in the standard
  // fonts: plainer, and still a certificate.
  var brand = null;
  function loadBrand() {
    if (!brand) {
      brand = Promise.all([
        json(root.dataset.brand, "omit"), json(root.dataset.brandLight, "omit"),
      ]).then(function (found) {
        if (!found[0]) { brand = null; return null; }
        if (found[1] && found[1].fonts) {
          Object.keys(found[1].fonts).forEach(function (role) {
            found[0].fonts[role] = found[1].fonts[role];
          });
        }
        return found[0];
      });
    }
    return brand;
  }

  // The original's colours (its Coding Club template).
  var INK = {
    blue: "#013afb", green: "#00ff97", title: "#0f172a", name: "#1e293b",
    text: "#475569", muted: "#64748b", faint: "#94a3b8",
    rule: "#e2e8f0", line: "#cbd5e1", white: "#ffffff",
  };
  var PAGE = { wide: 1123, high: 794 };
  var PX = 0.75;
  // How far under the top of its line a face's baseline sits, in font sizes.
  var RISE = { display: 1.18, text: 1.03 };

  function qrOf(signed) {
    if (typeof window.qrcode !== "function") return null;
    try {
      var qr = window.qrcode(0, "L");
      qr.addData(signed);
      qr.make();
      return qr;
    } catch (e) { return null; }
  }

  function draw(found, data) {
    var pdf = window.wsPdf(found, { width: PAGE.wide * PX, height: PAGE.high * PX });
    var locale = document.documentElement.lang || undefined;
    function upper(sentence) { return String(sentence || "").toLocaleUpperCase(locale); }
    function down(px) { return (PAGE.high - px) * PX; }
    // One line, centred on the page, its line box starting `top` pixels down.
    function centred(string, top, font, size, color, tracking, look) {
      var points = size * PX, spacing = (tracking || 0) * points;
      var width = pdf.measure(string, font, points, spacing);
      var rise = font === "display" ? RISE.display : RISE.text;
      pdf.text(string, (pdf.width - width) / 2, down(top + rise * size), font, points, color,
               spacing, look);
    }
    // The largest size, from `size` down, at which the line fits in `max` pixels.
    function fitted(string, font, size, max, tracking) {
      while (size > 18 && pdf.measure(string, font, size, (tracking || 0) * size) > max) size -= 1;
      return size;
    }

    // The frame, 24 pixels in, 2 thick.
    pdf.box(25 * PX, 25 * PX, (PAGE.wide - 50) * PX, (PAGE.high - 50) * PX,
            { stroke: INK.blue, line: 2 * PX });

    // The header: the wordmark, then the institute.
    var mark = 42 * PX;
    var markWide = pdf.logo(0, -1000, mark, INK.white);   // drawn off the page: its width
    if (markWide) pdf.logo((pdf.width - markWide) / 2, down(48 + 42), mark, INK.blue);
    centred(upper(data.institute), 100, "bold", 12, INK.muted, 0.18);

    // The title and its line.
    var heading = upper(data.heading);
    centred(heading, 134, "display", fitted(heading, "display", 46, 900, 0.04), INK.title, 0.04);
    pdf.box((pdf.width - 110 * PX) / 2, down(212), 110 * PX, 5 * PX,
            { fill: INK.green, radius: 2.5 * PX });

    // The footer, from the bottom: a rule, and the day on the right.
    var left = 96, right = PAGE.wide - 96;
    pdf.box(left * PX, down(724), (right - left) * PX, 0.01, { stroke: INK.rule, line: PX });
    var place = pdf.measure(data.place, "regular", 11 * PX);
    pdf.text(data.place, right * PX - place - 2, down(751), "regular", 11 * PX, INK.faint, 0,
             { slant: 0.2 });

    // The body, centred between the title's line and the footer: the name,
    // the line under it, the sentence, the proof. 22 pixels between them.
    var name = upper(data.name);
    var nameSize = fitted(name, "display", 44, 900);
    // The sentence, on as few lines as 880 pixels allow, and those lines as
    // even as they get: never one word alone under a full line.
    var lines = pdf.wrap(data.sentence, "regular", 16 * PX, 880 * PX);
    for (var wide = 860; wide > 300; wide -= 20) {
      var tighter = pdf.wrap(data.sentence, "regular", 16 * PX, wide * PX);
      if (tighter.length > lines.length) break;
      lines = tighter;
    }
    var qr = qrOf(data.signed), side = 150;
    var gap = 22, lead = 16 * 1.6;
    var blocks = [nameSize * 1.05, 18 * 1.3, lines.length * lead, qr ? side : 40];
    var total = blocks.reduce(function (sum, high) { return sum + high; }, 0)
      + gap * (blocks.length - 1);
    var top = 212 + (696 - 212 - total) / 2;

    centred(name, top - 0.07 * nameSize, "display", nameSize, INK.blue);
    top += blocks[0] + gap;
    centred(data.attribution, top, "light", 18, INK.text);
    top += blocks[1] + gap;
    lines.forEach(function (line, i) {
      centred(line, top + i * lead + (lead - 16 * 1.3) / 2, "regular", 16, INK.text, 0,
              { slant: 0.2 });
    });
    top += blocks[2] + gap;

    // The proof: the QR code, and beside it a signature block as the original draws
    // one, a line with a name over a role. Centred together.
    var proofWide = Math.max(pdf.measure(data.proof, "bold", 14 * PX),
                             pdf.measure(data.serial, "regular", 12 * PX), 180 * PX);
    var between = qr ? 24 * PX : 0, qrWide = qr ? side * PX : 0;
    var x = (pdf.width - qrWide - between - proofWide) / 2;
    if (qr) {
      pdf.cells(x, down(top + side), qrWide, qr.getModuleCount(),
                function (r, c) { return qr.isDark(r, c); });
    }
    var tx = x + qrWide + between, middle = top + blocks[3] / 2;
    pdf.box(tx, down(middle - 18), proofWide, 0.01, { stroke: INK.line, line: PX });
    pdf.text(data.proof, tx, down(middle + 2), "bold", 14 * PX, INK.name);
    pdf.text(data.serial, tx, down(middle + 20), "regular", 12 * PX, INK.muted);

    // The signed text, as text: in the file, not on the page.
    pdf.text(data.signed, left * PX, down(770), "regular", 1, INK.white, 0, { hidden: true });
    return pdf.build();
  }

  function fail() {
    if (error) { error.textContent = root.dataset.failed; error.hidden = false; }
  }

  button.addEventListener("click", function () {
    if (button.disabled) return;
    button.disabled = true;
    if (error) error.hidden = true;
    Promise.all([json(root.dataset.url, "same-origin"), loadBrand()]).then(function (got) {
      var data = got[0] && got[0].success && got[0].data;
      if (!data) { fail(); return; }
      var blob = new Blob([draw(got[1], data)], { type: "application/pdf" });
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = data.file;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
    }).catch(fail).then(function () { button.disabled = false; });
  });
})();
