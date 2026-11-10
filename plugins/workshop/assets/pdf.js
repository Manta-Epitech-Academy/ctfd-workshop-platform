/* A one-page PDF, written by hand (door.js is its one caller; PLAN.md §60.4).
 *
 * The token door hands the personal code out as a file, and the file has to
 * be made in the browser, because the code exists nowhere else. A PDF and not
 * a text file: a text file opens in an editor, where one stray key changes a
 * character of the code and saves it.
 *
 * What a library would bring is not needed for one page of text, so this
 * writes the few objects itself:
 *
 *   - A4, the three standard fonts every reader has (Helvetica, its bold,
 *     Courier bold), so nothing is embedded;
 *   - WinAnsi encoding, which holds French;
 *   - **plain ASCII from the first byte to the last, and nothing compressed.**
 *     An accent is written as an octal escape. So the text put on the page is
 *     in the file as it is, and door.js finds the code again by reading the
 *     file as text, with no PDF parser. It also makes the byte offsets of the
 *     cross-reference table plain string lengths.
 *
 * Every coordinate is written with two decimals, and a QR code is drawn in
 * its own small units: no run of digits in the file can be taken for a code.
 *
 *     var pdf = window.wsPdf();
 *     pdf.text("Bonjour", 56, 770, "bold", 18);
 *     var bytes = pdf.build();          // a string, one character per byte
 *
 * Coordinates are PDF's: points, from the bottom left corner.
 */
(function () {
  var WIDTH = 595, HEIGHT = 842;
  // Name in the page's resources, and the PostScript name.
  var FONTS = {
    regular: ["F1", "Helvetica"],
    bold: ["F2", "Helvetica-Bold"],
    mono: ["F3", "Courier-Bold"],
  };
  // Advance widths of characters 32 to 126, in thousandths of the font size
  // (the Adobe core font metrics). Courier is 600 throughout.
  var WIDTHS = {
    regular: [278, 278, 355, 556, 556, 889, 667, 222, 333, 333, 389, 584, 278, 333, 278, 278, 556,
      556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667,
      722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611,
      722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 222, 556, 556, 500, 556, 556, 278,
      556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500,
      500, 500, 334, 260, 334, 584],
    bold: [278, 333, 474, 556, 556, 889, 722, 278, 333, 333, 389, 584, 278, 333, 278, 278, 556,
      556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722,
      722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611,
      722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 278, 556, 611, 556, 611, 556, 333,
      611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556,
      556, 500, 389, 280, 389, 584],
  };
  // The characters WinAnsi keeps outside Latin-1 that French text uses: the
  // code, and the width (the regular font's, or the bold's where it is wider).
  var EXTRA = {
    "\u20ac": [128, 556], "\u2026": [133, 1000], "\u0152": [140, 1000],
    "\u2018": [145, 278], "\u2019": [146, 278], "\u201c": [147, 500],
    "\u201d": [148, 500], "\u2013": [150, 556], "\u2014": [151, 1000],
    "\u0153": [156, 944],
  };

  // The WinAnsi code of a character, or 0 when it has none (an emoji).
  function code(ch) {
    var c = ch.charCodeAt(0);
    if (c >= 32 && c <= 126) return c;
    if (c >= 161 && c <= 255) return c;
    return EXTRA[ch] ? EXTRA[ch][0] : 0;
  }

  // A text as this file can write it: every kind of space is a space, and a
  // character with no code is left out rather than shown as a question mark.
  function clean(string) {
    var spaced = String(string || "").normalize("NFC").replace(/[\s\u00a0\u202f]+/g, " ");
    return Array.from(spaced)
      .filter(function (ch) { return ch.length === 1 && code(ch); }).join("");
  }

  // A PDF string: ASCII as it is, the three characters that mean something
  // escaped, the rest in octal.
  function literal(string) {
    var out = "(";
    clean(string).split("").forEach(function (ch) {
      var c = code(ch);
      if (ch === "\\" || ch === "(" || ch === ")") out += "\\" + ch;
      else if (c <= 126) out += ch;
      else out += "\\" + c.toString(8);
    });
    return out + ")";
  }

  // An accented letter is as wide as its letter, near enough to wrap a line.
  function advance(ch, font) {
    if (font === "mono") return 600;
    if (EXTRA[ch]) return EXTRA[ch][1];
    var c = ch.charCodeAt(0);
    if (c > 126) c = ch.normalize("NFD").charCodeAt(0);
    return c >= 32 && c <= 126 ? WIDTHS[font][c - 32] : 556;
  }

  function measure(string, font, size) {
    var total = 0;
    clean(string).split("").forEach(function (ch) { total += advance(ch, font); });
    return total * size / 1000;
  }

  // The lines of a text no wider than `max`. A word wider than a whole line
  // (an address) is cut where it has to be.
  function wrap(string, font, size, max) {
    var lines = [], line = "";
    clean(string).split(" ").forEach(function (word) {
      if (!word) return;
      var longer = line ? line + " " + word : word;
      if (measure(longer, font, size) <= max) { line = longer; return; }
      if (line) lines.push(line);
      while (word.length > 1 && measure(word, font, size) > max) {
        var cut = word.length - 1;
        while (cut > 1 && measure(word.slice(0, cut), font, size) > max) cut--;
        lines.push(word.slice(0, cut));
        word = word.slice(cut);
      }
      line = word;
    });
    if (line) lines.push(line);
    return lines;
  }

  function n(value) {
    return Number(value).toFixed(2);
  }

  window.wsPdf = function () {
    var ops = [], links = [];
    return {
      width: WIDTH,
      height: HEIGHT,
      measure: measure,
      wrap: wrap,

      // One line of text, its baseline starting at (x, y).
      text: function (string, x, y, font, size) {
        ops.push("BT /" + FONTS[font][0] + " " + n(size) + " Tf " + n(x) + " " + n(y)
                 + " Td " + literal(string) + " Tj ET");
      },

      // A light panel with a thin border, for what the eye should find.
      panel: function (x, y, w, h) {
        var rect = n(x) + " " + n(y) + " " + n(w) + " " + n(h) + " re";
        ops.push("q 0.96 g 0.60 G 0.75 w " + rect + " B Q");
      },

      // A square of `count` by `count` cells, the dark ones black: a QR code.
      // (x, y) is its bottom left corner, `dark(row, column)` counts rows from
      // the top. One path filled once, so two cells side by side leave no
      // hairline between them, and a run of cells in a row is one rectangle.
      cells: function (x, y, side, count, dark) {
        var path = [];
        for (var r = 0; r < count; r++) {
          for (var c = 0; c < count; c++) {
            if (!dark(r, c)) continue;
            var from = c;
            while (c + 1 < count && dark(r, c + 1)) c++;
            path.push(from + " " + (count - 1 - r) + " " + (c - from + 1) + " 1 re");
          }
        }
        var scale = n(side / count);
        ops.push("q " + scale + " 0 0 " + scale + " " + n(x) + " " + n(y) + " cm 0 g");
        ops.push(path.join("\n"));
        ops.push("f Q");
      },

      // A rectangle that opens an address when it is clicked.
      link: function (x, y, w, h, url) {
        links.push("<< /Type /Annot /Subtype /Link /Border [0 0 0] /Rect ["
          + [x, y, x + w, y + h].map(n).join(" ") + "] /A << /S /URI /URI "
          + literal(String(url).replace(/[^\x20-\x7e]/g, "")) + " >> >>");
      },

      // The file. Every character is one byte.
      build: function () {
        var content = ops.join("\n");
        var first = 5 + Object.keys(FONTS).length;
        var fonts = Object.keys(FONTS).map(function (name, i) {
          return "/" + FONTS[name][0] + " " + (5 + i) + " 0 R";
        });
        var annots = links.map(function (unused, i) { return (first + i) + " 0 R"; });
        var objects = [
          "<< /Type /Catalog /Pages 2 0 R >>",
          "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
          "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 " + WIDTH + " " + HEIGHT + "]"
            + " /Resources << /Font << " + fonts.join(" ") + " >> >>"
            + " /Contents 4 0 R /Annots [" + annots.join(" ") + "] >>",
          "<< /Length " + content.length + " >>\nstream\n" + content + "\nendstream",
        ].concat(Object.keys(FONTS).map(function (name) {
          return "<< /Type /Font /Subtype /Type1 /BaseFont /" + FONTS[name][1]
            + " /Encoding /WinAnsiEncoding >>";
        }), links);
        var out = "%PDF-1.4\n", offsets = [];
        objects.forEach(function (body, i) {
          offsets.push(out.length);
          out += (i + 1) + " 0 obj\n" + body + "\nendobj\n";
        });
        var table = out.length;
        out += "xref\n0 " + (objects.length + 1) + "\n0000000000 65535 f \n";
        offsets.forEach(function (offset) {
          out += ("0000000000" + offset).slice(-10) + " 00000 n \n";
        });
        return out + "trailer\n<< /Size " + (objects.length + 1) + " /Root 1 0 R >>\n"
          + "startxref\n" + table + "\n%%EOF\n";
      },
    };
  };
})();
