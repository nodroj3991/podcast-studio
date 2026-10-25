/* Podcast Studio renderer - plain JS, no build step. Talks to the local engine API. */
const API = "";                                   // same origin (served by the engine)
const S = window.studio || {                      // graceful fallback when opened in a browser
  isElectron: false, pickFiles: async () => [], pathFor: (f) => f.path || null, setBusy: async () => false,
  notify: async (t, b) => { if (Notification && Notification.permission === "granted") new Notification(t, { body: b }); },
  openExternal: (u) => window.open(u), showInFolder: () => {}, openPath: () => {}, appInfo: async () => ({ version: "dev", platform: "web" }),
  checkUpdate: async () => ({ state: "none" }), downloadUpdate: async () => {}, installUpdate: () => {}, onUpdateStatus: () => {}, onUpdateProgress: () => {},
};
const $ = (sel, el = document) => el.querySelector(sel);
const view = $("#view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
async function api(path, body, method) {
  const r = await fetch(API + "/api" + path, body !== undefined ? { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const d = await r.json();
  if (!r.ok || d.error) throw new Error(d.error || d.message || "Request failed");
  return d;
}
function toast(msg, bad) { const d = document.createElement("div"); d.textContent = msg; if (bad) d.className = "bad"; $("#toast").appendChild(d); setTimeout(() => d.remove(), bad ? 9000 : 4000); }
const fmtDur = (s) => { s = Math.round(s || 0); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `about ${h} h ${String(m).padStart(2, "0")} min` : m ? `about ${m} min` : "under a minute"; };
const tc = (t) => { t = Math.max(0, t || 0); const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = Math.floor(t % 60); return (h ? h + ":" : "") + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0"); };
let settings = {}, presets = {}, hw = null;

// ------------------------------------------------------------------ router
const routes = { "": home, new: newEpisode, episode: episode, settings: settingsPage, help: helpPage };
async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === (parts[0] || "home")));
  const fn = routes[parts[0]] || home;
  view.innerHTML = "<p class='muted'>Loading…</p>";
  try { await fn(...parts.slice(1)); } catch (e) { view.innerHTML = `<div class="err"><b>Something went wrong.</b><br>${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route);
async function boot() {
  try { [settings, presets, hw] = await Promise.all([api("/settings"), api("/presets"), api("/hw")]); } catch (e) { view.innerHTML = `<div class="err">The engine isn't responding. ${esc(e.message)}</div>`; return; }
  route(); pollQueue();
  S.onUpdateStatus((s) => showUpdate(s));
}
async function pollQueue() {
  try {
    const q = await api("/queue");
    const b = $("#queue-badge");
    const busy = !!q.current;
    b.innerHTML = busy ? `⏳ Working on <b>${esc(q.current.id)}</b>${q.queued.length ? `<br>+${q.queued.length} queued` : ""}` : q.queued.length ? `${q.queued.length} queued` : "";
    S.setBusy(busy || q.queued.length > 0);
  } catch (_) {}
  setTimeout(pollQueue, 3000);
}

// ------------------------------------------------------------------ home
async function home() {
  const eps = await api("/episodes");
  const est = hw.pretty || {};
  view.innerHTML = `
    <div class="row"><h1>Episodes</h1><button class="primary right" onclick="location.hash='#/new'">＋ New episode</button></div>
    <div class="card small muted">Your computer: ${esc(hw.hardware.os)} · ${hw.hardware.cpu_count} CPU cores · ${hw.hardware.ram_gb} GB RAM · ${hw.hardware.gpu === "none" ? "no GPU acceleration" : esc(hw.hardware.gpu_name)} ·
      transcription model <b>${esc(hw.model)}</b>. A 90-minute episode takes <b>${esc(est.total || "")}</b> on this machine (transcribing ${esc(est.transcribe)}, final render ${esc(est.render_final)}).</div>
    <div id="model-banner"></div>
    <div class="card">
      ${eps.length ? eps.map((e) => `
        <div class="episode" onclick="location.hash='#/episode/${encodeURIComponent(e.id)}'">
          <div><b>${esc(e.title)}</b><div class="muted small">${esc(e.guest || "")}${e.duration ? " · " + tc(e.duration) + " raw" : ""}</div></div>
          <span class="badge ${e.stages_done === e.stages_total ? "ok" : ""}">${e.stages_done === e.stages_total ? "Finished" : e.stages_done ? "In progress · " + e.stages_done + "/" + e.stages_total : "Not started"}</span>
          <span class="muted small">${e.qa_passed === true ? "QA ✓" : e.qa_passed === false ? "QA ✗" : ""}</span>
        </div>`).join("") : `<p class="muted">No episodes yet. Click <b>New episode</b>, drop in your recording files and press one button.</p>`}
    </div>`;
  modelBanner();
}
async function modelBanner() {
  const el = $("#model-banner"); if (!el) return;
  try {
    const m = await api("/models/status");
    if (m.complete) { el.innerHTML = ""; return; }
    const mb = (b) => (b / 1e6).toFixed(0) + " MB";
    el.innerHTML = `<div class="card"><div class="row"><b>One-time download: transcription model “${esc(m.model)}” (${mb(m.expected_bytes)})</b>
      <span class="right small muted">${m.running ? mb(m.bytes) + " so far" : "needed before the first episode"}</span>
      ${m.running ? "" : `<button class="primary" onclick="api('/models/download',{}).then(modelBanner)">Download now</button>`}</div>
      ${m.running ? `<div class="progress" style="margin-top:10px"><div style="width:${Math.round(m.fraction * 100)}%"></div></div>` : ""}
      ${m.error ? `<div class="err small">${esc(m.error)}</div>` : ""}</div>`;
    if (m.running) setTimeout(modelBanner, 1500);
  } catch (_) { el.innerHTML = ""; }
}

// ------------------------------------------------------------------ new episode
let newFiles = [];
async function newEpisode() {
  newFiles = [];
  view.innerHTML = `
    <h1>New episode</h1>
    <div class="card">
      <div id="drop" class="dropzone">Drop your recording files here (MP4, MOV, WAV …)<br><span class="small">one file per camera or microphone is fine - they will be synced automatically</span><br><br><button id="pick">Choose files…</button></div>
      <div id="files" class="filelist"></div>
    </div>
    <div class="card grid2">
      <div><label>Guest name</label><input id="g_name" type="text" placeholder="Dr Jane Smith"></div>
      <div><label>Guest title / role</label><input id="g_title" type="text" placeholder="Senior Lecturer in Sociology"></div>
      <div><label>Institution</label><input id="g_inst" type="text" placeholder="University of Edinburgh"></div>
      <div><label>Episode title (optional)</label><input id="e_title" type="text" placeholder="Leave blank to use the date and guest name"></div>
      <div><label>Preset</label><select id="preset">${Object.keys(presets).map((p) => `<option>${esc(p)}</option>`).join("")}</select></div>
      <div><label>Your name (host)</label><input id="host" type="text" value="${esc(settings.host_name || "")}"></div>
      <div><label>Custom vocabulary (names, terms - one per line, optional)</label><textarea id="vocab" placeholder="Nordic Seas\nCRISPR\nProf. Adebayo"></textarea></div>
      <div><label>Before the final render</label>
        <label class="row" style="color:var(--text)"><input type="checkbox" id="review" checked> Pause so I can review the transcript, cuts, music and charts</label>
        <label class="row" style="color:var(--text)"><input type="checkbox" id="burn"> Also make a version with burned-in captions</label>
      </div>
    </div>
    <div class="row"><button id="go" class="primary">Edit My Episode</button><span id="est" class="muted small"></span></div>`;
  const drop = $("#drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); for (const f of e.dataTransfer.files) addFile(S.pathFor(f) || f.name); });
  $("#pick").onclick = async () => { for (const p of await S.pickFiles()) addFile(p); };
  $("#go").onclick = startEpisode;
  function addFile(p) { if (p && !newFiles.find((f) => f.path === p)) newFiles.push({ path: p, role: "auto", speaker: "" }); renderFiles(); }
}
function renderFiles() {
  $("#files").innerHTML = newFiles.map((f, i) => `
    <div class="row"><span class="grow mono">${esc(f.path)}</span>
      <select onchange="newFiles[${i}].role=this.value"><option value="auto" ${f.role === "auto" ? "selected" : ""}>auto</option><option value="speaker" ${f.role === "speaker" ? "selected" : ""}>one speaker's camera/mic</option><option value="wide" ${f.role === "wide" ? "selected" : ""}>wide shot</option></select>
      <input type="text" style="width:160px" placeholder="who is on it?" value="${esc(f.speaker)}" onchange="newFiles[${i}].speaker=this.value">
      <button class="sm" onclick="newFiles.splice(${i},1);renderFiles()">remove</button></div>`).join("");
  $("#est").textContent = newFiles.length ? `${newFiles.length} file${newFiles.length > 1 ? "s" : ""} - estimate shown after the files are read` : "";
}
async function startEpisode() {
  if (!newFiles.length) return toast("Add at least one recording file first.", true);
  const guest = { name: $("#g_name").value.trim(), title: $("#g_title").value.trim(), institution: $("#g_inst").value.trim() };
  if (!guest.name) return toast("Please enter the guest's name (it is used for the title card and the research charts).", true);
  const vocab = $("#vocab").value.split("\n").map((s) => s.trim()).filter(Boolean);
  const body = { title: $("#e_title").value.trim(), guest, inputs: newFiles, preset: $("#preset").value,
    settings: { host_name: $("#host").value.trim(), vocabulary: vocab, burn_in_captions: $("#burn").checked } };
  $("#go").disabled = true;
  try {
    const r = await api("/episodes", body);
    await api(`/episodes/${encodeURIComponent(r.id)}/run`, $("#review").checked ? { until: "render_preview" } : {});
    toast(`Started. Estimated time: ${fmtDur(Object.values(r.estimate).reduce((a, b) => a + b, 0))}. You can minimise the app - you'll get a notification when it's done.`);
    location.hash = `#/episode/${encodeURIComponent(r.id)}`;
  } catch (e) { toast(e.message, true); $("#go").disabled = false; }
}

// ------------------------------------------------------------------ episode (progress / review / output)
let pollTimer = null;
async function episode(id, tab) {
  clearTimeout(pollTimer);
  id = decodeURIComponent(id);
  const d = await api(`/episodes/${encodeURIComponent(id)}`);
  const st = d.project.stages || {};
  const running = d.progress && d.progress.state === "running";
  const failed = d.result && d.result.state === "failed" || Object.values(st).some((s) => s.state === "failed");
  const allDone = d.stages.every((s) => st[s] && st[s].state === "done");
  const previewDone = st.render_preview && st.render_preview.state === "done";
  const title = d.project.title || id;
  view.innerHTML = `
    <div class="row"><h1>${esc(title)}</h1><span class="muted">${esc((d.project.guest || {}).name || "")}</span>
      <span class="right small muted">${d.project.duration ? "raw " + tc(d.project.duration) : ""}${d.project.duration_final ? " → edited " + tc(d.project.duration_final) : ""}</span></div>
    <div class="stages">${d.stages.map((s) => `<span class="${(st[s] || {}).state || ""}">${esc(d.stage_labels[s])}</span>`).join("")}</div>
    <div id="progress-area"></div>
    <div id="body"></div>`;
  const pa = $("#progress-area");
  if (running || (d.result && d.result.state === "cancelled")) {
    renderProgress(id, d);
  } else if (failed) {
    const r = d.result && d.result.state === "failed" ? d.result : { message: "A step failed earlier.", fix: "Press Resume to try again.", stage: Object.keys(st).find((k) => st[k].state === "failed") };
    pa.innerHTML = `<div class="err"><b>${esc(r.message)}</b><br>${esc(r.fix || "")}<div class="row" style="margin-top:10px">
      <button class="primary" onclick="runEp('${esc(id)}',{})">Resume</button><button onclick="showLog('${esc(id)}')">Show log file</button></div></div>`;
  } else if (allDone) {
    pa.innerHTML = `<div class="card row"><span class="badge ok">Finished</span><span>Outputs are in the <b>Output</b> folder of this episode.</span>
      <button class="right" onclick="S.openPath(${JSON.stringify(d.project.output_folder || "")})">Open folder</button>
      <button onclick="sendCanva('${esc(id)}')">Send to Canva</button></div>`;
  } else if (previewDone) {
    pa.innerHTML = `<div class="card row"><span class="badge warn">Ready for review</span><span>Check the preview and the cuts below, then render the final video.</span>
      <button class="right" onclick="runEp('${esc(id)}',{from:'edl',until:'render_preview'})">Re-make preview</button>
      <button class="primary" onclick="runEp('${esc(id)}',{from:'edl'})">Render final video</button></div>`;
  } else if (st.edit_plan && st.edit_plan.state === "done") {
    pa.innerHTML = `<div class="card row"><span class="badge warn">Changes not rendered yet</span><span>Re-make the preview to check your edits, or go straight to the final render.</span>
      <button class="right" onclick="runEp('${esc(id)}',{until:'render_preview'})">Re-make preview</button>
      <button class="primary" onclick="runEp('${esc(id)}',{})">Render final video</button></div>`;
  } else {
    pa.innerHTML = `<div class="card row"><span>Not started.</span><button class="primary right" onclick="runEp('${esc(id)}',{until:'render_preview'})">Start (pause for review)</button><button onclick="runEp('${esc(id)}',{})">Start (no review)</button></div>`;
  }
  if (d.project.transcript_note || d.project.diarization_note) pa.insertAdjacentHTML("beforeend", `<p class="small muted">${esc(d.project.diarization_note || "")}</p>`);
  if (st.edit_plan && st.edit_plan.state === "done") await renderReview(id, d, tab || "preview");
}
function renderProgress(id, d) {
  const pa = $("#progress-area");
  const p = d.progress || {};
  pa.innerHTML = `<div class="card">
      <div class="row"><b id="p-label">${esc(p.label || "Starting")}…</b><span id="p-detail" class="muted small">${esc(p.detail || "")}</span>
        <span class="right muted small" id="p-remain">${p.remaining ? "about " + fmtDur(p.remaining).replace("about ", "") + " left" : ""}</span></div>
      <div class="progress" style="margin:10px 0"><div id="p-bar" style="width:${Math.round((p.overall_fraction || 0) * 100)}%"></div></div>
      <div class="row small muted"><span>You can minimise this window - it keeps working and will notify you when finished.</span>
        <button class="right danger sm" onclick="cancelEp('${esc(id)}')">Cancel</button></div></div>`;
  const tick = async () => {
    try {
      const r = await api(`/episodes/${encodeURIComponent(id)}/progress`);
      const p = r.progress || {};
      if (p.state === "running") {
        $("#p-label").textContent = (p.label || "") + "…"; $("#p-detail").textContent = p.detail || "";
        $("#p-bar").style.width = Math.round((p.overall_fraction || 0) * 100) + "%";
        $("#p-remain").textContent = p.remaining ? fmtDur(p.remaining) + " left" : "";
        document.querySelectorAll(".stages span").forEach((el, i) => el.className = d.stages[i] === p.stage ? "running" : i < p.stage_index ? "done" : el.className.replace("running", ""));
        pollTimer = setTimeout(tick, 1000);
      } else if (p.state === "done" || (r.result && r.result.state === "done")) {
        S.notify("Podcast Studio", `${d.project.title || id}: finished.`); episode(id);
      } else if (p.state === "failed") {
        S.notify("Podcast Studio", `${d.project.title || id}: stopped - ${p.message}`); episode(id);
      } else if (p.state === "cancelled") { toast("Cancelled."); episode(id); }
      else if (r.running) pollTimer = setTimeout(tick, 1000);
      else episode(id);
    } catch (e) { pollTimer = setTimeout(tick, 2000); }
  };
  pollTimer = setTimeout(tick, 1000);
}
window.runEp = async (id, opts) => { await api(`/episodes/${encodeURIComponent(id)}/run`, opts); toast("Queued."); setTimeout(() => episode(id), 600); };
window.cancelEp = async (id) => { await api(`/episodes/${encodeURIComponent(id)}/cancel`, {}); toast("Cancelling after the current step…"); };
window.showLog = async (id) => { const l = await api(`/episodes/${encodeURIComponent(id)}/log`); $("#body").innerHTML = `<div class="card"><div class="row"><b>Log file</b><span class="mono muted small">${esc(l.path)}</span><button class="right sm" onclick="S.showInFolder(${JSON.stringify(l.path)})">Show file</button><button class="sm" onclick="navigator.clipboard.writeText(${JSON.stringify(l.text)});toast('Log copied')">Copy</button></div><pre>${esc(l.text)}</pre></div>`; };
window.sendCanva = async (id) => {
  try { const st = await api("/canva/status"); if (!st.connected) { toast("Connect Canva first in Settings.", true); location.hash = "#/settings"; return; }
    toast("Uploading to Canva…"); const r = await api(`/episodes/${encodeURIComponent(id)}/canva/send`, {}); toast(`Sent ${r.assets.length} assets to Canva${r.designs.length ? " and created a thumbnail design" : ""}.`);
    if (r.designs[0] && r.designs[0].url) S.openExternal(r.designs[0].url);
  } catch (e) { toast("Canva: " + e.message, true); }
};

// ------------------------------------------------------------------ review
let R = {};   // review state
async function renderReview(id, d, tab) {
  const [tr, dec, edl, music, charts] = await Promise.all([api(`/episodes/${encodeURIComponent(id)}/transcript`), api(`/episodes/${encodeURIComponent(id)}/decisions`),
    api(`/episodes/${encodeURIComponent(id)}/edl`), api(`/episodes/${encodeURIComponent(id)}/music`), api(`/episodes/${encodeURIComponent(id)}/charts`)]);
  R = { id, d, tr, dec, edl, music, charts, tab, sel: null };
  const tabs = ["preview", "transcript", "cuts", "chapters", "music", "charts", "edl", "output"];
  const names = { preview: "Preview", transcript: "Transcript", cuts: "Cuts & fillers", chapters: "Chapters", music: "Music", charts: "Charts", edl: "Edit list", output: "Outputs" };
  $("#body").innerHTML = `<div class="tabs">${tabs.map((t) => `<button class="${t === tab ? "active" : ""}" onclick="location.hash='#/episode/${encodeURIComponent(id)}/${t}'">${names[t]}</button>`).join("")}</div><div id="tab"></div>`;
  ({ preview: tabPreview, transcript: tabTranscript, cuts: tabCuts, chapters: tabChapters, music: tabMusic, charts: tabCharts, edl: tabEdl, output: tabOutput })[tab]();
}
const fileUrl = (rel) => `/files/${encodeURIComponent(R.id)}/${rel}`;
function tabPreview() {
  const has = (R.d.project.stages.render_preview || {}).state === "done";
  $("#tab").innerHTML = has ? `<video controls src="${fileUrl("renders/preview.mp4")}"></video><p class="muted small">Low-resolution preview with the current cuts, camera switches, music, charts and chapter cards. Use the other tabs to change things, then press <b>Re-make preview</b> or <b>Render final video</b> above.</p>`
    : `<p class="muted">The preview has not been rendered yet.</p>`;
}
function words() { const out = []; (R.tr.segments || []).forEach((s) => s.words.forEach((w) => { if (!w.removed) out.push(w); })); return out; }
function tabTranscript() {
  const ws = words();
  const deleted = new Set(); (R.dec.deletions || []).forEach((d) => d.word_ids.forEach((i) => deleted.add(String(i))));
  const fillers = new Set(); (R.dec.fillers || []).forEach((f) => { if (f.enabled && R.dec.settings.remove_fillers) f.word_ids.forEach((i) => fillers.add(String(i))); });
  const chapterAt = new Set((R.dec.chapters || []).map((c) => String(c.word_id)));
  const chartAt = {}; (R.charts.placements || []).forEach((c) => { if (c.enabled && c.src_in != null) { const w = ws.find((w) => w.start >= c.src_in); if (w) chartAt[String(w.id)] = c.title; } });
  const names = R.dec.speakers || R.tr.speakers || {};
  let html = `<div class="row"><span class="muted small">Click a word to correct it. Select a passage with the mouse and press <b>Delete selection</b> to cut it. Speakers: </span>
    ${Object.keys(names).map((k) => `<input type="text" style="width:150px" value="${esc(names[k])}" onchange="renameSpeaker('${esc(k)}',this.value)">`).join("")}
    <button class="right danger" id="delsel" disabled>Delete selection</button></div><div class="transcript" id="tx">`;
  let para = null;
  ws.forEach((w) => {
    if (!para || para !== w.speaker) { if (para) html += "</div>"; para = w.speaker; html += `<div class="para"><span class="spk">${esc(names[w.speaker] || w.speaker)}</span><span class="ts">${tc(w.start)}</span>`; }
    const cls = ["w", deleted.has(String(w.id)) ? "del" : "", fillers.has(String(w.id)) ? "fill" : "", chapterAt.has(String(w.id)) ? "chapter" : "", chartAt[String(w.id)] ? "chart" : ""].join(" ");
    html += `<span class="${cls}" data-id="${esc(w.id)}" title="${esc(tc(w.start))}${chartAt[String(w.id)] ? " · chart: " + esc(chartAt[String(w.id)]) : ""}">${esc(w.word)}</span> `;
  });
  html += "</div></div>";
  if (R.dec.deletions && R.dec.deletions.length) html += `<h3>Your deletions</h3><div class="list">${R.dec.deletions.map((d) => `<div class="item"><span class="grow">${d.word_ids.length} words · ${esc(d.note || "")}</span><button class="sm" onclick="undelete('${esc(d.id)}')">restore</button></div>`).join("")}</div>`;
  $("#tab").innerHTML = html;
  const tx = $("#tx");
  tx.addEventListener("click", (e) => { const el = e.target.closest(".w"); if (!el || window.getSelection().toString().length > 1) return; editWord(el); });
  document.addEventListener("selectionchange", () => {
    const sel = window.getSelection(); const btn = $("#delsel"); if (!btn) return;
    if (sel.isCollapsed || !tx.contains(sel.anchorNode)) { btn.disabled = true; R.sel = null; return; }
    const range = sel.getRangeAt(0); const ids = [...tx.querySelectorAll(".w")].filter((el) => range.intersectsNode(el)).map((el) => el.dataset.id);
    R.sel = ids; btn.disabled = ids.length === 0; btn.textContent = `Delete selection (${ids.length} words)`;
  });
  $("#delsel").onclick = async () => { if (!R.sel) return; const note = prompt("Note for this cut (optional):", "") ?? ""; await api(`/episodes/${encodeURIComponent(R.id)}/delete`, { word_ids: R.sel, note }); toast("Cut added. Re-make the preview to hear it."); episode(R.id, "transcript"); };
}
function editWord(el) {
  const box = document.createElement("div"); box.className = "editbox";
  const r = el.getBoundingClientRect(); box.style.left = r.left + "px"; box.style.top = r.bottom + 4 + "px";
  box.innerHTML = `<input type="text" value="${esc(el.textContent)}" style="width:260px"> <button class="sm">Save</button> <span class="muted small">empty = remove word</span>`;
  document.body.appendChild(box); const inp = box.querySelector("input"); inp.focus(); inp.select();
  const save = async () => { const v = inp.value; box.remove(); if (v === el.textContent) return; await api(`/episodes/${encodeURIComponent(R.id)}/correct`, { word_id: isNaN(el.dataset.id) ? el.dataset.id : Number(el.dataset.id), text: v }); toast("Corrected - timings updated."); episode(R.id, "transcript"); };
  box.querySelector("button").onclick = save; inp.onkeydown = (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") box.remove(); };
  setTimeout(() => document.addEventListener("mousedown", function h(e) { if (!box.contains(e.target)) { box.remove(); document.removeEventListener("mousedown", h); } }), 50);
}
window.renameSpeaker = async (label, name) => { await api(`/episodes/${encodeURIComponent(R.id)}/speakers`, { [label]: name }); toast("Speaker renamed."); };
window.undelete = async (id) => { await api(`/episodes/${encodeURIComponent(R.id)}/undelete`, { id }); episode(R.id, "transcript"); };
function tabCuts() {
  const dec = R.dec;
  $("#tab").innerHTML = `
    <div class="card"><div class="row"><label style="margin:0">Remove pauses longer than</label><input type="range" min="0.6" max="3" step="0.1" value="${dec.settings.min_silence}" id="minsil" oninput="$('#minsil-v').textContent=this.value+' s'"><span id="minsil-v">${dec.settings.min_silence} s</span>
      <label class="row" style="margin:0 0 0 30px;color:var(--text)"><input type="checkbox" id="rmfill" ${dec.settings.remove_fillers ? "checked" : ""}> Remove filler words</label>
      <button class="right" onclick="recalcCuts()">Apply & re-detect</button></div></div>
    <h2>Filler words (${dec.fillers.length}) <span class="muted small">untick any that are real speech</span></h2>
    <div class="list">${dec.fillers.map((f) => `<div class="item"><input type="checkbox" ${f.enabled ? "checked" : ""} onchange="setEnabled('fillers','${f.id}',this.checked)"><span class="muted small">${tc(f.start)}</span><span class="grow">${esc(f.context)}</span></div>`).join("")}</div>
    <h2>Silences (${dec.silences.length})</h2>
    <div class="list">${dec.silences.map((s) => `<div class="item"><input type="checkbox" ${s.enabled ? "checked" : ""} onchange="setEnabled('silences','${s.id}',this.checked)"><span class="muted small">${tc(s.start)}</span><span class="grow">${(s.gap).toFixed(1)} s pause ${s.source.includes("words-only") ? '<span class="badge warn">audio has sound here - left off</span>' : ""}</span></div>`).join("")}</div>
    <div class="row" style="margin-top:14px"><button class="primary" onclick="saveDecisions()">Save cuts</button></div>`;
}
window.setEnabled = (kind, id, en) => { const it = R.dec[kind].find((x) => x.id === id); if (it) it.enabled = en; };
window.saveDecisions = async () => { await api(`/episodes/${encodeURIComponent(R.id)}/decisions`, R.dec); toast("Saved. Re-make the preview to see the changes."); };
window.recalcCuts = async () => {
  await api(`/episodes/${encodeURIComponent(R.id)}/decisions`, R.dec);
  const s = { min_silence: Number($("#minsil").value), remove_fillers: $("#rmfill").checked };
  const meta = R.d.project; meta.settings = { ...(meta.settings || {}), ...s };
  await api(`/episodes/${encodeURIComponent(R.id)}/run`, { from: "edit_plan", until: "edit_plan", force: true, settings: s });
  toast("Re-detecting…"); setTimeout(() => episode(R.id, "cuts"), 1500);
};
function tabChapters() {
  const chs = R.dec.chapters || [];
  $("#tab").innerHTML = `<p class="muted small">Chapter titles are suggestions - edit them freely. Times are in the raw recording. A chapter card and transition are added at each break (except the first).</p>
    <div class="list" id="chs">${chs.map((c, i) => `<div class="item"><span class="muted small" style="width:70px">${tc(c.start)}</span><input type="text" value="${esc(c.title)}" onchange="R.dec.chapters[${i}].title=this.value;R.dec.chapters[${i}].auto=false">
      <label class="row" style="margin:0;color:var(--text)"><input type="checkbox" ${c.card !== false ? "checked" : ""} onchange="R.dec.chapters[${i}].card=this.checked"> card</label><button class="sm" onclick="R.dec.chapters.splice(${i},1);tabChapters()">remove</button></div>`).join("")}</div>
    <div class="row" style="margin-top:12px"><input type="text" id="newch-t" placeholder="New chapter title" style="width:280px"><input type="text" id="newch-time" placeholder="mm:ss" style="width:90px"><button onclick="addChapter()">Add</button>
      <span class="right"></span><label style="margin:0">Transition</label><select id="trtype" onchange="R.dec.transitions.type=this.value">${["fade", "slide", "wipe", "dissolve", "fadeblack", "circle"].map((t) => `<option ${(R.dec.transitions || {}).type === t ? "selected" : ""}>${t}</option>`).join("")}</select>
      <button class="primary" onclick="saveDecisions()">Save chapters</button></div>`;
}
window.addChapter = () => { const t = $("#newch-t").value.trim(), m = $("#newch-time").value.trim().split(":").map(Number); if (!t || m.some(isNaN)) return toast("Enter a title and a time like 12:30", true);
  const secs = m.length === 3 ? m[0] * 3600 + m[1] * 60 + m[2] : m[0] * 60 + (m[1] || 0); const w = words().find((w) => w.start >= secs) || words()[0];
  R.dec.chapters.push({ id: "chm" + Date.now(), start: w.start, title: t, keywords: [], word_id: w.id, auto: false, card: true }); R.dec.chapters.sort((a, b) => a.start - b.start); tabChapters(); };
function tabMusic() {
  const m = R.music || {};
  const slot = (k) => m[k] ? `<b>${esc(m[k].title)}</b> - ${esc(m[k].artist || "")} <span class="badge ${m[k].license === "own" ? "" : "ok"}">${esc(m[k].license || "")}</span> <button class="sm" onclick="chooseTrack('${k}',null)">remove</button>` : `<span class="muted">none</span>`;
  $("#tab").innerHTML = `
    <div class="card"><div class="kv"><span>Intro</span><span>${slot("intro")}</span><span>Outro</span><span>${slot("outro")}</span><span>Background bed</span><span>${slot("bed")}</span></div>
      ${m.attribution && m.attribution.length ? `<h3>Attribution (added to description.txt automatically)</h3><pre>${esc(m.attribution.join("\n"))}</pre>` : ""}
      <p class="small muted">You can also drop your own licensed files into this episode's <span class="mono">music/own/</span> folder as intro / outro / bed .mp3 or .wav.</p></div>
    <div class="card"><div class="row"><label style="margin:0">Mood</label><select id="mood">${["calm", "curious", "upbeat", "serious", "warm"].map((x) => `<option ${(settings.music_mood || "curious") === x ? "selected" : ""}>${x}</option>`).join("")}</select>
      <label style="margin:0">Length</label><select id="len"><option value="15,90">short (intro/outro)</option><option value="120,600">long (background bed)</option></select>
      <button onclick="searchMusic()">Search free music</button><span class="muted small">Jamendo${settings.jamendo_client_id ? "" : " (add a key in Settings)"} + Openverse</span></div><div id="tracks"></div></div>`;
}
window.searchMusic = async () => {
  $("#tracks").innerHTML = "<p class='muted'>Searching…</p>";
  const [a, b] = $("#len").value.split(",").map(Number);
  try {
    const r = await api(`/episodes/${encodeURIComponent(R.id)}/music/search`, { mood: $("#mood").value, min_s: a, max_s: b });
    $("#tracks").innerHTML = r.tracks.length ? `<div class="list">${r.tracks.map((t, i) => `<div class="item"><span class="grow"><b>${esc(t.title)}</b> - ${esc(t.artist)} <span class="muted small">${Math.round(t.duration)} s · ${esc(t.provider)}</span><br>
        <span class="badge ${t.verdict.ok ? "ok" : "bad"}">${esc(t.verdict.code.toUpperCase())}</span> <span class="small muted">${esc(t.verdict.reason)}</span></span>
        ${t.download ? `<audio controls preload="none" src="${esc(t.download)}" style="width:220px"></audio>` : ""}
        ${t.verdict.ok ? `<select id="slot${i}"><option>intro</option><option>outro</option><option>bed</option></select><button class="sm" onclick='chooseTrack($("#slot${i}").value, ${JSON.stringify(t).replace(/'/g, "&#39;")})'>Use</button>` : `<span class="badge bad">blocked</span>`}</div>`).join("")}</div>` : "<p class='muted'>No tracks found.</p>";
  } catch (e) { $("#tracks").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
window.chooseTrack = async (slot, track) => { try { R.music = await api(`/episodes/${encodeURIComponent(R.id)}/music/choose`, { slot, track }); toast(track ? "Track added. Re-make the preview to hear it." : "Removed."); tabMusic(); } catch (e) { toast(e.message, true); } };
function tabCharts() {
  const pl = R.charts.placements || [];
  const prof = R.charts.profile || {};
  $("#tab").innerHTML = `
    ${prof.missing && prof.missing.length ? `<div class="err small">${prof.missing.map(esc).join("<br>")}</div>` : ""}
    ${prof.candidates && prof.candidates.length && !prof.profile ? `<div class="card"><b>Which of these is the guest?</b><div class="list">${prof.candidates.slice(0, 6).map((c) => `<div class="item"><span class="grow">${esc(c.name)} - ${esc(c.institution || "no institution")} · ${c.works} works · ${c.cited_by} citations</span><button class="sm" onclick="pickAuthor('${esc(c.id)}')">This one</button></div>`).join("")}</div></div>` : ""}
    <div class="list">${pl.map((c) => `<div class="item" style="align-items:flex-start"><input type="checkbox" ${c.enabled ? "checked" : ""} onchange="placeChart('${c.key}',undefined,this.checked)">
      <img class="chart-thumb" src="${fileUrl("charts/" + c.key + ".png")}" onerror="this.style.display='none'">
      <div class="grow"><b>${esc(c.title)}</b>${c.nodata ? ' <span class="badge warn">no data</span>' : ""}<br><span class="small muted">${c.src_in != null ? `Shown at ${tc(c.src_in)} (raw) · cue: “${esc(c.cue)}”` : "Not placed yet"}</span><br>
        <div class="row" style="margin-top:6px"><input type="text" placeholder="mm:ss (raw time)" style="width:110px" id="t_${c.key}"><button class="sm" onclick="placeChart('${c.key}',$('#t_${c.key}').value)">Move here</button><button class="sm" onclick="S.openExternal('${fileUrl("charts/" + c.key + ".mp4")}')">Play animation</button></div></div></div>`).join("")}</div>
    <div class="card"><h3>Add a custom chart</h3><div class="grid2"><div><label>Title</label><input type="text" id="cc-title"><label>Type</label><select id="cc-kind"><option value="bar">bars</option><option value="barh">horizontal bars</option><option value="line">line</option></select><label>Data source (shown on the chart)</label><input type="text" id="cc-src" placeholder="e.g. Guest's CV, 2026"></div>
      <div><label>Paste numbers ("2019: 3, 2020: 5") or CSV with a header row</label><textarea id="cc-data" placeholder="year,papers\n2019,3\n2020,5"></textarea></div></div>
      <div class="row"><button onclick="addCustomChart()">Make chart</button><span class="muted small">Charts are re-rendered when you re-make the preview.</span></div></div>`;
}
window.placeChart = async (key, timeStr, enabled) => {
  const body = { key, enabled: enabled !== undefined ? enabled : true };
  if (timeStr !== undefined) { const m = String(timeStr).trim().split(":").map(Number); if (m.some(isNaN) || !timeStr) return toast("Enter a time like 12:30", true); body.src_in = m.length === 3 ? m[0] * 3600 + m[1] * 60 + m[2] : m[0] * 60 + (m[1] || 0); }
  R.charts.placements = await api(`/episodes/${encodeURIComponent(R.id)}/charts/place`, body); toast("Updated."); tabCharts();
};
window.addCustomChart = async () => {
  const data = $("#cc-data").value; const body = { title: $("#cc-title").value.trim(), kind: $("#cc-kind").value, source: $("#cc-src").value.trim() };
  if (!body.title || !data.trim()) return toast("Title and data are needed.", true);
  if (data.includes("\n") && data.split("\n")[0].includes(",") && isNaN(Number(data.split("\n")[0].split(",")[1]))) body.csv = data; else body.pasted = data;
  try { const r = await api(`/episodes/${encodeURIComponent(R.id)}/charts/custom`, body); toast(`Chart saved with ${r.rows} rows - it will render with the next preview.`); } catch (e) { toast(e.message, true); }
};
window.pickAuthor = async (oid) => { await api(`/episodes/${encodeURIComponent(R.id)}/guest`, { openalex_id: oid }); await api(`/episodes/${encodeURIComponent(R.id)}/run`, { from: "charts", until: "charts", force: true }); toast("Re-fetching charts for the chosen author…"); };
function tabEdl() { $("#tab").innerHTML = `<pre>${esc(R.edl.text || "No edit list yet.")}</pre>`; }
function tabOutput() {
  const p = R.d.project; const outs = p.outputs || [];
  $("#tab").innerHTML = `<div class="card"><div class="row"><b>Output folder</b><span class="mono small muted">${esc(p.output_folder || "")}</span><button class="right sm" onclick="S.openPath(${JSON.stringify(p.output_folder || "")})">Open</button>${p.output_zip ? `<button class="sm" onclick="S.showInFolder(${JSON.stringify(p.output_zip)})">Show zip</button>` : ""}<button class="sm" onclick="sendCanva('${esc(R.id)}')">Send to Canva</button></div>
    ${outs.length ? `<ul class="mono small">${outs.map((o) => `<li>${esc(o)}</li>`).join("")}</ul>` : "<p class='muted'>No outputs yet - render the final video first.</p>"}</div>
    <div class="card"><h3>QA report</h3><pre id="qa">loading…</pre></div><div class="card"><h3>Description for YouTube (chapters + music credits + data sources)</h3><pre id="desc">loading…</pre><button class="sm" onclick="navigator.clipboard.writeText($('#desc').textContent);toast('Copied')">Copy</button></div>`;
  fetch(fileUrl("Output/qa_report.txt")).then((r) => r.ok ? r.text() : "not available yet").then((t) => $("#qa").textContent = t);
  fetch(fileUrl("Output/description.txt")).then((r) => r.ok ? r.text() : "not available yet").then((t) => $("#desc").textContent = t);
}

// ------------------------------------------------------------------ settings
async function settingsPage() {
  settings = await api("/settings"); presets = await api("/presets");
  const b = settings.brand || {};
  const canva = await api("/canva/status");
  view.innerHTML = `<h1>Settings</h1>
  <div class="card"><h2 style="margin-top:0">Your show</h2><div class="grid2">
    <div><label>Podcast name (shown on title cards)</label><input id="s_podcast" type="text" value="${esc(b.podcast_name || "")}"></div>
    <div><label>Your name (host)</label><input id="s_host" type="text" value="${esc(settings.host_name || "")}"></div>
    <div><label>Episodes folder</label><div class="row"><input id="s_dir" type="text" value="${esc(settings.episodes_dir)}" readonly><button class="sm" onclick="pickDir()">Change…</button></div></div>
    <div><label>Contact email (sent with research-data lookups so OpenAlex/Crossref give you their faster "polite" lane)</label><input id="s_email" type="email" value="${esc(settings.contact_email || "")}"></div></div></div>
  <div class="card"><h2 style="margin-top:0">Look & feel (brand)</h2><div class="row">
    <label style="margin:0">Primary <input type="color" id="b_primary" value="${esc(b.primary || "#1F3A5F")}"></label><label style="margin:0">Accent <input type="color" id="b_accent" value="${esc(b.accent || "#E8B04B")}"></label>
    <label style="margin:0">Background <input type="color" id="b_bg" value="${esc(b.background || "#0F1B2D")}"></label><label style="margin:0">Text <input type="color" id="b_text" value="${esc(b.text || "#FFFFFF")}"></label></div>
    <label>Font file (.ttf/.otf, optional - leave empty for the built-in Inter)</label><input id="b_font" type="text" value="${esc(b.font_file || "")}" placeholder="/Users/you/Library/Fonts/MyFont-Bold.ttf">
    <div class="grid2"><div><label>Transition at chapter breaks</label><select id="s_tr">${["fade", "slide", "wipe", "dissolve", "fadeblack", "circle"].map((t) => `<option ${settings.transition === t ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    <div><label>Chapter cards</label><select id="s_cards"><option value="true" ${settings.chapter_cards ? "selected" : ""}>show a chapter title card at each break</option><option value="false" ${!settings.chapter_cards ? "selected" : ""}>no cards, just the transition</option></select></div></div></div>
  <div class="card"><h2 style="margin-top:0">Editing defaults</h2><div class="grid2">
    <div><label>Remove pauses longer than (seconds)</label><input id="s_sil" type="number" step="0.1" value="${settings.min_silence}"></div>
    <div><label>Minimum chapter length (minutes)</label><input id="s_chap" type="number" value="${Math.round(settings.min_chapter_s / 60)}"></div>
    <div><label>Transcription quality</label><select id="s_q"><option value="balanced" ${settings.quality === "balanced" ? "selected" : ""}>balanced (recommended)</option><option value="max" ${settings.quality === "max" ? "selected" : ""}>maximum accuracy (slower)</option></select></div>
    <div><label>Filler words to remove (comma separated)</label><input id="s_fill" type="text" value="${esc((settings.filler_words || ["um", "uh", "er", "erm", "you know", "i mean", "sort of", "kind of"]).join(", "))}"></div>
    <div><label>Music</label><select id="s_mood">${["calm", "curious", "upbeat", "serious", "warm"].map((x) => `<option ${settings.music_mood === x ? "selected" : ""}>${x}</option>`).join("")}</select>
      <label class="row" style="color:var(--text)"><input type="checkbox" id="s_bed" ${settings.music_bed ? "checked" : ""}> Low background music bed under the talk (ducked)</label>
      <label class="row" style="color:var(--text)"><input type="checkbox" id="s_sa" ${settings.allow_sharealike ? "checked" : ""}> Allow CC BY-SA tracks (only if you publish episodes under CC BY-SA)</label></div>
    <div><label>Captions</label><label class="row" style="color:var(--text)"><input type="checkbox" id="s_burn" ${settings.burn_in_captions ? "checked" : ""}> Always make a burned-in caption version</label>
      <label class="row" style="color:var(--text)"><input type="checkbox" id="s_voice" ${settings.vtt_voice_tags ? "checked" : ""}> Speaker names in .vtt captions</label>
      <label class="row" style="color:var(--text)"><input type="checkbox" id="s_hw" ${settings.use_hardware_encoder !== false ? "checked" : ""}> Use GPU video encoder when available</label></div></div></div>
  <div class="card"><h2 style="margin-top:0">Keys & logins (one-time)</h2>
    <h3>Speaker detection - Hugging Face token (free)</h3>
    <ol class="steps small"><li>Go to <a href="#" onclick="S.openExternal('https://huggingface.co/join');return false">huggingface.co/join</a> and create a free account.</li>
      <li>Open <a href="#" onclick="S.openExternal('https://huggingface.co/pyannote/speaker-diarization-3.1');return false">pyannote/speaker-diarization-3.1</a> and <a href="#" onclick="S.openExternal('https://huggingface.co/pyannote/segmentation-3.0');return false">pyannote/segmentation-3.0</a>, click <b>Agree and access repository</b> on each (free, they just ask for your name).</li>
      <li>Go to <a href="#" onclick="S.openExternal('https://huggingface.co/settings/tokens');return false">Settings → Access Tokens</a>, click <b>Create new token</b> (type: Read), copy it and paste it below. Without it the app uses a basic built-in voice separator (less accurate).</li></ol>
    <input id="k_hf" type="password" value="${esc(settings.hf_token || "")}" placeholder="hf_…">
    <h3>Music - Jamendo client ID (free; Openverse works without a key)</h3>
    <ol class="steps small"><li>Go to <a href="#" onclick="S.openExternal('https://devportal.jamendo.com/');return false">devportal.jamendo.com</a>, sign up and create an app (any name).</li><li>Copy the <b>Client ID</b> and paste it here.</li></ol>
    <input id="k_jam" type="text" value="${esc(settings.jamendo_client_id || "")}" placeholder="client id">
    <h3>Canva (optional) - send charts, title cards and thumbnails to your Canva account</h3>
    <ol class="steps small"><li>Go to <a href="#" onclick="S.openExternal('https://www.canva.com/developers/integrations/connect-api');return false">canva.com/developers → Your integrations</a> and click <b>Create an integration</b> (Public or Private - private is fine for your own account).</li>
      <li>Under <b>Scopes</b> tick: asset:read, asset:write, design:content:write, design:meta:read.</li>
      <li>Under <b>Authentication</b> add this redirect URL exactly: <span class="mono">http://127.0.0.1:5890/canva/callback</span></li>
      <li>Copy the <b>Client ID</b> and generate a <b>Client secret</b>, paste both below, click Save, then <b>Connect Canva</b> and approve in the browser window.</li></ol>
    <div class="grid2"><input id="k_cid" type="text" value="${esc(settings.canva_client_id || "")}" placeholder="Canva client ID"><input id="k_csec" type="password" value="${esc(settings.canva_client_secret || "")}" placeholder="Canva client secret"></div>
    <div class="row" style="margin-top:8px"><button onclick="saveSettings().then(()=>api('/canva/login'))" ${canva.configured ? "" : "disabled"}>Connect Canva</button><span class="badge ${canva.connected ? "ok" : ""}">${canva.connected ? "connected" : canva.configured ? "not connected yet" : "add keys first"}</span></div></div>
  <div class="card"><h2 style="margin-top:0">Presets</h2><p class="small muted">A preset is a saved copy of the settings above. "Standard episode" is used unless you pick another one on the New episode screen.</p>
    <div class="row"><select id="preset_sel">${Object.keys(presets).map((p) => `<option>${esc(p)}</option>`).join("")}</select><button onclick="savePreset()">Save current settings as…</button><button class="danger" onclick="deletePreset()">Delete selected</button></div></div>
  <div class="row"><button class="primary" onclick="saveSettings().then(()=>toast('Settings saved'))">Save settings</button></div>`;
}
window.pickDir = async () => { const p = await S.pickFolder(); if (p) { $("#s_dir").value = p; await api("/settings", { episodes_dir: p }); toast("Episodes folder changes after you restart the app."); } };
function collectSettings() {
  return { host_name: $("#s_host").value.trim(), contact_email: $("#s_email").value.trim(),
    brand: { ...(settings.brand || {}), podcast_name: $("#s_podcast").value.trim(), primary: $("#b_primary").value, accent: $("#b_accent").value, background: $("#b_bg").value, text: $("#b_text").value, font_file: $("#b_font").value.trim() },
    transition: $("#s_tr").value, chapter_cards: $("#s_cards").value === "true", min_silence: Number($("#s_sil").value), min_chapter_s: Number($("#s_chap").value) * 60, quality: $("#s_q").value,
    filler_words: $("#s_fill").value.split(",").map((s) => s.trim()).filter(Boolean), music_mood: $("#s_mood").value, music_bed: $("#s_bed").checked, allow_sharealike: $("#s_sa").checked,
    burn_in_captions: $("#s_burn").checked, vtt_voice_tags: $("#s_voice").checked, use_hardware_encoder: $("#s_hw").checked,
    hf_token: $("#k_hf").value.trim(), jamendo_client_id: $("#k_jam").value.trim(), canva_client_id: $("#k_cid").value.trim(), canva_client_secret: $("#k_csec").value.trim() };
}
window.saveSettings = async () => { settings = await api("/settings", collectSettings()); };
window.savePreset = async () => { const name = prompt("Preset name:", "Standard episode"); if (!name) return; presets = await api("/presets", { name, settings: collectSettings() }); toast(`Preset "${name}" saved.`); settingsPage(); };
window.deletePreset = async () => { const n = $("#preset_sel").value; if (n === "Standard episode") return toast("The standard preset can't be deleted (you can overwrite it).", true); presets = await api("/presets", { delete: n }); settingsPage(); };

// ------------------------------------------------------------------ help / updates
async function helpPage() {
  const info = await S.appInfo();
  view.innerHTML = `<h1>Help</h1>
  <div class="card"><h2 style="margin-top:0">How an episode works</h2><ol class="steps">
    <li><b>New episode</b> → drop the recordings (one file per camera/mic is ideal), type the guest's name, press <b>Edit My Episode</b>.</li>
    <li>The app transcribes, finds who is speaking, removes long pauses and filler words, picks camera angles, finds chapters, fetches research charts and music, and renders a low-res <b>preview</b>. You can close the window - it keeps going and notifies you.</li>
    <li><b>Review</b>: fix words (click), cut passages (select → Delete), untick filler words that were real speech, rename chapters, swap music, move charts. Then <b>Render final video</b>.</li>
    <li>Everything lands in the episode's <b>Output</b> folder: MP4 (1080p, −14 LUFS), .srt/.vtt captions, transcript .docx/.txt, chapters, description text with music credits, thumbnail, charts and a QA report.</li></ol></div>
  <div class="card"><h2 style="margin-top:0">If something goes wrong</h2><p>Every error screen has a <b>Resume</b> button - finished steps are never redone. The log file for an episode is in its <span class="mono">logs/engine.log</span>; the app's own log is <span class="mono">${esc(info.engineLog || "")}</span>. Send either file when asking for help.</p>
    <button onclick="S.showInFolder(${JSON.stringify(info.engineLog || "")})">Show app log file</button></div>
  <div class="card"><h2 style="margin-top:0">Updates</h2><p>Version ${esc(info.version)}. <span id="upd" class="muted">Checking…</span></p><div class="row"><button onclick="checkUpd()">Check for updates</button><button id="dl" style="display:none" onclick="S.downloadUpdate()">Download</button><button id="inst" style="display:none" class="primary" onclick="S.installUpdate()">Restart and install</button></div></div>`;
  checkUpd();
}
window.checkUpd = async () => { const r = await S.checkUpdate(); showUpdate(r); };
function showUpdate(s) {
  const el = $("#upd"), b = $("#update-badge");
  const msg = s.state === "available" ? `Version ${s.version} is available.` : s.state === "downloaded" ? "Update downloaded." : s.state === "error" ? `Could not check: ${s.message}` : "You have the latest version.";
  if (el) el.textContent = msg;
  if (b) b.innerHTML = s.state === "available" ? `<a href="#/help">⬆ Update ${esc(s.version)} available</a>` : "";
  if ($("#dl")) $("#dl").style.display = s.state === "available" ? "" : "none";
  if ($("#inst")) $("#inst").style.display = s.state === "downloaded" ? "" : "none";
}
S.onUpdateProgress((p) => { const el = $("#upd"); if (el) el.textContent = `Downloading… ${Math.round(p.percent)}%`; });
boot();
