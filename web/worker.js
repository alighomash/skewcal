// Runs the skewcal Python package in Pyodide, off the main thread.
// Messages in:  {id, cmd, args}   Messages out: {id, ok, result | error} and {type: "status", stage}
const BASE = new URL("./", self.location).href;
const PYODIDE_CDN = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";   // keep in step with tools/build_web.py
importScripts(BASE + "py_bundle.js");

let py = null;
let bridge = null;
let scipy = false;
let starting = null;

function status(stage, detail) {
  self.postMessage({ type: "status", stage, detail });
}

function safeName(name) {
  return name.replace(/[^\w.-]+/g, "_");
}

async function init() {
  if (bridge) return;
  if (starting) return starting;
  starting = (async () => {
    status("runtime");
    // A local copy (tools/build_web.py) is used when present; static hosts can use the CDN.
    let indexURL = BASE + "pyodide/";
    try {
      importScripts(indexURL + "pyodide.js");
    } catch {
      indexURL = PYODIDE_CDN;
      importScripts(indexURL + "pyodide.js");
    }
    py = await loadPyodide({ indexURL, stdout: () => {}, stderr: () => {} });
    status("packages");
    await py.loadPackage(["numpy", "opencv-python", "pillow"], { messageCallback: () => {} });
    const root = "/home/pyodide/";
    for (const [path, src] of Object.entries(self.SKEWCAL_PY)) {
      const parts = path.split("/");
      for (let i = 1; i < parts.length; i++) {
        const dir = root + parts.slice(0, i).join("/");
        if (!py.FS.analyzePath(dir).exists) py.FS.mkdir(dir);
      }
      py.FS.writeFile(root + path, src);
    }
    for (const dir of ["/in", "/tmp/out", "/gcode"]) {
      if (!py.FS.analyzePath(dir).exists) py.FS.mkdirTree(dir);
    }
    py.runPython(`import sys; sys.path.insert(0, "${root}")`);
    bridge = py.pyimport("bridge");
    status("ready");
  })();
  try {
    await starting;
  } catch (e) {
    starting = null;
    status("failed", String(e && e.message || e));
    throw e;
  }
}

async function needScipy() {
  if (scipy) return;
  status("scipy");
  await py.loadPackage(["scipy"], { messageCallback: () => {} });
  scipy = true;
  status("ready");
}

const commands = {
  async init() {
    await init();
    return true;
  },

  async reset() {
    await init();
    bridge.reset();
    return true;
  },

  // args: {mode, name, buffer, gray: {width, height} | null, opts}
  async analyze_one({ mode, name, buffer, gray, opts }) {
    await init();
    if (mode === "photo") await needScipy();
    const stem = safeName(name);
    let path = "/in/" + stem;
    if (gray) {
      const raw = path + ".gray";
      py.FS.writeFile(raw, new Uint8Array(buffer));
      path = path.replace(/\.[^.]*$/, "") + ".png";
      bridge.write_gray_png(path, raw, gray.width, gray.height);
      py.FS.unlink(raw);
    } else {
      py.FS.writeFile(path, new Uint8Array(buffer));
    }
    const out = JSON.parse(bridge.analyze_one(mode, path, stem, JSON.stringify(opts || {})));
    py.FS.unlink(path);
    out.name = name;
    let overlay = null;
    if (out.ok && py.FS.analyzePath(out.overlay_path).exists) {
      overlay = py.FS.readFile(out.overlay_path);
    }
    return { data: out, overlay, transfer: overlay ? [overlay.buffer] : [] };
  },

  async combine({ mode, names, plane }) {
    await init();
    const out = JSON.parse(bridge.combine_all(mode, JSON.stringify({ plane: plane || "xy" })));
    // restore the original file names (the filesystem copies use safe names)
    const map = new Map(names.map((n) => [safeName(n), n]));
    for (const im of out.images || []) im.name = map.get(im.name) || im.name;
    return { data: out };
  },

  // args: {name, buffer, angle, scale_x, scale_y, cx, cy, xz, yz}
  async gcode({ name, buffer, angle, scale_x, scale_y, cx, cy, xz = 90, yz = 90 }) {
    await init();
    const stem = safeName(name).replace(/\.(gcode|gco|g)$/i, "");
    const src = "/gcode/in.gcode";
    const outName = stem + "_skewcorrected.gcode";
    const dst = "/gcode/" + outName;
    py.FS.writeFile(src, new Uint8Array(buffer));
    const res = JSON.parse(bridge.correct_gcode(src, dst, angle, scale_x, scale_y, cx, cy, xz, yz));
    let zip = null;
    if (res.ok) {
      const zipPath = "/gcode/" + stem + "_skewcorrected.zip";
      bridge.make_zip(zipPath, JSON.stringify([[dst, outName]]));
      zip = py.FS.readFile(zipPath);
      py.FS.unlink(zipPath);
      py.FS.unlink(dst);
      res.zipName = stem + "_skewcorrected.zip";
      res.gcodeName = outName;
    }
    py.FS.unlink(src);
    return { data: res, zip, transfer: zip ? [zip.buffer] : [] };
  },
};

self.onmessage = async (ev) => {
  const { id, cmd, args } = ev.data;
  try {
    const r = await commands[cmd](args || {});
    const transfer = (r && r.transfer) || [];
    if (r && r.transfer) delete r.transfer;
    self.postMessage({ id, ok: true, result: r }, transfer);
  } catch (e) {
    self.postMessage({ id, ok: false, error: String((e && e.message) || e) });
  }
};
