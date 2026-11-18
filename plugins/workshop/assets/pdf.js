/* A one-page PDF, written by hand (PLAN.md §61.4). Two callers: door.js for
 * the personal code, certificate.js for the certificate (§63).
 *
 * The token door hands the personal code out as a file, and the file has to
 * be made in the browser, because the code exists nowhere else. A PDF and not
 * a text file: a text file opens in an editor, where one stray key changes a
 * character of the code and saves it.
 *
 * What a library would bring is not needed for one page, so this writes the
 * few objects itself:
 *
 *   - A4, in the platform's own typefaces and with its wordmark, which come
 *     ready for a PDF in assets/pdf-brand.json (scripts/build_pdf_brand.py
 *     makes it from the fonts and the logo the plugin vendors). Without that
 *     file the page is set in the standard fonts every reader has
 *     (Helvetica, its bold, Courier bold) and carries no wordmark: plainer,
 *     and still handed out;
 *   - WinAnsi encoding, which holds French;
 *   - **plain ASCII from the first byte to the last, and nothing compressed.**
 *     An accent is written as an octal escape, a font's bytes in hexadecimal.
 *     So the text put on the page is in the file as it is, and door.js finds
 *     the code again by reading the file as text, with no PDF parser. It also
 *     makes the byte offsets of the cross-reference table plain string
 *     lengths.
 *
 * Nothing else in the file may have the shape of a code (seven groups of
 * four letters or digits). So every coordinate is written with two decimals,
 * a QR code is drawn in its own small units, a font's widths are one a line
 * and its bytes one unbroken run.
 *
 *     var pdf = window.wsPdf(brand);    // or wsPdf() for the standard fonts
 *     var wide = window.wsPdf(brand, { width: 842, height: 595 });   // A4 on its side
 *     pdf.box(56, 700, 200, 40, { fill: "#013afb", radius: 8 });
 *     pdf.text("Bonjour", 64, 715, "bold", 18, "#ffffff");
 *     var bytes = pdf.build();          // a string, one character per byte
 *
 * Coordinates are PDF's: points, from the bottom left corner. A colour is
 * "#rrggbb"; text is black when none is given.
 */
(function () {
  // A4 upright, unless the caller asks for another page.
  var WIDTH = 595, HEIGHT = 842;
  // A font by its role: the name in the page's resources, then what stands in
  // for it when no brand is given, as a PostScript name and a row of WIDTHS.
  var FONTS = {
    regular: ["F1", "Helvetica", "regular"],
    bold: ["F2", "Helvetica-Bold", "bold"],
    mono: ["F3", "Courier-Bold", null],
    display: ["F4", "Helvetica-Bold", "bold"],
    // Only the certificate writes in it, so it is in the file only when used.
    light: ["F5", "Helvetica", "regular"],
  };
  var OPTIONAL = { light: true };
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

  function n(value) {
    return Number(value).toFixed(2);
  }

  // "#013afb" as the three numbers PDF takes, and the operator after them.
  function paint(color, operator) {
    var value = parseInt(String(color || "#000000").slice(1), 16);
    return [value >> 16, (value >> 8) & 255, value & 255].map(function (part) {
      return (part / 255).toFixed(3);
    }).join(" ") + " " + operator;
  }

  // Base64 to the hexadecimal a PDF stream can hold as text.
  function hexOf(base64) {
    var bytes = window.atob(base64), out = "";
    for (var i = 0; i < bytes.length; i++) {
      var b = bytes.charCodeAt(i);
      out += (b < 16 ? "0" : "") + b.toString(16).toUpperCase();
    }
    return out;
  }

  // `brand` is assets/pdf-brand.json once parsed, or nothing: the page is
  // then set in the standard fonts and carries no wordmark.
  // `page` is `{ width, height }` in points, for a page that is not A4 upright.
  window.wsPdf = function (brand, page) {
    var ops = [], links = [], used = {};
    var wide = (page && page.width) || WIDTH, high = (page && page.height) || HEIGHT;
    var embedded = (brand && brand.fonts) || {};
    var mark = brand && brand.logo;

    // An accented letter is as wide as its letter, near enough to wrap a
    // line. An embedded font knows the width of each of its characters.
    function advance(ch, font) {
      if (embedded[font]) return embedded[font].widths[code(ch) - 32] || 0;
      if (font === "mono") return 600;
      if (EXTRA[ch]) return EXTRA[ch][1];
      var c = ch.charCodeAt(0);
      if (c > 126) c = ch.normalize("NFD").charCodeAt(0);
      return c >= 32 && c <= 126 ? WIDTHS[FONTS[font][2]][c - 32] : 556;
    }

    // `spacing` is what is added after every character, in points.
    function measure(string, font, size, spacing) {
      var total = 0, text = clean(string);
      text.split("").forEach(function (ch) { total += advance(ch, font); });
      return total * size / 1000 + text.length * (spacing || 0);
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

    // The closed path of a rectangle, its corners rounded or not.
    function outline(x, y, w, h, radius) {
      var r = radius || 0;
      r = typeof r === "number" ? [r, r, r, r] : r;
      var k = 0.4477;   // 1 - 0.5523: where a quarter circle's handles sit
      var path = [n(x + r[3]) + " " + n(y) + " m"];
      function line(px, py) { path.push(n(px) + " " + n(py) + " l"); }
      function turn(ax, ay, bx, by, px, py) {
        path.push([ax, ay, bx, by, px, py].map(n).join(" ") + " c");
      }
      line(x + w - r[2], y);
      if (r[2]) turn(x + w - r[2] * k, y, x + w, y + r[2] * k, x + w, y + r[2]);
      line(x + w, y + h - r[1]);
      if (r[1]) turn(x + w, y + h - r[1] * k, x + w - r[1] * k, y + h, x + w - r[1], y + h);
      line(x + r[0], y + h);
      if (r[0]) turn(x + r[0] * k, y + h, x, y + h - r[0] * k, x, y + h - r[0]);
      line(x, y + r[3]);
      if (r[3]) turn(x, y + r[3] * k, x + r[3] * k, y, x + r[3], y);
      path.push("h");
      return path.join("\n");
    }

    return {
      width: wide,
      height: high,
      measure: measure,
      wrap: wrap,

      // One line of text, its baseline starting at (x, y). `look` is for the
      // two things a typeface file does not give: `slant`, which leans an
      // upright font (0.2 is an italic's angle), and `hidden`, which puts the
      // text in the file without drawing it. A program reading the file finds
      // it, and so does copy and paste.
      text: function (string, x, y, font, size, color, spacing, look) {
        used[font] = true;
        var place = look && look.slant
          ? "1 0 " + n(look.slant) + " 1 " + n(x) + " " + n(y) + " Tm "
          : n(x) + " " + n(y) + " Td ";
        ops.push((look && look.hidden ? "q 3 Tr " : "")
                 + "BT " + paint(color, "rg") + " /" + FONTS[font][0] + " " + n(size) + " Tf "
                 + n(spacing || 0) + " Tc " + place + literal(string) + " Tj ET"
                 + (look && look.hidden ? " Q" : ""));
      },

      // A rectangle, (x, y) its bottom left corner. `look` is its fill, its
      // stroke and the stroke's width, and `radius`, which rounds the
      // corners: one number, or four from the top left corner clockwise.
      box: function (x, y, w, h, look) {
        ops.push("q " + (look.fill ? paint(look.fill, "rg") + " " : "")
                 + (look.stroke ? paint(look.stroke, "RG") + " " + n(look.line || 0.5) + " w" : ""));
        ops.push(outline(x, y, w, h, look.radius));
        ops.push((look.fill && look.stroke ? "B" : look.fill ? "f" : "S") + " Q");
      },

      // From here to `unclip`, nothing is drawn outside this rectangle.
      clip: function (x, y, w, h, radius) {
        ops.push("q");
        ops.push(outline(x, y, w, h, radius));
        ops.push("W n");
      },
      unclip: function () { ops.push("Q"); },

      // The wordmark, `high` points tall, (x, y) its bottom left corner.
      // Answers its width: 0 when there is no brand to draw it from.
      logo: function (x, y, high, color) {
        if (!mark) return 0;
        var scale = high / mark.height;
        ops.push("q " + scale.toFixed(5) + " 0 0 " + scale.toFixed(5) + " " + n(x) + " " + n(y)
                 + " cm " + paint(color, "rg"));
        ops.push(mark.ops.map(function (op) {
          return op.slice(1).map(n).concat(op[0]).join(" ");
        }).join("\n"));
        ops.push("f Q");
        return mark.width * scale;
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
        var roles = Object.keys(FONTS).filter(function (role) {
          return !OPTIONAL[role] || used[role];
        });
        // Objects 1 to 4 are the catalogue, the page tree, the page and its
        // content. Then the fonts, an embedded one with the two objects it
        // needs right after it, then the links.
        var objects = [null, null, null,
          "<< /Length " + content.length + " >>\nstream\n" + content + "\nendstream"];
        var fonts = roles.map(function (role) {
          var face = embedded[role], at = objects.length + 1;
          if (!face) {
            objects.push("<< /Type /Font /Subtype /Type1 /BaseFont /" + FONTS[role][1]
              + " /Encoding /WinAnsiEncoding >>");
          } else {
            var data = hexOf(face.data) + ">";
            // One width a line: a row of four-digit widths with one space
            // between them is the shape of a code.
            objects.push("<< /Type /Font /Subtype /TrueType /BaseFont /" + face.name
              + " /Encoding /WinAnsiEncoding /FirstChar 32 /LastChar 255 /FontDescriptor "
              + (at + 1) + " 0 R /Widths [\n" + face.widths.join("\n") + "\n] >>");
            objects.push("<< /Type /FontDescriptor /FontName /" + face.name
              + " /Flags " + face.flags + " /FontBBox [" + face.box.map(n).join(" ") + "]"
              + " /ItalicAngle 0 /Ascent " + n(face.ascent) + " /Descent " + n(face.descent)
              + " /CapHeight " + n(face.cap) + " /StemV " + n(face.stem)
              + " /FontFile2 " + (at + 2) + " 0 R >>");
            // The font's bytes in hexadecimal, on one line: the file stays
            // text, and one unbroken run cannot be read as a code.
            objects.push("<< /Length " + data.length + " /Length1 " + face.bytes
              + " /Filter /ASCIIHexDecode >>\nstream\n" + data + "\nendstream");
          }
          return "/" + FONTS[role][0] + " " + at + " 0 R";
        });
        var annots = links.map(function (link) {
          objects.push(link);
          return objects.length + " 0 R";
        });
        objects[0] = "<< /Type /Catalog /Pages 2 0 R >>";
        objects[1] = "<< /Type /Pages /Kids [3 0 R] /Count 1 >>";
        objects[2] = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 " + wide + " " + high + "]"
          + " /Resources << /Font << " + fonts.join(" ") + " >> >>"
          + " /Contents 4 0 R /Annots [" + annots.join(" ") + "] >>";
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
