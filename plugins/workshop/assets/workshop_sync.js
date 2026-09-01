/* The admin sync page (PLAN.md §26).
 *
 * Press Sync, watch the log. If the fetched content carries an encrypted
 * answers file, the job parks and hands the blob back here: this page asks for
 * the passphrase, decrypts with openpgp.js **in this browser**, and posts the
 * plaintext the importer needs. The passphrase itself never reaches the server.
 *
 * Polled rather than streamed: nginx buffers server-sent events unless it is
 * told not to, and a one-second poll has nothing to configure.
 */
const ROOT = document.getElementById("ws-sync");
if (ROOT) {
  const nonce = ROOT.dataset.nonce;
  const button = document.getElementById("ws-sync-go");
  const statusEl = ROOT.querySelector(".ws-sync-status");
  const logEl = ROOT.querySelector(".ws-sync-log");
  const summaryEl = ROOT.querySelector(".ws-sync-summary");
  const unlockBox = ROOT.querySelector(".ws-sync-unlock");
  const passInput = document.getElementById("ws-sync-pass");
  const unlockBtn = document.getElementById("ws-sync-unlock");
  const unlockError = ROOT.querySelector(".ws-sync-unlock-error");

  let pending = null;      // the files the job is waiting on
  let polling = null;

  const api = (method, path, body) =>
    fetch(path, {
      method,
      headers: { "Content-Type": "application/json", "CSRF-Token": nonce },
      body: body ? JSON.stringify(body) : undefined,
    }).then((r) => r.json().catch(() => ({})));

  function say(text, kind) {
    statusEl.textContent = text;
    statusEl.className = "ws-sync-status ml-2 " + (kind || "text-muted");
  }

  function render(job) {
    if (job.log && job.log.length) {
      logEl.hidden = false;
      logEl.textContent = job.log.join("\n");
      logEl.scrollTop = logEl.scrollHeight;
    }
    if (job.state === "needs_answers" && !pending) {
      pending = job.needs || [];
      unlockBox.hidden = false;
      passInput.focus();
      say("waiting for the answers to be unlocked", "text-warning");
    }
    if (job.state === "running") say("working…");
    if (job.state === "done") {
      const s = job.summary || {};
      summaryEl.hidden = false;
      summaryEl.className = "ws-sync-summary mt-3 alert " +
        (s.created ? "alert-warning" : "alert-success");
      summaryEl.innerHTML =
        "<strong>Imported " + (job.commit || "").slice(0, 7) + "</strong> — " +
        (s.created || 0) + " created, " + (s.updated || 0) + " updated." +
        (s.created
          ? " Steps were <em>created</em> on an instance that already had this content:" +
            " an id changed, and the old challenges are still there."
          : "");
      say("done", "text-success");
    }
    if (job.state === "failed") {
      summaryEl.hidden = false;
      summaryEl.className = "ws-sync-summary mt-3 alert alert-danger";
      summaryEl.textContent = job.error || "the sync failed";
      say("failed", "text-danger");
    }
    if (job.state === "done" || job.state === "failed") {
      clearInterval(polling);
      polling = null;
      button.disabled = false;
      button.textContent = "Sync again";
    }
  }

  function poll() {
    polling = setInterval(async () => {
      const r = await api("GET", "/api/v1/workshop/sync");
      if (r && r.data) render(r.data);
    }, 1000);
  }

  // A job started before this page was opened (another tab, a reload while it
  // was working): show it, and keep polling until it lands.
  try {
    const running = ROOT.dataset.job ? JSON.parse(ROOT.dataset.job) : null;
    if (running && (running.state === "running" || running.state === "needs_answers")) {
      button.disabled = true;
      render(running);
      poll();
    }
  } catch (e) { /* no job to resume */ }

  button.addEventListener("click", async () => {
    button.disabled = true;
    pending = null;
    unlockBox.hidden = true;
    summaryEl.hidden = true;
    logEl.hidden = true;
    logEl.textContent = "";
    say("fetching…");
    const r = await api("POST", "/api/v1/workshop/sync", {});
    if (!r.success) {
      say((r.errors || ["could not start"])[0], "text-danger");
      button.disabled = false;
      return;
    }
    render(r.data);
    poll();
  });

  unlockBtn.addEventListener("click", async () => {
    const passphrase = passInput.value;
    if (!passphrase) return;
    unlockBtn.disabled = true;
    unlockError.textContent = "";
    try {
      // Loaded only here: 400 KB that a page which never meets an encrypted
      // subject has no reason to download.
      const openpgp = await import(ROOT.dataset.openpgp);
      const answers = {};
      for (const file of pending) {
        const bytes = Uint8Array.from(atob(file.blob), (c) => c.charCodeAt(0));
        const message = await openpgp.readMessage({ binaryMessage: bytes });
        const { data } = await openpgp.decrypt({
          message, passwords: [passphrase], format: "utf8",
        });
        answers[file.id] = data;
      }
      passInput.value = "";
      unlockBox.hidden = true;
      say("answers unlocked, importing…");
      await api("POST", "/api/v1/workshop/sync/answers", { answers });
    } catch (err) {
      // openpgp.js reports a wrong passphrase as "Modification detected".
      unlockError.textContent = /modification|session key|password/i.test(String(err))
        ? "That passphrase does not open this file."
        : String(err.message || err);
    } finally {
      unlockBtn.disabled = false;
    }
  });
}
