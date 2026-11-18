/* Show a PPM image, which no browser can.
 *
 * A subject may ship an image as a text PPM (P3) on purpose: every pixel is
 * three decimal numbers a participant can read in a text editor, and the CTF
 * subject hides a flag that way. An `<img>` pointing at one is a broken image,
 * so this fetches the file, paints its pixels and hands the `<img>` the result.
 *
 * The file itself is never converted: what the instance stores and serves is
 * the subject's PPM, byte for byte. Only the picture on screen is a copy.
 * workshop.css keeps an `<img>` whose src still ends in `.ppm` invisible, so
 * the broken-image icon never shows while this runs.
 */
(function () {
  "use strict";

  // P3 (text) and P6 (binary), maxval up to 255. Returns null on anything else.
  function decode(buf) {
    var bytes = new Uint8Array(buf), pos = 0;
    function token() {
      for (;;) {
        while (pos < bytes.length && bytes[pos] <= 32) pos++;
        if (bytes[pos] !== 35) break;                    // '#': a comment line
        while (pos < bytes.length && bytes[pos] !== 10) pos++;
      }
      var start = pos;
      while (pos < bytes.length && bytes[pos] > 32) pos++;
      return String.fromCharCode.apply(null, bytes.subarray(start, pos));
    }
    var magic = token(), w = +token(), h = +token(), max = +token();
    if ((magic !== "P3" && magic !== "P6") || !(w > 0) || !(h > 0) ||
        !(max > 0) || max > 255 || w * h > 4e6) return null;
    var out = new Uint8ClampedArray(w * h * 4), n = w * h * 3, i, v;
    if (magic === "P6") pos++;                           // one whitespace byte
    for (i = 0; i < n; i++) {
      if (magic === "P6") {
        v = bytes[pos++];
      } else {
        while (pos < bytes.length && bytes[pos] <= 32) pos++;
        for (v = 0; pos < bytes.length && bytes[pos] > 32; pos++) v = v * 10 + bytes[pos] - 48;
      }
      out[i + (i / 3 | 0)] = max === 255 ? v : Math.round(v * 255 / max);
    }
    for (i = 3; i < out.length; i += 4) out[i] = 255;
    return new ImageData(out, w, h);
  }

  function show(img) {
    var src = img.getAttribute("src");
    fetch(src, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.arrayBuffer() : Promise.reject(r.status); })
      .then(function (buf) {
        var data = decode(buf);
        if (!data) return;
        var canvas = document.createElement("canvas");
        canvas.width = data.width;
        canvas.height = data.height;
        canvas.getContext("2d").putImageData(data, 0, 0);
        canvas.toBlob(function (blob) {
          img.dataset.ppmSrc = src;                      // where the real file is
          img.removeAttribute("loading");
          img.src = URL.createObjectURL(blob);
        });
      })
      .catch(function () {});
  }

  document.querySelectorAll('img[src$=".ppm"]').forEach(show);
})();
