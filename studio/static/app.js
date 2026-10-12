/* Media Studio front-end. Vanilla JS, no build step, no external requests. */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === false || v == null) continue;
      if (k === "class") n.className = v;
      else if (k === "on") for (const [ev, fn] of Object.entries(v)) n.addEventListener(ev, fn);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat()) if (k != null && k !== false) n.append(k.nodeType ? k : document.createTextNode(String(k)));
    return n;
  };

  const S = {
    caps: null,
    mode: "image",
    jobs: [],
    view: null,                 // {kind:'job',id,index} | {kind:'src',rec}
    inputs: { "vid-start": [], "edit-img": [], "edit-vid": [] },
    mine: new Set(),
    compare: false,
    imgPreset: 0,
    vidPreset: 0,
    artAssets: {},              // "jobId:index" -> uploaded asset record
    sig: { stage: "", strip: "", activity: "" },
    timer: null,
  };

  const KIND = { image: "Image", video: "Video", image_adjust: "Adjust", video_edit: "Edit" };

  /* ------------------------------------------------------------ helpers */
  async function api(path, opts = {}) {
    const res = await fetch(path, { credentials: "same-origin", ...opts });
    let data = null;
    try { data = await res.json(); } catch { /* no body */ }
    if (res.status === 401) { showLogin(); throw new Error("Sign in to continue."); }
    if (!res.ok) {
      let msg = res.statusText || "Request failed";
      const d = data && data.detail;
      if (typeof d === "string") msg = d;
      else if (Array.isArray(d)) msg = d.map((x) => `${(x.loc || []).slice(-1)[0] || "value"}: ${x.msg}`).join("; ");
      throw new Error(msg);
    }
    return data;
  }
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg; t.classList.add("show");
    clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), 2800);
  }
  function notice(msg) {
    const n = $("#notice");
    n.hidden = !msg; n.textContent = msg || "";
    clearTimeout(notice.t);
    if (msg) notice.t = setTimeout(() => (n.hidden = true), 9000);
  }
  const fmtTime = (s) => {
    if (s == null || !isFinite(s)) return "0:00.0";
    const m = Math.floor(s / 60);
    return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
  };
  const fmtMB = (b) => (b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`);
  const num = (id) => { const v = $(id).value; return v === "" ? null : Number(v); };
  const short = (t, n = 60) => (t.length > n ? t.slice(0, n - 1) + "…" : t);
  const isActive = (j) => j.status === "queued" || j.status === "running";

  function radioGroup(root, onChange) {
    root.addEventListener("click", (e) => {
      const b = e.target.closest("button[role=radio]");
      if (!b || !root.contains(b)) return;
      $$("button[role=radio]", root).forEach((x) => x.setAttribute("aria-checked", String(x === b)));
      onChange && onChange(b.dataset.v);
    });
    return () => ($("button[aria-checked=true]", root) || {}).dataset?.v;
  }

  /* ------------------------------------------------------------ mode tabs */
  const GO = { image: "#imgGo", video: "#vidGo", "edit-image": "#editGo", "edit-video": "#vtGo" };
  function setMode(m) {
    S.mode = m;
    $$(".modes button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === m)));
    $$(".panel").forEach((p) => (p.hidden = p.dataset.panel !== m));
    if (m === "edit-video" && S.inputs["edit-vid"][0] && (!S.view || S.view.kind === "src")) showSource(S.inputs["edit-vid"][0]);
    if (m === "edit-image" && S.inputs["edit-img"][0] && (!S.view || S.view.kind === "src")) showSource(S.inputs["edit-img"][0]);
    renderStage(true);
  }
  $$(".modes button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

  /* ------------------------------------------------------------ uploads & drop zones */
  async function uploadFile(file) {
    const fd = new FormData();
    fd.append("file", file, file.name);
    return api("/v1/assets", { method: "POST", body: fd });
  }
  const kindOfFile = (f) => {
    if (f.type.startsWith("image/") || /\.(png|jpe?g|webp|heic|heif)$/i.test(f.name)) return "image";
    if (f.type.startsWith("video/") || /\.(mp4|mov|m4v|webm|mkv)$/i.test(f.name)) return "video";
    return null;
  };
  const ACCEPT = { image: ".png,.jpg,.jpeg,.webp,.heic,.heif,image/*", video: ".mp4,.mov,.m4v,.webm,.mkv,video/*" };

  function renderDrop(zone) {
    const key = zone.dataset.drop, max = +zone.dataset.max, kind = zone.dataset.accept;
    const items = S.inputs[key];
    zone.replaceChildren();
    if (!items.length) {
      zone.append(el("div", { class: "drop-empty" }, `Drop ${kind === "video" ? "a video" : max > 1 ? "images" : "an image"} here or click to choose`));
      return;
    }
    const row = el("div", { class: "drop-items" });
    items.forEach((rec, i) => {
      row.append(el("div", { class: "drop-item", title: rec.filename || "" },
        el("img", { src: rec.thumb_url, alt: rec.filename || "input" }),
        max > 1 && el("b", {}, `Image ${i + 1}`),
        kind === "video" && rec.duration != null && el("b", {}, fmtTime(rec.duration)),
        el("button", { type: "button", "aria-label": "Remove", on: { click: (e) => { e.stopPropagation(); S.inputs[key].splice(i, 1); renderDrop(zone); onInputsChanged(key); } } }, "×")));
    });
    if (items.length < max) row.append(el("div", { class: "drop-add", "aria-hidden": "true" }, "+"));
    zone.append(row);
    const first = items[0];
    if (first && first.width) zone.append(el("div", { class: "drop-meta" }, `${first.width}×${first.height}${first.duration != null ? " · " + fmtTime(first.duration) : ""}${first.bytes ? " · " + fmtMB(first.bytes) : ""}`));
  }

  function onInputsChanged(key) {
    if (key === "edit-vid" || key === "edit-img") {
      const rec = S.inputs[key][0];
      if (rec) showSource(rec); else if (S.view && S.view.kind === "src") { S.view = null; renderStage(true); }
    }
  }

  async function addFiles(zone, files) {
    const key = zone.dataset.drop, max = +zone.dataset.max, kind = zone.dataset.accept;
    const list = [...files].filter((f) => kindOfFile(f) === kind);
    if (!list.length) { notice(`That file isn't ${kind === "video" ? "a supported video (MP4, MOV, WebM, MKV)" : "a supported image (PNG, JPEG, WebP, HEIC)"}.`); return; }
    zone.classList.add("over");
    try {
      for (const f of list.slice(0, max)) {
        toast(`Uploading ${short(f.name, 30)}…`);
        const rec = await uploadFile(f);
        if (max === 1) S.inputs[key] = [rec];
        else if (S.inputs[key].length < max) S.inputs[key].push(rec);
      }
      renderDrop(zone); onInputsChanged(key);
    } catch (e) { notice(e.message); }
    finally { zone.classList.remove("over"); }
  }

  const picker = $("#filePick");
  function pickFor(zone) {
    picker.value = "";
    picker.accept = ACCEPT[zone.dataset.accept];
    picker.multiple = +zone.dataset.max > 1;
    picker.onchange = () => picker.files.length && addFiles(zone, picker.files);
    picker.click();
  }
  $$(".drop").forEach((zone) => {
    renderDrop(zone);
    zone.addEventListener("click", (e) => { if (!e.target.closest("button")) pickFor(zone); });
    zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickFor(zone); } });
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("over"));
    zone.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); zone.classList.remove("over"); addFiles(zone, e.dataTransfer.files); });
  });
  const zoneFor = (key) => $(`.drop[data-drop="${key}"]`);

  // Dropping anywhere on the stage routes by file type.
  const stage = $("#stage");
  stage.addEventListener("dragover", (e) => { e.preventDefault(); stage.classList.add("over"); });
  stage.addEventListener("dragleave", (e) => { if (e.target === stage) stage.classList.remove("over"); });
  stage.addEventListener("drop", async (e) => {
    e.preventDefault(); stage.classList.remove("over");
    const f = [...e.dataTransfer.files][0];
    if (!f) return;
    const k = kindOfFile(f);
    if (!k) return notice("Drop an image (PNG, JPEG, WebP, HEIC) or a video (MP4, MOV, WebM, MKV).");
    if (k === "video") { setMode("edit-video"); addFiles(zoneFor("edit-vid"), [f]); }
    else if (S.mode === "video") addFiles(zoneFor("vid-start"), [f]);
    else { setMode("edit-image"); addFiles(zoneFor("edit-img"), [f]); }
  });

  /* Convert a generated output into an uploaded asset (needed for AI edits / start frames). */
  async function ensureAsset(rec) {
    if (rec.asset_id) return rec.asset_id;
    const key = `${rec.job_id}:${rec.index || 0}`;
    if (S.artAssets[key]) return S.artAssets[key].asset_id;
    const blob = await (await fetch(rec.url, { credentials: "same-origin" })).blob();
    const ext = (blob.type.split("/")[1] || "png").replace("jpeg", "jpg");
    S.artAssets[key] = await uploadFile(new File([blob], `generated.${ext}`, { type: blob.type }));
    return S.artAssets[key].asset_id;
  }
  const srcBody = (rec) => (rec.asset_id ? { source_asset_id: rec.asset_id } : { source_job_id: rec.job_id, source_index: rec.index || 0 });

  /* ------------------------------------------------------------ capabilities / status */
  function presetChips(root, presets, current, onPick) {
    root.replaceChildren(...presets.map((p, i) =>
      el("button", { type: "button", role: "radio", "aria-checked": String(i === current), on: { click: () => {
        $$("button", root).forEach((b, j) => b.setAttribute("aria-checked", String(i === j))); onPick(i);
      } } }, p.label, el("small", {}, `${p.width}×${p.height}`))));
  }

  function applyCaps(c) {
    S.caps = c;
    const chip = $("#statusChip");
    if (c.mode === "demo") { chip.textContent = "Demo mode · placeholder media"; chip.classList.add("demo"); }
    else { chip.textContent = `${c.image.label} · ${c.video.label} · ${c.hardware.device.toUpperCase()}`; chip.classList.remove("demo"); }
    presetChips($("#imgPresets"), c.image.presets, S.imgPreset, (i) => (S.imgPreset = i));
    presetChips($("#vidPresets"), c.video.presets, S.vidPreset, (i) => { S.vidPreset = i; updateVidInfo(); });
    $("#imgSteps").value = c.image.default_steps;
    $("#editSteps").value = c.image.default_steps;
    $("#vidSteps").placeholder = `Default (${c.video.default_steps})`;
    $("#vidAudioWrap").hidden = !c.video.supports.audio;
    $("#vidNeg").closest(".field").hidden = !c.video.supports.negative_prompt;
    const fps = +$("#vidFps").value;
    const maxS = Math.min(10, Math.floor((c.video.max_frames - 1) / fps));
    $("#vidLen").max = maxS;
    $("#vidLen").value = Math.min(maxS, Math.max(1, Math.round(((c.video.default_frames - 1) / fps) * 2) / 2));
    updateVidInfo();
    $("#editGo").disabled = !c.image.supports.edit;
  }

  function updateVidInfo() {
    if (!S.caps) return;
    const fps = +$("#vidFps").value, sec = +$("#vidLen").value;
    const frames = Math.min(S.caps.video.max_frames, Math.max(9, Math.round((sec * fps) / 8) * 8 + 1));
    const p = S.caps.video.presets[S.vidPreset];
    $("#vidLenOut").textContent = `${sec} s`;
    const long = frames > S.caps.video.default_frames;
    $("#vidFrames").textContent = `${frames} frames · ${p.width}×${p.height}${long ? " · longer clips need more memory and time" : ""}`;
    return frames;
  }
  $("#vidLen").addEventListener("input", updateVidInfo);
  $("#vidFps").addEventListener("change", () => {
    if (!S.caps) return;
    const fps = +$("#vidFps").value, maxS = Math.min(10, Math.floor((S.caps.video.max_frames - 1) / fps));
    $("#vidLen").max = maxS; if (+$("#vidLen").value > maxS) $("#vidLen").value = maxS;
    updateVidInfo();
  });

  function renderAbout() {
    const c = S.caps, dl = $("#aboutList");
    if (!c) { dl.replaceChildren(); return; }
    const rows = [
      ["Mode", c.mode === "demo" ? "Demo (placeholder media, no AI models loaded)" : "AI models"],
      ["Image model", c.image.label + (c.image.loaded ? " (loaded)" : "")],
      ["Video model", c.video.label + (c.video.loaded ? " (loaded)" : "") + (c.video.experimental ? " – experimental" : "")],
      ["Device", `${c.hardware.device_name} · ${c.hardware.memory_gb} GB`],
      ["Hardware tier", c.tier],
      ["Queue", `${c.queue.heavy || 0} generating · ${c.queue.light || 0} editing`],
      ["Access", c.auth_required ? "Token required" : "Open (this computer only)"],
    ];
    dl.replaceChildren(...rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
  }
  $("#statusChip").addEventListener("click", async () => {
    try { S.caps = await api("/v1/capabilities"); } catch { /* shows login */ }
    renderAbout(); $("#loginBox").hidden = true; $("#settings").showModal();
  });
  function showLogin() {
    $("#aboutList").replaceChildren();
    $("#loginBox").hidden = false;
    if (!$("#settings").open) $("#settings").showModal();
  }
  $("#loginGo").addEventListener("click", async () => {
    const err = $("#loginErr"); err.hidden = true;
    try {
      await post("/v1/session", { token: $("#tokenInput").value });
      $("#settings").close(); $("#tokenInput").value = "";
      boot();
    } catch (e) { err.textContent = "That token wasn't accepted."; err.hidden = false; }
  });
  $("#tokenInput").addEventListener("keydown", (e) => e.key === "Enter" && $("#loginGo").click());

  /* ------------------------------------------------------------ submitting jobs */
  const radiosImgCount = radioGroup($("#imgCount"));
  const radiosEditCount = radioGroup($("#editCount"));
  const radiosOut = radioGroup($("#vtOutput"), () => updateOutHint());

  function updateOutHint() {
    const v = radiosOut();
    $("#vtOutHint").textContent = { mp4: "", gif: "Up to 20 seconds, 480 px wide, no sound.", frame: "Saves the picture at the playhead as a PNG." }[v] || "";
    $("#vtGo").textContent = { mp4: "Render video", gif: "Render GIF", frame: "Save frame" }[v];
  }

  async function submit(btn, path, bodyFn, label) {
    notice("");
    btn.disabled = true;
    try {
      const body = await bodyFn();
      if (!body) return;
      const job = await post(path, body);
      S.mine.add(job.job_id);
      S.view = { kind: "job", id: job.job_id, index: 0 };
      S.compare = false;
      await refresh(true);
      toast(`${label} queued`);
    } catch (e) { notice(e.message); }
    finally { btn.disabled = false; }
  }

  $("#imgGo").addEventListener("click", (e) => submit(e.currentTarget, "/v1/images/jobs", () => {
    const prompt = $("#imgPrompt").value.trim();
    if (!prompt) { notice("Describe the image first."); $("#imgPrompt").focus(); return null; }
    const p = S.caps.image.presets[S.imgPreset];
    return { prompt, width: p.width, height: p.height, steps: num("#imgSteps") || S.caps.image.default_steps,
             count: +radiosImgCount(), ...(num("#imgSeed") != null && { seed: num("#imgSeed") }) };
  }, "Image"));

  $("#vidGo").addEventListener("click", (e) => submit(e.currentTarget, "/v1/videos/jobs", async () => {
    const prompt = $("#vidPrompt").value.trim();
    if (!prompt) { notice("Describe the video first."); $("#vidPrompt").focus(); return null; }
    const p = S.caps.video.presets[S.vidPreset];
    const body = { prompt, width: p.width, height: p.height, frames: updateVidInfo(), fps: +$("#vidFps").value };
    if (num("#vidSteps")) body.steps = num("#vidSteps");
    if (num("#vidSeed") != null) body.seed = num("#vidSeed");
    if ($("#vidNeg").value.trim() && S.caps.video.supports.negative_prompt) body.negative_prompt = $("#vidNeg").value.trim();
    if (S.caps.video.supports.audio) body.audio = $("#vidAudio").checked;
    const start = S.inputs["vid-start"][0];
    if (start) body.reference_asset_id = await ensureAsset(start);
    return body;
  }, "Video"));

  $("#editGo").addEventListener("click", (e) => submit(e.currentTarget, "/v1/images/jobs", async () => {
    const recs = S.inputs["edit-img"];
    if (!recs.length) { notice("Choose an image to edit first."); return null; }
    const prompt = $("#editPrompt").value.trim();
    if (!prompt) { notice("Describe what should change."); $("#editPrompt").focus(); return null; }
    const ids = [];
    for (const r of recs) ids.push(await ensureAsset(r));
    return { prompt, reference_asset_ids: ids, steps: num("#editSteps") || S.caps.image.default_steps,
             count: +radiosEditCount(), ...(num("#editSeed") != null && { seed: num("#editSeed") }) };
  }, "Edit"));

  $("#adjGo").addEventListener("click", (e) => submit(e.currentTarget, "/v1/images/adjust", () => {
    const rec = S.inputs["edit-img"][0];
    if (!rec) { notice("Choose an image to adjust first."); return null; }
    const ops = [];
    const rot = +$("#adjRotate").value;
    if (rot) ops.push({ op: "rotate", degrees: rot });
    if ($("#adjFlip").checked) ops.push({ op: "flip", axis: "h" });
    const crop = $("#adjCrop").value;
    if (crop) { const [w, h] = crop.split(":").map(Number); ops.push({ op: "crop_aspect", ratio_w: w, ratio_h: h }); }
    if ($("#adjGray").checked) ops.push({ op: "grayscale" });
    const a = { brightness: +$("#adjBright").value, contrast: +$("#adjContrast").value, saturation: +$("#adjSat").value, sharpness: +$("#adjSharp").value };
    if (Object.values(a).some((v) => v !== 1)) ops.push({ op: "adjust", ...a });
    if (+$("#adjBlur").value > 0) ops.push({ op: "blur", radius: +$("#adjBlur").value });
    if (!ops.length) { notice("Change at least one setting first."); return null; }
    return { ...srcBody(rec), ops };
  }, "Adjustment"));

  $("#vtGo").addEventListener("click", (e) => submit(e.currentTarget, "/v1/videos/edit", () => {
    const rec = S.inputs["edit-vid"][0];
    if (!rec) { notice("Choose a video to edit first."); return null; }
    const output = radiosOut();
    const v = $("#stageVideo");
    if (output === "frame") return { ...srcBody(rec), ops: [], output, frame_time: v ? +v.currentTime.toFixed(3) : 0 };
    const ops = [];
    const start = num("#vtStart"), end = num("#vtEnd");
    if (start || end) {
      if (end && end <= (start || 0)) { notice("The end time must be after the start time."); return null; }
      ops.push({ op: "trim", start: start || 0, ...(end && { end }) });
    }
    if (+$("#vtSpeed").value !== 1) ops.push({ op: "speed", factor: +$("#vtSpeed").value });
    if ($("#vtReverse").checked) ops.push({ op: "reverse" });
    if ($("#vtSize").value) { const [w, h] = $("#vtSize").value.split("x").map(Number); ops.push({ op: "resize", width: w, height: h, mode: $("#vtFit").value }); }
    if (+$("#vtRotate").value) ops.push({ op: "rotate", degrees: +$("#vtRotate").value });
    if ($("#vtFlip").value) ops.push({ op: "flip", axis: $("#vtFlip").value });
    if ($("#vtFilter").value) ops.push({ op: "filter", name: $("#vtFilter").value });
    const adj = { brightness: +$("#vtBright").value, contrast: +$("#vtContrast").value, saturation: +$("#vtSat").value };
    if (adj.brightness !== 0 || adj.contrast !== 1 || adj.saturation !== 1) ops.push({ op: "adjust", ...adj });
    if (num("#vtFadeIn") || num("#vtFadeOut")) ops.push({ op: "fade", fade_in: num("#vtFadeIn") || 0, fade_out: num("#vtFadeOut") || 0 });
    if ($("#vtMute").checked) ops.push({ op: "mute" });
    if (!ops.length && output === "mp4") { notice("Choose at least one change, or save as GIF or a single frame."); return null; }
    return { ...srcBody(rec), ops, output };
  }, "Render"));

  $$("[data-grab]").forEach((b) => b.addEventListener("click", () => {
    const v = $("#stageVideo");
    if (!v) return notice("Load the video in the preview first.");
    $("#" + b.dataset.grab).value = v.currentTime.toFixed(2);
  }));

  // Slider read-outs
  $$(".sliders input[type=range]").forEach((r) => {
    const out = r.closest("label").querySelector("output");
    const sync = () => (out.textContent = (+r.value).toFixed(2).replace(/\.?0+$/, "") || "0");
    r.addEventListener("input", sync); sync();
  });

  // Ctrl/Cmd+Enter submits the current mode
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { const b = $(GO[S.mode]); b && !b.disabled && b.click(); return; }
    const v = $("#stageVideo");
    if (!v || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable) return;
    if (e.key === "ArrowRight") { e.preventDefault(); stepFrame(1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); stepFrame(-1); }
    else if (e.key === " " && !document.activeElement.closest("button, video, summary, .drop")) { e.preventDefault(); v.paused ? v.play() : v.pause(); }
  });

  /* ------------------------------------------------------------ jobs: polling */
  async function refresh(force = false) {
    const { jobs } = await api("/v1/jobs?limit=60");
    S.jobs = jobs;
    for (const j of jobs) {
      if (S.mine.has(j.job_id) && !isActive(j)) {
        S.mine.delete(j.job_id);
        if (j.status === "succeeded") { toast(`${KIND[j.kind]} ready`); if (!S.view || S.view.kind === "job") S.view = { kind: "job", id: j.job_id, index: 0 }; }
        else if (j.status === "failed") notice(j.error || "The job failed.");
      }
    }
    if (S.view && S.view.kind === "job" && !jobs.find((j) => j.job_id === S.view.id)) S.view = null;
    renderStrip(force); renderActivity(force); renderStage(force);
  }
  function schedule() {
    clearTimeout(S.timer);
    const wait = document.hidden ? 15000 : S.jobs.some(isActive) ? 900 : 5000;
    S.timer = setTimeout(async () => { try { await refresh(); } catch { /* retry */ } schedule(); }, wait);
  }

  const jobTitle = (j) => {
    const p = j.params || {};
    if (p.prompt) return p.prompt;
    if (j.kind === "video_edit") return p.output === "frame" ? "Saved frame" : p.output === "gif" ? "GIF from video" : "Edited video";
    if (j.kind === "image_adjust") return "Adjusted image";
    return KIND[j.kind];
  };

  function renderActivity(force) {
    const act = S.jobs.filter(isActive);
    const sig = act.map((j) => `${j.job_id}${j.status}${Math.round(j.progress * 100)}${j.message}`).join("|");
    if (!force && sig === S.sig.activity) return;
    S.sig.activity = sig;
    $("#activity").replaceChildren(...act.map((j) =>
      el("div", { class: "job" },
        el("div", { class: "t" }, `${KIND[j.kind]}: ${short(jobTitle(j), 48)}`),
        el("button", { class: "btn sm", type: "button", on: { click: async () => { try { await post(`/v1/jobs/${j.job_id}/cancel`, {}); refresh(true); } catch (e) { notice(e.message); } } } }, "Cancel"),
        el("div", { class: "m" }, j.status === "queued" ? "Waiting in queue" : `${j.message || "Working"} · ${Math.round(j.progress * 100)}%`),
        el("div", { class: "bar" }, el("i", { style: `width:${Math.max(3, Math.round(j.progress * 100))}%` })))));
  }

  function renderStrip(force) {
    const sig = S.jobs.map((j) => `${j.job_id}${j.status}${Math.round(j.progress * 10)}`).join("|") + JSON.stringify(S.view && [S.view.kind, S.view.id]);
    if (!force && sig === S.sig.strip) return;
    S.sig.strip = sig;
    $("#stripCount").textContent = S.jobs.length ? `${S.jobs.length} item${S.jobs.length > 1 ? "s" : ""}` : "";
    const root = $("#strip");
    if (!S.jobs.length) { root.replaceChildren(el("p", { class: "strip-empty" }, "Everything you make appears here, and stays after you close the app.")); return; }
    root.replaceChildren(...S.jobs.map((j) => {
      const sel = S.view && S.view.kind === "job" && S.view.id === j.job_id;
      const a = j.artifacts[0];
      const th = el("span", { class: "th" });
      if (j.status === "succeeded" && a) th.append(el("img", { src: a.thumb_url, alt: "", loading: "lazy" }));
      else th.append(el("span", { class: "state" + (j.status === "failed" ? " err" : "") },
        { queued: "Queued", running: `${Math.round(j.progress * 100)}%`, failed: "Failed", cancelled: "Cancelled" }[j.status] || j.status));
      if (isActive(j)) th.append(el("div", { class: "bar" }, el("i", { style: `width:${Math.max(3, Math.round(j.progress * 100))}%` })));
      if (j.artifacts.length > 1) th.append(el("span", { class: "badge" }, `×${j.artifacts.length}`));
      const extra = a && a.duration ? fmtTime(a.duration) : a && a.width ? `${a.width}×${a.height}` : "";
      const t = new Date(j.created_at * 1000);
      return el("div", { class: "tile", "data-sel": sel ? "1" : false },
        el("button", { class: "main", type: "button", title: jobTitle(j), "aria-current": sel ? "true" : false, on: { click: () => { S.view = { kind: "job", id: j.job_id, index: 0 }; S.compare = false; renderStrip(true); renderStage(true); } } },
          th, el("span", { class: "cap" }, el("span", {}, KIND[j.kind]), el("span", {}, extra || t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })))),
        !isActive(j) && el("button", { class: "x", type: "button", "aria-label": "Delete", title: "Delete", on: { click: async (e) => {
          e.stopPropagation();
          if (!confirm("Delete this item from history? This can't be undone.")) return;
          try { await api(`/v1/jobs/${j.job_id}`, { method: "DELETE" }); refresh(true); } catch (err) { notice(err.message); }
        } } }, "×"));
    }));
  }

  /* ------------------------------------------------------------ stage */
  function showSource(rec) { S.view = { kind: "src", rec }; S.compare = false; renderStrip(true); renderStage(true); }
  const curJob = () => (S.view && S.view.kind === "job" ? S.jobs.find((j) => j.job_id === S.view.id) : null);

  const EMPTY = {
    image: ["Your image appears here", "Describe what you want and press Generate. Drop a photo anywhere on this area to edit it instead."],
    video: ["Your video appears here", "Describe a scene, or start from an image. Clips take longer than pictures, so you can keep working while it renders."],
    "edit-image": ["Choose an image to edit", "Describe a change in words, or use the quick adjustments for instant results."],
    "edit-video": ["Choose a video to edit", "Trim, change speed, reframe, add a look, or grab a frame. Drop a video here to begin."],
  };

  function renderStage(force) {
    const job = curJob(), v = S.view;
    const sig = !v ? `empty|${S.mode}` : v.kind === "src" ? `src|${v.rec.url}` :
      `${v.id}|${job ? job.status : "?"}|${v.index}|${S.compare}`;
    if (!force && sig === S.sig.stage) { if (job && isActive(job)) updateRunning(job); return; }
    S.sig.stage = sig;
    const bar = $("#stageBar");
    bar.replaceChildren();

    if (!v || (v.kind === "job" && !job)) {
      const [h, p] = EMPTY[S.mode];
      stage.replaceChildren(el("div", { class: "gate" }, el("h2", {}, h), el("p", {}, p)));
      return;
    }
    if (v.kind === "src") { renderMedia(v.rec.kind, v.rec.url, v.rec.thumb_url, v.rec.filename || "Source", bar, { rec: v.rec }); return; }

    if (isActive(job)) {
      stage.replaceChildren(el("div", { class: "rendering" },
        el("div", { class: "pct", id: "rPct" }, "0%"), el("div", { class: "bar" }, el("i", { id: "rBar" })),
        el("p", { id: "rMsg" }), el("p", { class: "bar-meta", style: "margin-top:10px" }, short(jobTitle(job), 90))));
      updateRunning(job); return;
    }
    if (job.status !== "succeeded") {
      stage.replaceChildren(el("div", { class: "failed" },
        el("h2", {}, job.status === "cancelled" ? "Cancelled" : "That didn't work"),
        el("p", {}, job.error || (job.status === "cancelled" ? "You cancelled this job." : "Unknown error."))));
      bar.append(el("div", { class: "bar-row" }, el("button", { class: "btn sm danger", type: "button", on: { click: () => deleteJob(job) } }, "Remove from history")));
      return;
    }
    const idx = Math.min(v.index, job.artifacts.length - 1), art = job.artifacts[idx];
    const original = originalUrl(job);
    const url = S.compare && original ? original.url : art.url;
    renderMedia(art.mime.startsWith("video/") ? "video" : "image", url, art.thumb_url, jobTitle(job), bar, { job, art, idx, original });
  }

  function updateRunning(job) {
    const p = Math.round(job.progress * 100);
    const a = $("#rPct"), b = $("#rBar"), m = $("#rMsg");
    if (a) a.textContent = `${p}%`;
    if (b) b.style.width = `${Math.max(2, p)}%`;
    if (m) m.textContent = job.status === "queued" ? "Waiting for the previous job to finish…" : job.message || "Working…";
  }

  function originalUrl(job) {
    const p = job.params || {};
    if (p.reference_asset_ids && p.reference_asset_ids[0]) return { url: `/v1/assets/${p.reference_asset_ids[0]}` };
    if (p.source_asset_id && job.kind === "image_adjust") return { url: `/v1/assets/${p.source_asset_id}` };
    if (p.source_job_id && job.kind === "image_adjust") return { url: `/v1/media/${p.source_job_id}/${p.source_index || 0}` };
    return null;
  }

  function renderMedia(kind, url, poster, title, bar, ctx) {
    let node;
    if (kind === "video") {
      node = el("video", { id: "stageVideo", src: url, poster, controls: true, playsinline: true, preload: "metadata" });
    } else node = el("img", { src: url, alt: title });
    stage.replaceChildren(node);

    const { job, art, idx, rec } = ctx;
    const meta = [];
    const w = art ? art.width : rec && rec.width, h = art ? art.height : rec && rec.height;
    if (w) meta.push(`${w}×${h}`);
    const dur = art ? art.duration : rec && rec.duration;
    if (dur) meta.push(fmtTime(dur));
    if (job && job.metadata) {
      if (job.metadata.seeds && job.metadata.seeds[idx] != null) meta.push(`seed ${job.metadata.seeds[idx]}`);
      if (job.metadata.seconds) meta.push(`${job.metadata.seconds}s to make`);
      if (job.metadata.mock) meta.push("placeholder");
    }
    if (rec && rec.bytes) meta.push(fmtMB(rec.bytes));

    const top = el("div", { class: "bar-row" },
      el("div", { class: "grow" }, el("div", { class: "bar-title", title }, short(title, 120)), el("div", { class: "bar-meta" }, meta.join(" · "))));
    if (job && job.artifacts.length > 1)
      top.append(el("div", { class: "seg", role: "group", "aria-label": "Variation" }, job.artifacts.map((a2, i) =>
        el("button", { type: "button", "aria-pressed": String(i === idx), on: { click: () => { S.view.index = i; S.compare = false; renderStage(true); } } }, `${i + 1}`))));
    if (ctx.original)
      top.append(el("div", { class: "seg", role: "group", "aria-label": "Compare" },
        el("button", { type: "button", "aria-pressed": String(!S.compare), on: { click: () => { S.compare = false; renderStage(true); } } }, "Result"),
        el("button", { type: "button", "aria-pressed": String(S.compare), on: { click: () => { S.compare = true; renderStage(true); } } }, "Original")));
    bar.append(top);

    const acts = el("div", { class: "bar-row" });
    const dl = job ? `${art.url}?download=1` : url;
    acts.append(el("a", { class: "btn sm primary", href: dl, download: "" }, "Download"));
    const asRec = () => job
      ? { job_id: job.job_id, index: idx, url: art.url, thumb_url: art.thumb_url, kind, width: art.width, height: art.height, duration: art.duration, filename: short(jobTitle(job), 40) }
      : rec;
    if (kind === "image") {
      acts.append(
        el("button", { class: "btn sm", type: "button", on: { click: () => useInput("edit-img", asRec(), "edit-image") } }, "Edit this image"),
        el("button", { class: "btn sm", type: "button", on: { click: () => useInput("vid-start", asRec(), "video") } }, "Animate this image"));
    } else {
      acts.append(
        el("button", { class: "btn sm", type: "button", on: { click: () => useInput("edit-vid", asRec(), "edit-video") } }, "Edit this video"),
        el("button", { class: "btn sm", type: "button", title: "Capture the current frame and edit it as an image", on: { click: () => frameTo("edit-img", "edit-image") } }, "Frame → edit"),
        el("button", { class: "btn sm", type: "button", title: "Capture the current frame and use it to start a new video", on: { click: () => frameTo("vid-start", "video") } }, "Frame → animate"));
    }
    if (job) acts.append(el("button", { class: "btn sm danger", type: "button", on: { click: () => deleteJob(job) } }, "Delete"));
    bar.append(acts);

    if (kind === "video") {
      const fps = (job && job.params && job.params.fps) || 24;
      const spd = el("select", { "aria-label": "Playback speed", on: { change: (e) => (node.playbackRate = +e.target.value) } },
        [0.25, 0.5, 1, 1.5, 2].map((s) => el("option", { value: s, selected: s === 1 }, s === 1 ? "1×" : `${s}×`)));
      const time = el("output", {}, `0:00.0 / ${fmtTime(dur)}`);
      node.addEventListener("timeupdate", () => (time.textContent = `${fmtTime(node.currentTime)} / ${fmtTime(node.duration)}`));
      node.addEventListener("loadedmetadata", () => (time.textContent = `${fmtTime(node.currentTime)} / ${fmtTime(node.duration)}`));
      node.dataset.fps = fps;
      bar.append(el("div", { class: "player" },
        "Speed", spd,
        el("label", { class: "check" }, el("input", { type: "checkbox", on: { change: (e) => (node.loop = e.target.checked) } }), "Loop"),
        el("button", { class: "mini", type: "button", "aria-label": "Previous frame", on: { click: () => stepFrame(-1) } }, "◀ Frame"),
        el("button", { class: "mini", type: "button", "aria-label": "Next frame", on: { click: () => stepFrame(1) } }, "Frame ▶"),
        time, "← → step frames"));
    }
  }

  function stepFrame(dir) {
    const v = $("#stageVideo");
    if (!v) return;
    v.pause();
    const fps = +v.dataset.fps || 24;
    v.currentTime = Math.max(0, Math.min(v.duration || 0, v.currentTime + dir / fps));
  }

  function useInput(key, rec, mode) {
    S.inputs[key] = [rec];
    renderDrop(zoneFor(key));
    setMode(mode);
    if (mode === "edit-image" || mode === "edit-video") showSource(rec);
    toast("Ready in the " + $(`.modes [data-mode="${mode}"]`).textContent + " tab");
  }

  async function frameTo(key, mode) {
    const v = $("#stageVideo");
    if (!v || !v.videoWidth) return notice("Wait for the video to load, then pause on the frame you want.");
    v.pause();
    const c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    const blob = await new Promise((r) => c.toBlob(r, "image/png"));
    try {
      toast("Capturing frame…");
      const rec = await uploadFile(new File([blob], `frame-${v.currentTime.toFixed(2)}s.png`, { type: "image/png" }));
      S.inputs[key] = [rec]; renderDrop(zoneFor(key)); setMode(mode);
      if (mode === "edit-image") showSource(rec);
    } catch (e) { notice(e.message); }
  }

  async function deleteJob(job) {
    if (!confirm("Delete this item from history? This can't be undone.")) return;
    try { await api(`/v1/jobs/${job.job_id}`, { method: "DELETE" }); S.view = null; refresh(true); }
    catch (e) { notice(e.message); }
  }

  /* ------------------------------------------------------------ boot */
  async function boot() {
    try {
      applyCaps(await api("/v1/capabilities"));
      await refresh(true);
      schedule();
    } catch (e) {
      $("#statusChip").textContent = "Not connected";
      if (!/Sign in/.test(e.message)) notice(e.message);
    }
  }
  document.addEventListener("visibilitychange", () => !document.hidden && schedule());
  setMode("image");
  boot();
  window.__studio = S; // handy for debugging in the browser console
})();
