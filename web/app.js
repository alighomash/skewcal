"use strict";
// Skewcal browser app: UI, charts and the bridge to the Pyodide worker.

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fx = (v, d) => (v == null || !isFinite(v) ? "–" : Number(v).toFixed(d));
const sgn = (v, d) => (v == null || !isFinite(v) ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(d));
const IN_ARTIFACT = !!(window.claude && window.claude.use);

const store = {
  get(k) { try { return JSON.parse(localStorage.getItem("skewcal:" + k)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem("skewcal:" + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

const state = {
  mode: "scan",
  files: [],            // {name, file, status: "ready"|"running"|"done"|"failed", error, orient}
  result: null,         // combine output
  overlays: {},         // image name -> object URL
  sample: null,         // {key, title} when the shown result comes from sample scans
  view: { image: 0, kind: "overlay", gain: 150 },
  running: false,
  os: "mac",
};

// ------------------------------------------------------------------ toast
let toastTimer = 0;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3200);
}

// ------------------------------------------------------------------ saving files
let downloadsP = null;
function downloadsCap() {
  if (!IN_ARTIFACT) return Promise.resolve(null);
  if (!downloadsP) downloadsP = window.claude.use("downloads").catch(() => null);
  return downloadsP;
}
async function saveFile(filename, data) {
  const dl = await downloadsCap();
  if (dl) {
    try {
      await dl.save({ filename, data });
      toast("Saved " + filename);
    } catch (e) {
      if (e && e.code === "declined") return;
      if (e && e.code === "rate_limited") toast("A save prompt is already open.");
      else toast("Couldn't save " + filename + (e && e.message ? ": " + e.message : "."));
    }
    return;
  }
  const blob = data instanceof Blob ? data : new Blob([data]);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
}
async function saveAsset(path, filename) {
  try {
    const r = await fetch(path);
    if (!r.ok) throw new Error(r.status);
    await saveFile(filename, await r.blob());
  } catch (e) {
    toast("Couldn't load " + filename + ".");
  }
}

// ------------------------------------------------------------------ engine (worker)
const engine = {
  worker: null,
  seq: 0,
  pending: new Map(),
  stage: "idle",
  start() {
    if (this.worker) return;
    this.worker = new Worker("worker.js");
    this.worker.onmessage = (ev) => {
      const m = ev.data;
      if (m.type === "status") return setEngine(m.stage, m.detail);
      const p = this.pending.get(m.id);
      if (!p) return;
      this.pending.delete(m.id);
      m.ok ? p.resolve(m.result) : p.reject(new Error(m.error));
    };
    this.worker.onerror = (e) => setEngine("failed", e.message || "worker error");
  },
  call(cmd, args = {}, transfer = []) {
    this.start();
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ id, cmd, args }, transfer);
    });
  },
};
const ENGINE_TEXT = {
  idle: "Analysis runs in your browser. Images are not uploaded.",
  runtime: "Starting Python…",
  packages: "Loading OpenCV and NumPy…",
  scipy: "Loading SciPy for phone photos…",
  ready: "Engine ready. Images stay on this device.",
  failed: "The analysis engine didn't load.",
};
function setEngine(stage, detail) {
  engine.stage = stage;
  const el = $("#engine");
  el.dataset.state = stage === "ready" ? "ready" : stage === "failed" ? "failed" : stage === "idle" ? "idle" : "loading";
  $("#engineText").textContent = ENGINE_TEXT[stage] || stage;
  if (stage === "failed" && detail) el.title = detail;
  if (state.running) {
    const base = { runtime: 4, packages: 14, scipy: 24, ready: 28 }[stage];
    if (base != null) setProgress("#progress", base);
  }
}
function setProgress(sel, pct) {
  const p = $(sel);
  p.hidden = pct == null;
  if (pct != null) p.firstElementChild.style.width = Math.max(2, Math.min(100, pct)) + "%";
}

// ------------------------------------------------------------------ tabs
function showTab(name) {
  for (const t of $$(".tab")) t.setAttribute("aria-selected", String(t.dataset.tab === name));
  for (const p of $$(".panel")) p.hidden = p.id !== "panel-" + name;
  store.set("tab", name);
  if (name === "correct") renderCorrect();
}
for (const t of $$(".tab")) t.addEventListener("click", () => showTab(t.dataset.tab));

// ------------------------------------------------------------------ files
const IMG_EXT = /\.(tiff?|png|jpe?g|heic|heif|bmp|webp)$/i;
const isHeic = (f) => /\.(heic|heif)$/i.test(f.name) || /hei[cf]/i.test(f.type);

function setMode(mode) {
  state.mode = mode;
  $("#modeScan").setAttribute("aria-pressed", String(mode === "scan"));
  $("#modePhoto").setAttribute("aria-pressed", String(mode === "photo"));
  $("#scanOpts").hidden = mode !== "scan";
  $("#photoOpts").hidden = mode !== "photo";
  $("#dropHint").textContent = mode === "scan"
    ? "Drop two or more scans here, or click to choose. TIFF, PNG or JPEG."
    : "Drop your photos here (ideally four, plate turned 90° each time), or click to choose. JPEG or HEIC, original files.";
  store.set("mode", mode);
}
$("#modeScan").addEventListener("click", () => setMode("scan"));
$("#modePhoto").addEventListener("click", () => setMode("photo"));

function addFiles(list) {
  const incoming = Array.from(list).filter((f) => IMG_EXT.test(f.name) || f.type.startsWith("image/"));
  if (!incoming.length) return toast("Those files aren't images Skewcal can read.");
  if (!state.files.length && incoming.every(isHeic)) setMode("photo");
  for (const f of incoming) {
    let name = f.name;
    for (let k = 2; state.files.some((x) => x.name === name); k++) name = f.name.replace(/(\.[^.]*)?$/, ` (${k})$1`);
    state.files.push({ name, file: f, status: "ready" });
  }
  renderFiles();
  engine.start();                 // warm up while the user reviews the list
  engine.call("init").catch(() => {});
}
// plate position of an image relative to another, to the nearest 90 degrees
function relPos(rot, rot0) {
  if (rot == null || rot0 == null) return "?";
  return ((Math.round((rot - rot0) / 90) % 4) + 4) % 4 * 90;
}

function renderFiles() {
  const ul = $("#fileList");
  ul.innerHTML = state.files.map((f, i) => {
    let chip = "";
    if (f.status === "running") chip = '<span class="chip info">measuring</span>';
    else if (f.status === "done") chip = `<span class="chip ok">plate at ${relPos(f.rot, state.files.find((x) => x.status === "done").rot)}°</span>`;
    else if (f.status === "failed") chip = '<span class="chip bad">failed</span>';
    return `<li><span title="${esc(f.name)}">${esc(f.name)}</span>${chip || "<span></span>"}<button type="button" aria-label="Remove ${esc(f.name)}" data-rm="${i}" ${state.running ? "disabled" : ""}>×</button></li>`;
  }).join("");
  $("#runBtn").disabled = state.running || !state.files.length;
  $("#runBtn").textContent = state.running ? "Measuring…" : state.files.length === 1 ? "Measure 1 image" : `Measure ${state.files.length || ""} images`.replace("  ", " ");
}
$("#fileList").addEventListener("click", (e) => {
  const b = e.target.closest("[data-rm]");
  if (!b) return;
  state.files.splice(Number(b.dataset.rm), 1);
  renderFiles();
});
$("#fileInput").addEventListener("change", (e) => { addFiles(e.target.files); e.target.value = ""; });
for (const [zone, handler] of [["#drop", addFiles], ["#gdrop", (fl) => fl[0] && correctGcode(fl[0])]]) {
  const z = $(zone);
  z.addEventListener("dragover", (e) => { e.preventDefault(); z.classList.add("over"); });
  z.addEventListener("dragleave", () => z.classList.remove("over"));
  z.addEventListener("drop", (e) => { e.preventDefault(); z.classList.remove("over"); handler(e.dataTransfer.files); });
}

// HEIC: the browser decodes it (Safari can), exifr reads the focal length
let exifrP = null;
function loadExifr() {
  if (!exifrP) {
    exifrP = new Promise((res) => {
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/exifr@7.1.3/dist/full.umd.js";
      s.onload = () => res(window.exifr || null);
      s.onerror = () => res(null);
      document.head.appendChild(s);
    });
  }
  return exifrP;
}
async function decodeGray(file) {
  let bmp;
  try {
    bmp = await createImageBitmap(file);
  } catch {
    throw new Error("This browser can't open HEIC photos. Open Skewcal in Safari, or export the photos as JPEG (on iPhone: Settings → Camera → Formats → Most Compatible).");
  }
  const c = document.createElement("canvas");
  c.width = bmp.width; c.height = bmp.height;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bmp, 0, 0);
  const d = ctx.getImageData(0, 0, c.width, c.height).data;
  const g = new Uint8Array(c.width * c.height);
  for (let i = 0, j = 0; i < g.length; i++, j += 4) g[i] = (d[j] * 299 + d[j + 1] * 587 + d[j + 2] * 114 + 500) / 1000;
  return { buffer: g.buffer, width: c.width, height: c.height };
}

function friendlyError(msg) {
  if (/resolution unknown/i.test(msg)) return "The file doesn't say what dpi it was scanned at. Enter the scan dpi in the box and measure again.";
  if (/reference markers/i.test(msg)) return msg.replace(/^.*?: /, "") + " Is this a phone photo of the reference sheet? Switch to Scanner for scans.";
  if (/plate|holes/i.test(msg)) return msg + " Check the whole plate is in the image, flat and in focus.";
  return msg;
}

async function runAnalysis() {
  if (state.running || !state.files.length) return;
  state.running = true;
  renderFiles();
  setProgress("#progress", 2);
  $("#runNote").textContent = engine.stage === "ready" ? "Measuring…" : "Loading the analysis engine…";
  const opts = { plate: $("#optPlate").value };
  store.set("plate", opts.plate);
  if (state.mode === "scan") {
    const dpi = parseFloat($("#optDpi").value);
    if (dpi > 0) opts.dpi = dpi;
  } else {
    const sx = parseFloat($("#optSpanX").value), sy = parseFloat($("#optSpanY").value);
    if (sx > 0) opts.span_x = sx;
    if (sy > 0) opts.span_y = sy;
    const bar = parseFloat($("#optBar").value);
    if (bar > 0) opts.screen_bar = bar;
    opts.warp = { auto: "auto", on: true, off: false }[$("#optWarp").value];
  }
  const overlays = {};
  try {
    await engine.call("reset");
    const n = state.files.length;
    for (let i = 0; i < n; i++) {
      const f = state.files[i];
      f.status = "running"; f.error = null;
      renderFiles();
      $("#runNote").textContent = `Measuring image ${i + 1} of ${n}…`;
      setProgress("#progress", 30 + (70 * i) / n);
      const o = { ...opts };
      let gray = null, buffer;
      try {
        if (isHeic(f.file)) {
          const dec = await decodeGray(f.file);
          gray = { width: dec.width, height: dec.height };
          buffer = dec.buffer;
          const ex = await loadExifr();
          if (ex) {
            try {
              const tags = await ex.parse(f.file, ["FocalLengthIn35mmFormat"]);
              if (tags && tags.FocalLengthIn35mmFormat) Object.assign(o, { focal35: tags.FocalLengthIn35mmFormat, width: dec.width, height: dec.height });
            } catch { /* no EXIF: the engine estimates the focal length */ }
          }
        } else {
          buffer = await f.file.arrayBuffer();
        }
        const r = await engine.call("analyze_one", { mode: state.mode, name: f.name, buffer, gray, opts: o }, [buffer]);
        if (r.data.ok) {
          f.status = "done";
          f.orient = r.data.result.plate_x_along_frame_x ? "A" : "B";
          f.rot = r.data.result.rotation_deg;
          if (r.overlay) overlays[f.name] = URL.createObjectURL(new Blob([r.overlay], { type: "image/jpeg" }));
        } else {
          f.status = "failed";
          f.error = friendlyError(r.data.error);
        }
      } catch (e) {
        f.status = "failed";
        f.error = friendlyError(e.message);
        if (engine.stage === "failed") throw e;
      }
      renderFiles();
    }
    const c = await engine.call("combine", { mode: state.mode, names: state.files.map((f) => f.name) });
    for (const im of c.data.images || []) {
      const f = state.files.find((x) => x.name === im.name);
      if (f && f.error) im.error = f.error;
    }
    for (const u of Object.values(state.overlays)) if (u.startsWith("blob:")) URL.revokeObjectURL(u);
    state.overlays = overlays;
    state.sample = null;
    state.view.image = 0;
    if (c.data.ok) {
      state.result = c.data;
      store.set("last", { theta: c.data.combined.theta, theta_std: c.data.combined.theta_std, when: Date.now() });
      $("#runNote").textContent = "Done. Results are on the right.";
    } else {
      state.result = { ok: false, images: c.data.images, mode: state.mode };
      $("#runNote").textContent = "No image could be measured. See the messages on the right.";
    }
    renderResults();
  } catch (e) {
    $("#runNote").textContent = "The analysis engine didn't load: " + e.message + ". Reload the page to try again.";
  } finally {
    state.running = false;
    setProgress("#progress", null);
    renderFiles();
  }
}
$("#runBtn").addEventListener("click", runAnalysis);

// ------------------------------------------------------------------ samples
let samples = [];
async function loadSamples() {
  try {
    samples = await (await fetch("samples/index.json")).json();
  } catch { samples = []; }
  $("#samples").innerHTML = samples.map((s, i) =>
    `<button class="btn small" data-sample="${i}" style="justify-content:flex-start">${esc(s.title)} <span class="muted">· ${s.files.length} scans</span></button>`).join("");
}
$("#samples").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-sample]");
  if (!b || state.running) return;
  const s = samples[Number(b.dataset.sample)];
  b.disabled = true;
  try {
    const files = await Promise.all(s.files.map(async (n) => new File([await (await fetch("samples/" + n)).blob()], n, { type: "image/jpeg" })));
    state.files = files.map((f) => ({ name: f.name, file: f, status: "ready" }));
    setMode(s.mode);
    $("#optDpi").value = "";
    renderFiles();
    await runAnalysis();
    if (state.result && state.result.ok) state.sample = { key: s.key, title: s.title };
    renderResults();
  } catch {
    toast("Couldn't load the sample scans.");
  } finally {
    b.disabled = false;
  }
});

// ------------------------------------------------------------------ results
function verdict(skew) {
  const a = Math.abs(skew);
  if (a < 0.02) return '<span class="chip ok">Square. No correction needed</span>';
  if (a < 0.05) return '<span class="chip info">Small skew. Correction optional</span>';
  return '<span class="chip warn">Correction recommended</span>';
}

function renderResults() {
  const root = $("#results");
  const R = state.result;
  if (!R) { root.innerHTML = ""; return; }
  if (!R.ok) {
    root.innerHTML = `<div class="card stack"><h2>No measurement</h2>${imageErrors(R.images)}</div>`;
    return;
  }
  const c = R.combined;
  const skew = c.theta - 90;
  const per100 = 100 * Math.tan((skew * Math.PI) / 180);
  const diag = Math.SQRT2 * 100 * Math.cos((c.theta * Math.PI) / 180);
  const okImgs = R.images.filter((i) => i.ok);
  const banner = state.sample
    ? `<div class="banner"><span class="chip info">Example</span><span>Sample scans: ${esc(state.sample.title)}. Add your own images on the left to measure your printer.</span></div>` : "";
  const lean = Math.abs(skew) < 0.02 ? "X and Y are square to within the measurement uncertainty."
    : skew < 0 ? "The Y axis leans toward +X. Corners at +X+Y and −X−Y are acute."
    : "The Y axis leans toward −X. Corners at +X+Y and −X−Y are obtuse.";

  root.innerHTML = `
    ${banner}
    <div class="card readout">
      <div class="angle">
        <span class="label">XY angle</span>
        <div class="row" style="align-items:baseline;gap:4px 12px"><div class="big">${fx(c.theta, 3)}°</div><span class="pm num">± ${fx(c.theta_std, 3)}°</span></div>
        <div class="row">${verdict(skew)}${c.reversal ? '<span class="chip ok">90° turn: scanner error removed</span>' : '<span class="chip warn">No 90° turn</span>'}</div>
        <p class="note">${lean}</p>
      </div>
      <div class="facts">
        <div class="fact"><div class="label">Skew</div><div class="v">${sgn(skew, 3)}°</div><div class="s">from a perfect 90°</div></div>
        <div class="fact"><div class="label">X drift</div><div class="v">${fx(Math.abs(per100), 2)} mm</div><div class="s">per 100 mm of Y travel</div></div>
        <div class="fact"><div class="label">Diagonals</div><div class="v">${fx(Math.abs(diag), 2)} mm</div><div class="s">difference on a 100 mm square</div></div>
        <div class="fact"><div class="label">${R.mode === "scan" ? "Scanner" : okImgs[0].result.reference === "screen" ? "Screen" : "Sheet"} error</div><div class="v">${c.reversal ? sgn(c.frame_bias_deg, 3) + "°" : "not removed"}</div><div class="s">${c.reversal ? "measured and cancelled" : "add a turned image"}</div></div>
      </div>
    </div>

    <div class="grid2">
      <div class="card">
        <header><h3>Shape of your XY frame</h3><span class="note" id="exag"></span></header>
        <div id="skewDiagram"></div>
      </div>
      <div class="card">
        <header><h3>Each image</h3></header>
        <div id="reversalChart"></div>
        <div class="legend" style="margin-top:8px"><span><i style="background:var(--accent)"></i>Plate at 0° or 180°</span><span><i style="background:var(--mark)"></i>Plate at 90° or 270°</span><span><i style="background:var(--ink);border-radius:2px;height:3px;width:14px;vertical-align:3px"></i>Combined result</span></div>
        <p class="note" style="margin-top:8px">${c.reversal
          ? "The two positions disagree by twice the scanner's own squareness error. The combined result sits halfway between them, so that error cancels."
          : "All images have the plate in the same position, so the scanner's own squareness error is still in this result. Add an image with the plate turned 90°."}</p>
      </div>
    </div>

    <div class="card">
      <header>
        <h3>Hole fit</h3>
        <div class="row">
          <div class="seg" role="group" aria-label="View">
            <button type="button" data-kind="overlay" aria-pressed="${state.view.kind === "overlay"}">Image overlay</button>
            <button type="button" data-kind="residual" aria-pressed="${state.view.kind === "residual"}">Residual map</button>
          </div>
        </div>
      </header>
      <div class="row" style="margin-bottom:12px" id="imgPick">${okImgs.map((im, i) =>
        `<button type="button" class="btn small" data-img="${i}" style="${i === state.view.image ? "border-color:var(--accent);color:var(--accent)" : ""}">${esc(im.name)}</button>`).join("")}</div>
      <div id="viewer"></div>
    </div>

    <div class="grid2">
      <div class="card">
        <header><h3>Scale</h3><span class="note">hole spacing, measured ÷ nominal</span></header>
        <div id="scaleBars"></div>
        <p class="note" style="margin-top:10px">${R.mode === "photo" && !(okImgs[0].result.sheet_scale && (okImgs[0].result.sheet_scale[0] !== 1 || okImgs[0].result.sheet_scale[1] !== 1))
          ? (okImgs[0].result.reference === "screen"
            ? "Assumes the tablet's published ppi is exact, which is often true only to about 0.3%. Measure the on-screen 100 mm bar with calipers and enter it for exact scale."
            : "Assumes the reference sheet printed at exactly 100%. Enter your measured sheet spans for exact scale.")
          : `To compensate in the slicer, scale objects to X ${fx(100 / c.scale_x, 2)}% and Y ${fx(100 / c.scale_y, 2)}%. Only worth it for tight fits.`}</p>
      </div>
      <div class="card">
        <header><h3>Quality checks</h3></header>
        <ul class="checks" id="checks"></ul>
      </div>
    </div>

    <div class="card">
      <header><h3>Per image</h3></header>
      <div class="table-wrap">${imageTable(R)}</div>
      ${imageErrors(R.images)}
    </div>

    <div class="card">
      <details class="report">
        <summary>Full report</summary>
        <pre class="report">${esc(R.report)}</pre>
      </details>
      <div class="row" style="margin-top:12px">
        <button class="btn small" id="saveTxt">Save report (.txt)</button>
        <button class="btn small" id="saveJson">Save data (.json)</button>
        <button class="btn small primary" id="goCorrect">Correct with ${fx(c.theta, 4)}°</button>
      </div>
    </div>`;

  drawSkew(c.theta);
  drawReversal(okImgs, c);
  drawScale(c);
  drawChecks(R, okImgs);
  drawViewer();
  $("#saveTxt").onclick = () => saveFile("skewcal_report.txt", R.report + "\n");
  $("#saveJson").onclick = () => saveFile("skewcal_report.json", R.report_json);
  $("#goCorrect").onclick = () => { $("#corAngle").value = c.theta.toFixed(4); showTab("correct"); };
  for (const b of $$("[data-kind]", root)) b.onclick = () => { state.view.kind = b.dataset.kind; renderResults(); };
  for (const b of $$("[data-img]", root)) b.onclick = () => { state.view.image = Number(b.dataset.img); renderResults(); };
}

function imageErrors(images) {
  const bad = (images || []).filter((i) => !i.ok);
  if (!bad.length) return "";
  return `<div class="errors stack" style="margin-top:12px;gap:4px">${bad.map((i) => `<div><strong>${esc(i.name)}:</strong> ${esc(friendlyError(i.error || "failed"))}</div>`).join("")}</div>`;
}

function imageTable(R) {
  const photo = R.mode === "photo";
  const rows = R.images.filter((i) => i.ok).map((im) => {
    const r = im.result;
    const warp = r.warp_corners_mm ? Math.max(...r.warp_corners_mm.map(Math.abs)) : null;
    return `<tr><td class="name" title="${esc(im.name)}">${esc(im.name)}</td><td>${fx(r.theta, 4)}°</td><td>±${fx(r.theta_std, 4)}</td>
      <td>${fx(100 * r.scale_x, 3)}%</td><td>${fx(100 * r.scale_y, 3)}%</td><td>${r.n_holes}/${r.n_expected}</td><td>${fx(r.rms_um, 0)} µm</td>
      <td>${relPos(r.rotation_deg, R.images.find((i) => i.ok).result.rotation_deg)}°${r.mirrored ? ", face down" : ""}</td>
      ${photo ? `<td>${fx(r.ref_rms_um, 0)} µm</td><td>${warp == null ? "–" : fx(warp, 1) + " mm"}</td><td>${fx(r.camera_distance_mm, 0)} mm</td>` : ""}</tr>`;
  }).join("");
  return `<table><thead><tr><th>Image</th><th>XY angle</th><th>±</th><th>Scale X</th><th>Scale Y</th><th>Holes</th><th>Fit rms</th><th>Position</th>
    ${photo ? "<th>Sheet fit</th><th>Plate bow</th><th>Distance</th>" : ""}</tr></thead><tbody>${rows}</tbody></table>`;
}

// --- skew diagram: the measured frame, skew exaggerated so it is visible
function drawSkew(theta) {
  const skew = theta - 90;
  const a = Math.abs(skew);
  const k = Math.max(1, 7 / Math.max(a, 0.05));
  const phi = ((90 + k * skew) * Math.PI) / 180;
  const W = 340, H = 270, ox = 70, oy = 228, L = 160;
  const P = (x, y) => `${(ox + x).toFixed(1)},${(oy - y).toFixed(1)}`;
  const yx = L * Math.cos(phi), yy = L * Math.sin(phi);
  const arcR = 36;
  const arc = `M ${P(arcR, 0)} A ${arcR} ${arcR} 0 0 0 ${P(arcR * Math.cos(phi), arcR * Math.sin(phi))}`;
  const mid = phi / 2;
  $("#exag").textContent = k > 1.5 ? `skew drawn ×${Math.round(k)}` : "drawn to scale";
  $("#skewDiagram").innerHTML = `
  <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Measured XY frame compared with a perfect square">
    <defs><marker id="arrM" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="var(--mark)"/></marker></defs>
    <polygon points="${P(0, 0)} ${P(L, 0)} ${P(L, L)} ${P(0, L)}" fill="none" stroke="var(--muted)" stroke-width="1" stroke-dasharray="4 4"/>
    <polygon points="${P(0, 0)} ${P(L, 0)} ${P(L + yx, yy)} ${P(yx, yy)}" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="2"/>
    <line x1="${ox}" y1="${oy}" x2="${(ox + L + 22).toFixed(1)}" y2="${oy}" stroke="var(--mark)" stroke-width="2" marker-end="url(#arrM)"/>
    <line x1="${ox}" y1="${oy}" x2="${(ox + (L + 22) * Math.cos(phi)).toFixed(1)}" y2="${(oy - (L + 22) * Math.sin(phi)).toFixed(1)}" stroke="var(--mark)" stroke-width="2" marker-end="url(#arrM)"/>
    <text x="${ox + L + 28}" y="${oy + 4}" class="t-mark" style="font-weight:600;font-size:13px">X</text>
    <text x="${(ox + (L + 30) * Math.cos(phi)).toFixed(1)}" y="${(oy - (L + 30) * Math.sin(phi)).toFixed(1)}" class="t-mark" text-anchor="middle" style="font-weight:600;font-size:13px">Y</text>
    <path d="${arc}" fill="none" stroke="var(--ink)" stroke-width="1.2"/>
    <text x="${(ox + (arcR + 10) * Math.cos(mid)).toFixed(1)}" y="${(oy - (arcR + 10) * Math.sin(mid) + 4).toFixed(1)}" class="t-ink" style="font-size:12px">${fx(theta, 3)}°</text>
    <text x="${ox + L / 2}" y="${oy + 26}" text-anchor="middle">dashed: perfect 90°</text>
    <circle cx="${ox}" cy="${oy}" r="3" fill="var(--ink)"/>
  </svg>`;
}

// --- per-image angles with ±1σ and the combined result
function niceTicks(lo, hi, n = 5) {
  const span = hi - lo;
  const step0 = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-12; v += step) out.push(+v.toFixed(6));
  return { ticks: out, step };
}
function drawReversal(imgs, c) {
  const W = 320, H = 210, ml = 58, mr = 10, mt = 12, mb = 30;
  const vals = imgs.map((i) => i.result.theta);
  const errs = imgs.map((i) => i.result.theta_std);
  let lo = Math.min(c.theta - c.theta_std, ...vals.map((v, i) => v - errs[i]));
  let hi = Math.max(c.theta + c.theta_std, ...vals.map((v, i) => v + errs[i]));
  const minSpan = 0.03;
  if (hi - lo < minSpan) { const m = (hi + lo) / 2; lo = m - minSpan / 2; hi = m + minSpan / 2; }
  const pad = (hi - lo) * 0.15; lo -= pad; hi += pad;
  const y = (v) => mt + ((hi - v) / (hi - lo)) * (H - mt - mb);
  const n = imgs.length;
  const x = (i) => ml + ((i + 0.5) / n) * (W - ml - mr);
  const { ticks, step } = niceTicks(lo, hi, 5);
  const dec = Math.max(0, Math.min(4, -Math.floor(Math.log10(step)) + (step / Math.pow(10, Math.floor(Math.log10(step))) === 2.5 ? 1 : 0)));
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="XY angle measured in each image">`;
  for (const t of ticks) s += `<line x1="${ml}" x2="${W - mr}" y1="${y(t)}" y2="${y(t)}" stroke="var(--rule)" stroke-width="1"/><text x="${ml - 8}" y="${y(t) + 4}" text-anchor="end">${t.toFixed(dec)}°</text>`;
  if (90 > lo && 90 < hi) s += `<line x1="${ml}" x2="${W - mr}" y1="${y(90)}" y2="${y(90)}" stroke="var(--ok)" stroke-dasharray="2 4" stroke-width="1.5"/><text x="${W - mr}" y="${y(90) - 5}" text-anchor="end" style="fill:var(--ok)">square</text>`;
  s += `<rect x="${ml}" width="${W - ml - mr}" y="${y(c.theta + c.theta_std)}" height="${Math.max(1, y(c.theta - c.theta_std) - y(c.theta + c.theta_std))}" fill="var(--accent-soft)"/>`;
  s += `<line x1="${ml}" x2="${W - mr}" y1="${y(c.theta)}" y2="${y(c.theta)}" stroke="var(--ink)" stroke-width="2"/>`;
  imgs.forEach((im, i) => {
    const col = im.result.plate_x_along_frame_x ? "var(--accent)" : "var(--mark)";
    const v = vals[i], e = errs[i];
    s += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(v + e)}" y2="${y(v - e)}" stroke="${col}" stroke-width="2"/>`;
    s += `<line x1="${x(i) - 6}" x2="${x(i) + 6}" y1="${y(v + e)}" y2="${y(v + e)}" stroke="${col}" stroke-width="2"/><line x1="${x(i) - 6}" x2="${x(i) + 6}" y1="${y(v - e)}" y2="${y(v - e)}" stroke="${col}" stroke-width="2"/>`;
    s += `<circle cx="${x(i)}" cy="${y(v)}" r="5.5" fill="${col}" stroke="var(--surface)" stroke-width="2"><title>${esc(im.name)}: ${fx(v, 4)}° ± ${fx(e, 4)}</title></circle>`;
    const room = Math.floor((W - ml - mr) / n / 7);
    const lbl = im.name.length > room ? im.name.slice(0, Math.max(3, room - 4)) + "…" : im.name;
    s += `<text x="${x(i)}" y="${H - mb + 18}" text-anchor="middle">${esc(lbl)}</text>`;
  });
  s += "</svg>";
  $("#reversalChart").innerHTML = s;
}

// --- scale bars around 100 %
function drawScale(c) {
  const devs = [c.scale_x - 1, c.scale_y - 1].map((d) => d * 100);
  const R = Math.max(0.5, Math.ceil(Math.max(...devs.map(Math.abs)) * 1.25 * 4) / 4);
  const W = 320, rowH = 40, H = rowH * 2 + 26, ml = 22, mr = 78;
  const x = (d) => ml + ((d + R) / (2 * R)) * (W - ml - mr);
  let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="X and Y scale compared with 100 percent">`;
  for (const t of [-R, -R / 2, 0, R / 2, R]) {
    s += `<line x1="${x(t)}" x2="${x(t)}" y1="8" y2="${H - 20}" stroke="${t === 0 ? "var(--muted)" : "var(--rule)"}" stroke-width="1"/>`;
    s += `<text x="${x(t)}" y="${H - 4}" text-anchor="middle">${t === 0 ? "100%" : (t > 0 ? "+" : "−") + Math.abs(t) + "%"}</text>`;
  }
  ["X", "Y"].forEach((ax, i) => {
    const d = devs[i], yc = 14 + i * rowH + 12;
    s += `<text x="4" y="${yc + 5}" class="t-ink" style="font-weight:600;font-size:13px">${ax}</text>`;
    s += `<rect x="${Math.min(x(0), x(d))}" y="${yc - 7}" width="${Math.max(2, Math.abs(x(d) - x(0)))}" height="14" rx="3" fill="var(--accent)"/>`;
    s += `<text x="${W - mr + 10}" y="${yc + 5}" class="t-ink" style="font-size:12px">${fx(100 + d, 3)}%</text>`;
  });
  s += "</svg>";
  $("#scaleBars").innerHTML = s;
}

// --- quality checks
function drawChecks(R, imgs) {
  const items = [];
  const add = (level, what, why) => items.push({ level, what, why });
  const c = R.combined;
  add(c.reversal ? "ok" : "warn", c.reversal ? "Plate turned 90° between images" : "All images in one position",
    c.reversal ? "The scanner's or sheet's own squareness error was measured and removed." : "Add an image with the plate turned 90°. Until then the result includes the scanner's error, often ±0.05°.");
  const na = imgs.filter((i) => i.result.frame_sign > 0).length, nb = imgs.length - na;
  if (c.reversal) {
    add(imgs.length >= 4 && na >= 2 && nb >= 2 ? "ok" : "info", `${imgs.length} images: ${na} at 0°/180°, ${nb} at 90°/270°`,
      imgs.length >= 4 && na >= 2 && nb >= 2 ? "Enough images for the scatter between them to set the uncertainty."
        : "Two more images, with the plate turned another 90° each time, average out image-to-image noise and give an honest uncertainty.");
  }
  const minHoles = Math.min(...imgs.map((i) => i.result.n_holes / i.result.n_expected));
  add(minHoles >= 0.95 ? "ok" : minHoles >= 0.8 ? "warn" : "bad", `${Math.round(minHoles * 81)} of 81 holes used (worst image)`,
    minHoles >= 0.95 ? "Every hole was found and fits the grid." : "Some holes were missing or rejected as outliers. Check for dust, flash or the plate edge leaving the image.");
  const rms = Math.max(...imgs.map((i) => i.result.rms_um));
  add(rms < 80 ? "ok" : rms < 150 ? "warn" : "bad", `Fit residual ${fx(rms, 0)} µm`,
    rms < 80 ? "Holes sit where a straight, evenly scaled grid puts them." : "Holes scatter more than usual. A blurry image, a bent plate or poor first layer can cause this.");
  if (R.mode === "photo") {
    const screen = imgs.every((i) => i.result.reference === "screen");
    const ref = Math.max(...imgs.map((i) => i.result.ref_rms_um || 0));
    add(ref < 100 ? "ok" : ref < 200 ? "warn" : "bad", `${screen ? "Screen" : "Reference sheet"} fit ${fx(ref, 0)} µm`,
      ref < 100 ? `The ${screen ? "screen's" : "sheet's"} markers fit a flat plane.` : "The reference looks uneven or the photo is soft. Use the 2× lens from 40–50 cm and focus on the plate.");
    const bowed = imgs.filter((i) => i.result.warp_corners_mm);
    if (bowed.length) {
      const warp = Math.max(...bowed.map((i) => Math.max(...i.result.warp_corners_mm.map(Math.abs))));
      const used = bowed.some((i) => i.result.warp_used);
      add(!used ? "ok" : warp < 1.5 ? "warn" : "bad", used ? `Plate bow ${fx(warp, 1)} mm at the corners, corrected` : `Plate flat (fitted bow ${fx(warp, 1)} mm)`,
        !used ? "No significant bow, so the bow correction was left off for a more precise result."
          : "Either the plate is bowed or the lens distorts (common with the 1× lens up close). Scale is less certain. Use the 2× lens from 40–50 cm, and check the plate on a flat surface.");
    }
    const foc = imgs.map((i) => i.result.focal_src);
    add(foc.every((f) => f === "EXIF") ? "ok" : "warn", foc.every((f) => f === "EXIF") ? "Focal length read from the photos" : "Focal length estimated",
      foc.every((f) => f === "EXIF") ? "Camera distance is known, so plate thickness is corrected exactly." : "Use the original photo files so the focal length is available.");
  }
  if (imgs.length > 1) {
    const spread = Math.max(...imgs.map((i) => i.result.theta)) - Math.min(...imgs.map((i) => i.result.theta));
    const tol = c.reversal ? 0.15 : 0.06;
    add(spread < tol ? "ok" : "warn", `Images agree within ${fx(spread, 3)}°`,
      spread < tol ? (c.reversal ? "The spread is the scanner error, which the turn cancels." : "Repeat images give the same angle.") : "The images disagree more than expected. Re-scan the odd one out.");
  }
  const failed = R.images.filter((i) => !i.ok).length;
  if (failed) add("bad", `${failed} image${failed > 1 ? "s" : ""} could not be measured`, "See the messages under Per image.");
  const label = { ok: "Good", warn: "Check", bad: "Problem", info: "Tip" };
  $("#checks").innerHTML = items.map((it) => `<li><span class="chip ${it.level}">${label[it.level]}</span><div><div class="what">${esc(it.what)}</div><div class="why">${esc(it.why)}</div></div></li>`).join("");
}

// --- overlay image or residual quiver plot
function drawViewer() {
  const imgs = state.result.images.filter((i) => i.ok);
  const im = imgs[Math.min(state.view.image, imgs.length - 1)];
  const v = $("#viewer");
  if (state.view.kind === "overlay") {
    const src = state.overlays[im.name] || im.overlay;
    v.innerHTML = src
      ? `<div class="overlay" id="ovl"><img src="${esc(src)}" alt="Scan of ${esc(im.name)} with every detected hole circled and the fitted axes drawn"></div>
         <p class="note" style="margin-top:8px">Green circles: holes used. Blue arrows: each hole's distance from the fitted grid, enlarged 100×. Red arrows: the plate's X and Y axes. Click to zoom.</p>`
      : '<p class="note">No overlay image for this file.</p>';
    const o = $("#ovl");
    if (o) o.onclick = () => o.classList.toggle("zoom");
    return;
  }
  const h = im.result.holes;
  if (!h) { v.innerHTML = '<p class="note">No per-hole data for this image.</p>'; return; }
  const gain = state.view.gain;
  const W = 520, pad = 34, span = 100, edge = 7;
  const sc = (W - 2 * pad) / (span + 2 * edge);
  const X = (mm) => pad + (mm + edge) * sc;
  const Y = (mm) => W - pad - (mm + edge) * sc;
  let s = `<svg class="chart" viewBox="0 0 ${W} ${W + 14}" role="img" aria-label="Residual of each hole from the fitted grid">
    <defs><marker id="arrR" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker></defs>
    <rect x="${X(-edge)}" y="${Y(span + edge)}" width="${(span + 2 * edge) * sc}" height="${(span + 2 * edge) * sc}" rx="4" fill="var(--surface-2)" stroke="var(--rule)"/>`;
  const rms = [];
  h.nom.forEach((p, i) => {
    const [r0, r1] = h.res[i];
    const cx = X(p[0]), cy = Y(p[1]);
    if (h.keep[i]) {
      rms.push(r0 * r0 + r1 * r1);
      s += `<circle cx="${cx}" cy="${cy}" r="${2 * sc}" fill="var(--surface)" stroke="var(--muted)" stroke-opacity="0.5" stroke-width="1"/>`;
      const ex = cx + r0 * gain * sc, ey = cy - r1 * gain * sc;
      if (Math.hypot(ex - cx, ey - cy) > 1.5) s += `<line x1="${cx}" y1="${cy}" x2="${ex.toFixed(1)}" y2="${ey.toFixed(1)}" stroke="var(--accent)" stroke-width="1.6" marker-end="url(#arrR)"><title>${fx(Math.hypot(r0, r1) * 1000, 0)} µm</title></line>`;
      else s += `<circle cx="${cx}" cy="${cy}" r="1.6" fill="var(--accent)"/>`;
    } else {
      s += `<circle cx="${cx}" cy="${cy}" r="${2 * sc}" fill="var(--mark-soft)" stroke="var(--mark)" stroke-width="1.2"><title>Rejected as an outlier: ${fx(Math.hypot(r0, r1) * 1000, 0)} µm</title></circle>`;
    }
  });
  const bar = 0.05 * gain * sc;
  s += `<line x1="${X(-edge)}" y1="${W - 10}" x2="${X(-edge) + bar}" y2="${W - 10}" stroke="var(--accent)" stroke-width="2" marker-end="url(#arrR)"/>
    <text x="${X(-edge) + bar + 8}" y="${W - 6}">50 µm</text>
    <text x="${X(span + edge)}" y="${W - 6}" text-anchor="end">origin hole bottom left · X → · Y ↑</text></svg>`;
  const rmsUm = Math.sqrt(rms.reduce((a, b) => a + b, 0) / Math.max(1, rms.length)) * 1000;
  v.innerHTML = `<div class="row" style="justify-content:space-between;margin-bottom:6px">
      <span class="note">Plate seen from above, in its own axes. rms ${fx(rmsUm, 0)} µm. Red holes were rejected as outliers.</span>
      <label class="row note" for="gain">Arrows ×<span id="gainV" class="num">${gain}</span><input id="gain" type="range" min="50" max="500" step="10" value="${gain}"></label>
    </div><div style="max-width:560px;margin:0 auto">${s}</div>`;
  $("#gain").oninput = (e) => { state.view.gain = Number(e.target.value); drawViewer(); $("#gain").focus(); };
}

// ------------------------------------------------------------------ correct tab
function corAngle() {
  const v = parseFloat($("#corAngle").value);
  return isFinite(v) ? v : null;
}
function corCentre() {
  const sel = $("#corBed").value;
  const txt = sel === "custom" ? $("#corCustom").value : sel;
  const m = String(txt).split(",").map((s) => parseFloat(s));
  return m.length === 2 && m.every(isFinite) ? m : null;
}
function renderCorrect() {
  if (!$("#corAngle").value) {
    const own = store.get("last");
    const r = state.result && state.result.ok && !state.sample ? state.result.combined : null;
    const th = r ? r.theta : own ? own.theta : null;
    if (th) $("#corAngle").value = th.toFixed(4);
  }
  const src = state.result && state.result.ok && !state.sample ? "Filled in from your latest measurement."
    : store.get("last") ? "Filled in from your last measurement on this device." : "Measure your printer first, or type the XY angle you measured.";
  $("#corSource").textContent = src;
  updateCmd();
}
function updateCmd() {
  const a = corAngle();
  const c = corCentre();
  const angle = a == null ? "90.0000" : a.toFixed(4);
  const centre = c && !(c[0] === 90 && c[1] === 90) ? ` --center ${c[0]},${c[1]}` : "";
  $("#cmdLine").textContent = state.os === "mac"
    ? `/usr/bin/python3 "/path/to/skew_gcode.py" --xy-angle ${angle}${centre};`
    : `"C:\\Path\\To\\python.exe" "C:\\path\\to\\skew_gcode.py" --xy-angle ${angle}${centre};`;
}
$("#corAngle").addEventListener("input", updateCmd);
$("#corCustom").addEventListener("input", updateCmd);
$("#corBed").addEventListener("change", () => { $("#corCustomWrap").hidden = $("#corBed").value !== "custom"; updateCmd(); });
$("#osMac").addEventListener("click", () => { state.os = "mac"; $("#osMac").setAttribute("aria-pressed", "true"); $("#osWin").setAttribute("aria-pressed", "false"); updateCmd(); });
$("#osWin").addEventListener("click", () => { state.os = "win"; $("#osWin").setAttribute("aria-pressed", "true"); $("#osMac").setAttribute("aria-pressed", "false"); updateCmd(); });
$("#copyCmd").addEventListener("click", async () => {
  const text = $("#cmdLine").textContent;
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied");
  } catch {
    const r = document.createRange();
    r.selectNodeContents($("#cmdLine"));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
    toast("Press Ctrl+C or ⌘C to copy");
  }
});
$("#saveScript").addEventListener("click", () => IN_ARTIFACT ? saveAsset("assets/skew_gcode.zip", "skew_gcode.zip") : saveAsset("assets/skew_gcode.py", "skew_gcode.py"));
$("#gInput").addEventListener("change", (e) => { if (e.target.files[0]) correctGcode(e.target.files[0]); e.target.value = ""; });

async function correctGcode(file) {
  const a = corAngle(), c = corCentre();
  if (a == null || Math.abs(a - 90) > 3) return toast("Enter the measured XY angle first (between 87° and 93°).");
  if (!c) return toast("Enter the bed centre as x,y in millimetres.");
  $("#gNote").textContent = engine.stage === "ready" ? "Correcting…" : "Loading the engine, then correcting…";
  setProgress("#gProgress", 30);
  try {
    const buffer = await file.arrayBuffer();
    const head = new Uint8Array(buffer, 0, 4);
    if (String.fromCharCode(...head) === "GCDE") throw new Error("This is binary G-code. Turn off Supports binary G-code in PrusaSlicer's Printer Settings and export again.");
    const r = await engine.call("gcode", { name: file.name, buffer, angle: a, scale_x: 1, scale_y: 1, cx: c[0], cy: c[1] }, [buffer]);
    setProgress("#gProgress", 100);
    if (!r.data.ok) throw new Error(r.data.error);
    $("#gNote").textContent = `${r.data.message.replace(/^skew_gcode: /, "")}. Corrected about (${c[0]}, ${c[1]}) for an XY angle of ${a.toFixed(4)}°.`;
    await saveFile(r.data.zipName, new Blob([r.zip], { type: "application/zip" }));
  } catch (e) {
    $("#gNote").textContent = e.message;
  } finally {
    setTimeout(() => setProgress("#gProgress", null), 600);
  }
}

// ------------------------------------------------------------------ print / capture drawings
function plateSvg(rot = 0, size = 150, label = "") {
  const n = 9, pitch = 12.5, edge = 7, span = 100, total = span + 2 * edge;
  const s = size / total;
  let g = `<rect x="0" y="0" width="${size}" height="${size}" rx="${3 * s}" fill="var(--ink)"/>`;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    g += `<circle cx="${((edge + i * pitch) * s).toFixed(1)}" cy="${(size - (edge + j * pitch) * s).toFixed(1)}" r="${(2 * s).toFixed(2)}" fill="var(--surface)"/>`;
  }
  // marker holes: X marker between (0,0) and (1,0); Y pair between (0,0) and (0,1)
  g += `<circle cx="${((edge + 6.25) * s).toFixed(1)}" cy="${(size - edge * s).toFixed(1)}" r="${s}" fill="var(--mark)"/>`;
  for (const dy of [-1.75, 1.75]) g += `<circle cx="${(edge * s).toFixed(1)}" cy="${(size - (edge + 6.25 + dy) * s).toFixed(1)}" r="${s}" fill="var(--mark)"/>`;
  return `<g transform="rotate(${rot} ${size / 2} ${size / 2})">${g}</g>${label}`;
}
function drawGuides() {
  const S = 170;
  $("#plateDrawing").innerHTML = `<svg class="chart" viewBox="0 0 360 ${S + 40}" role="img" aria-label="Test plate, top view, origin at front left">
    <g transform="translate(70 10)">${plateSvg(0, S)}</g>
    <text x="64" y="${S + 32}" class="t-mark" style="font-size:12px">origin: front left of bed</text>
    <line x1="70" y1="${S + 16}" x2="${70 + S}" y2="${S + 16}" stroke="var(--muted)"/><text x="${70 + S + 6}" y="${S + 20}">X →</text>
    <text x="250" y="${S - 4}" style="font-size:11px">red: marker holes</text></svg>`;
  const s2 = 120;
  $("#turnDrawing").innerHTML = `<svg class="chart" viewBox="0 0 380 ${s2 + 46}" role="img" aria-label="Scan once, turn the plate 90 degrees, scan again">
    <defs><marker id="arrT" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker></defs>
    <g transform="translate(20 8)">${plateSvg(0, s2)}</g>
    <g transform="translate(240 8)">${plateSvg(90, s2)}</g>
    <path d="M 160 60 Q 190 30 222 60" fill="none" stroke="var(--accent)" stroke-width="2" marker-end="url(#arrT)"/>
    <text x="191" y="86" text-anchor="middle" class="t-accent" style="font-size:12px">turn 90°</text>
    <text x="80" y="${s2 + 34}" text-anchor="middle" class="t-ink">Scan 1</text>
    <text x="300" y="${s2 + 34}" text-anchor="middle" class="t-ink">Scan 2</text></svg>`;
}
let plateVersion = "standard";
function setPlateVersion(v) {
  plateVersion = v;
  $("#plateStd").setAttribute("aria-pressed", String(v === "standard"));
  $("#plateThin").setAttribute("aria-pressed", String(v === "thinrim"));
  $("#plateNote").textContent = v === "thinrim"
    ? "Holes are 4 mm only in the bottom 0.6 mm, then widen to 5.6 mm at the top. The visible hole edge is a thin first-layer rim, so lighting and viewing angle barely move it. Recommended for phone photos. Scan or photograph it first-layer side down, and choose Thin-rim when you measure."
    : "Straight 4 mm holes through the plate. Works well with a flatbed scanner.";
  $("#optPlate").value = v;
}
$("#plateStd").addEventListener("click", () => setPlateVersion("standard"));
$("#plateThin").addEventListener("click", () => setPlateVersion("thinrim"));
$("#saveStl").addEventListener("click", () => {
  const base = plateVersion === "thinrim" ? "skewcal_plate_thinrim" : "skewcal_plate";
  IN_ARTIFACT ? saveAsset(`assets/${base}.zip`, `${base}.zip`) : saveAsset(`assets/${base}.stl`, `${base}.stl`);
});
if (!IN_ARTIFACT) $("#stlNote").textContent = "114 × 114 × 1.6 mm, print as exported.";
$("#sheetButtons").addEventListener("click", (e) => {
  const b = e.target.closest("[data-sheet]");
  if (b) saveAsset(`assets/skewcal_reference_${b.dataset.sheet}.pdf`, `skewcal_reference_${b.dataset.sheet}.pdf`);
});

// ------------------------------------------------------------------ tablet-screen reference
const TABLETS = [
  { name: "iPad Pro 13-inch (M4)", px: [2064, 2752], ppi: 264 },
  { name: "iPad Pro 12.9-inch, iPad Air 13-inch", px: [2048, 2732], ppi: 264 },
  { name: "iPad Pro 11-inch (M4)", px: [1668, 2420], ppi: 264 },
  // Apple quotes 264 ppi, rounded. The 1st-4th gen 11-inch is 264.8 (11.0-inch diagonal):
  // the 100 mm bar drawn at 264 measured 99.7 mm with calipers on a 3rd gen (MHQU3LL/A).
  { name: "iPad Pro 11-inch (1st–4th gen, 2018–2022)", px: [1668, 2388], ppi: 264.8, checked: true },
  { name: "iPad Air 11-inch / 10.9-inch, iPad 10th gen / A16", px: [1640, 2360], ppi: 264 },
  { name: "iPad 10.2-inch (7th–9th gen)", px: [1620, 2160], ppi: 264 },
  { name: "iPad mini (too small)", px: [1488, 2266], ppi: 326 },
];
let screenSpec = null;
function screenPx() {
  const d = window.devicePixelRatio || 1;
  return [Math.round(screen.width * d), Math.round(screen.height * d)].sort((a, b) => a - b);
}
function initTablet() {
  const sel = $("#tabModel");
  const px = screenPx();
  const hit = TABLETS.findIndex((t) => Math.abs(t.px[0] - px[0]) <= 2 && Math.abs(t.px[1] - px[1]) <= 2);
  sel.innerHTML = TABLETS.map((t, i) => `<option value="${i}">${esc(t.name)}${t.checked ? ", calibrated" : ""}${i === hit ? " (this device)" : ""}</option>`).join("")
    + '<option value="custom">Other screen: enter its pixels per inch</option>';
  sel.value = hit >= 0 ? String(hit) : "custom";
  const syncSel = () => { $("#tabPpiWrap").hidden = sel.value !== "custom"; };
  sel.onchange = syncSel; syncSel();
  if (hit < 0) $("#tabNote").textContent = `This screen: ${px[1]} × ${px[0]} pixels. Pick your model, or enter its ppi.`;
}
async function showScreenRef() {
  if (!screenSpec) {
    try { screenSpec = await (await fetch("screen_markers.json")).json(); } catch { return toast("Couldn't load the marker pattern."); }
  }
  const sel = $("#tabModel").value;
  const ppi = sel === "custom" ? parseFloat($("#tabPpi").value) : TABLETS[Number(sel)].ppi;
  if (!(ppi > 50)) return toast("Enter the screen's pixels per inch.");
  const px = screenPx();
  const needMm = 2 * (screenSpec.ring_mm + screenSpec.marker_mm / 2) + 4;
  const shortMm = (px[0] / ppi) * 25.4;
  if (shortMm < needMm) return toast(`This screen is ${shortMm.toFixed(0)} mm across; the pattern needs ${needMm.toFixed(0)} mm. Use a larger tablet or the paper sheet.`);
  const el = $("#screenRef");
  el.hidden = false;
  el.dataset.ppi = ppi;
  try { await (el.requestFullscreen || el.webkitRequestFullscreen).call(el); } catch { /* fills the window instead */ }
  try { screenRefLock = await navigator.wakeLock?.request("screen"); } catch { /* optional */ }
  drawScreenRef();
}
let screenRefLock = null;
function drawScreenRef() {
  const el = $("#screenRef");
  if (el.hidden || !screenSpec) return;
  const dpr = window.devicePixelRatio || 1;
  const W = Math.round(window.innerWidth * dpr), H = Math.round(window.innerHeight * dpr);
  const cv = $("#screenCanvas");
  cv.width = W; cv.height = H;
  cv.style.width = W / dpr + "px"; cv.style.height = H / dpr + "px";
  const ctx = cv.getContext("2d");
  const k = Number(el.dataset.ppi) / 25.4;              // device pixels per mm
  const cx = W / 2, cy = H / 2;
  const X = (mm) => cx + mm * k, Y = (mm) => cy - mm * k;
  const dark = $("#tabDark").checked;
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, W, H);
  const wh = screenSpec.window_half_mm;
  if (dark) { ctx.fillStyle = "#000"; ctx.fillRect(X(-wh), Y(wh), 2 * wh * k, 2 * wh * k); }
  ctx.fillStyle = "#000";
  const m = screenSpec.marker_mm, cell = m / 6;
  for (const mk of screenSpec.markers) {
    mk.bits.forEach((row, r) => [...row].forEach((b, c) => {
      if (b === "0") ctx.fillRect(X(mk.x - m / 2 + c * cell), Y(mk.y + m / 2 - r * cell), cell * k, cell * k);
    }));
  }
  // corner ticks for placing the plate, inside the window
  ctx.strokeStyle = dark ? "#fff" : "#000"; ctx.lineWidth = 0.3 * k;
  for (const sx of [-1, 1]) for (const sy of [-1, 1]) {
    const x0 = sx * (wh - 1), y0 = sy * (wh - 1);
    ctx.beginPath(); ctx.moveTo(X(x0 - sx * 6), Y(y0)); ctx.lineTo(X(x0), Y(y0)); ctx.lineTo(X(x0), Y(y0 - sy * 6)); ctx.stroke();
  }
  // 100 mm check bar in the margin along the long side
  ctx.strokeStyle = "#000"; ctx.fillStyle = "#000"; ctx.lineWidth = 0.25 * k;
  const off = screenSpec.ring_mm + m / 2 + 8;
  const portrait = H >= W;
  for (let i = 0; i <= 10; i++) {
    const t = -50 + 10 * i, len = i % 5 ? 1.5 : 3;
    ctx.beginPath();
    if (portrait) { ctx.moveTo(X(t), Y(-off)); ctx.lineTo(X(t), Y(-off - len)); } else { ctx.moveTo(X(off), Y(t)); ctx.lineTo(X(off + len), Y(t)); }
    ctx.stroke();
  }
  ctx.beginPath();
  if (portrait) { ctx.moveTo(X(-50), Y(-off)); ctx.lineTo(X(50), Y(-off)); } else { ctx.moveTo(X(off), Y(-50)); ctx.lineTo(X(off), Y(50)); }
  ctx.stroke();
  ctx.font = `${3 * k}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  if (portrait) ctx.fillText("100 mm", X(0), Y(-off - 8));
  const vv = window.visualViewport;
  if (vv && Math.abs(vv.scale - 1) > 0.01) toast("The page is zoomed. Zoom back out, or the scale will be wrong.");
}
async function hideScreenRef() {
  $("#screenRef").hidden = true;
  try { if (document.fullscreenElement) await document.exitFullscreen(); } catch { /* not fullscreen */ }
  try { await screenRefLock?.release(); } catch { /* already released */ }
}
$("#tabShow").addEventListener("click", showScreenRef);
$("#screenDone").addEventListener("click", hideScreenRef);
window.addEventListener("resize", drawScreenRef);
document.addEventListener("fullscreenchange", () => setTimeout(drawScreenRef, 100));

// ------------------------------------------------------------------ start
async function boot() {
  setMode(store.get("mode") === "photo" ? "photo" : "scan");
  setPlateVersion(store.get("plate") === "thinrim" ? "thinrim" : "standard");
  initTablet();
  drawGuides();
  renderFiles();
  loadSamples();
  try {
    const r = await (await fetch("sample_result.json")).json();
    if (!state.result) {
      state.result = r;
      state.sample = r.sample;
      renderResults();
    }
  } catch { /* the page still works without the example */ }
  const tab = store.get("tab");
  if (tab && $("#panel-" + tab)) showTab(tab);
}
boot();
