const ALWAYS_TERMINAL = new Set([
  "DELIVERED",
  "PARTIAL",
  "FAILED",
  "FAILED_NO_PROGRESS",
  "ESCALATED",
]);

const $ = (id) => document.getElementById(id);

const state = {
  tenantId: "",
  pollTimer: null,
  objectUrl: null,
  featureShotGeneration: false,
  featureAssembleDeliver: false,
  typeTimer: null,
  typedFull: "",
  typedShown: "",
};

function isTerminal(status) {
  if (ALWAYS_TERMINAL.has(status)) return true;
  if (status === "SHOTS_READY") return !state.featureAssembleDeliver;
  if (status === "BIBLE_LOCKED") return !state.featureShotGeneration;
  return false;
}

function setHint(text, show) {
  const el = $("setup-hint");
  el.hidden = !show;
  el.textContent = text;
}

function setError(payload) {
  const el = $("error");
  if (!payload) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  const code = payload.code || "ERROR";
  const message = payload.message || "Request failed";
  const trace = payload.trace_id ? ` trace_id=${payload.trace_id}` : "";
  el.textContent = `${code}: ${message}${trace}`;
}

function headers() {
  return {
    "Content-Type": "application/json",
    "X-Tenant-Id": state.tenantId,
  };
}

async function readError(response) {
  try {
    return await response.json();
  } catch {
    return {
      code: "HTTP_" + response.status,
      message: response.statusText || "Request failed",
    };
  }
}

function renderBible(bible) {
  const el = $("bible");
  if (!bible) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  const rows = [
    ["character", bible.character],
    ["wardrobe", bible.wardrobe],
    ["location", bible.location],
    ["lighting", bible.lighting],
    ["palette", bible.palette],
    ["lens", bible.lens],
  ];
  el.innerHTML = rows
    .map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v || "—")}</dd>`)
    .join("");
  el.hidden = false;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderShots(shots, activeBeat) {
  const byBeat = new Map((shots || []).map((s) => [s.beat_index, s]));
  document.querySelectorAll("#strip li").forEach((li) => {
    const beat = Number(li.dataset.beat);
    const shot = byBeat.get(beat);
    li.classList.remove("exposed", "degraded", "failed", "loading");
    const stateEl = li.querySelector(".beat-state");
    if (!shot) {
      stateEl.textContent = beat === activeBeat ? "rolling" : "—";
      if (beat === activeBeat) li.classList.add("loading");
      return;
    }
    stateEl.textContent = shot.status + (shot.qc_score != null ? ` ${shot.qc_score}` : "");
    if (shot.status === "SUCCEEDED") li.classList.add("exposed");
    if (shot.degraded) li.classList.add("degraded");
    if (shot.status === "FAILED") li.classList.add("failed");
    if (beat === activeBeat && shot.status !== "SUCCEEDED" && shot.status !== "FAILED") {
      li.classList.add("loading");
      stateEl.textContent = "rolling";
    }
  });
}

function stopPoll() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function stopTypewriter() {
  if (state.typeTimer) {
    clearInterval(state.typeTimer);
    state.typeTimer = null;
  }
}

function setPhase(text) {
  const loader = $("loader");
  const typed = $("loader-type");
  if (!loader || !typed) return;
  if (!text) {
    stopTypewriter();
    state.typedFull = "";
    state.typedShown = "";
    typed.textContent = "";
    loader.hidden = true;
    return;
  }
  loader.hidden = false;
  if (text === state.typedFull) return;
  stopTypewriter();
  state.typedFull = text;
  state.typedShown = "";
  if (prefersReducedMotion()) {
    typed.textContent = text;
    return;
  }
  state.typeTimer = setInterval(() => {
    state.typedShown = state.typedFull.slice(0, state.typedShown.length + 1);
    typed.textContent = state.typedShown;
    if (state.typedShown === state.typedFull) stopTypewriter();
  }, 16);
}

function activeBeatFor(body) {
  if (isTerminal(body.status)) return 0;
  const shots = body.shots || [];
  const done = shots.filter((shot) => shot.status === "SUCCEEDED").length;
  if (body.status === "BIBLE_LOCKED" || body.status === "RUNNING" || body.status === "QUEUED") {
    if (done < 4 && (body.continuity_bible || body.status === "BIBLE_LOCKED")) {
      return done + 1;
    }
  }
  return 0;
}

function phaseFor(body) {
  const status = body.status;
  if (ALWAYS_TERMINAL.has(status) || isTerminal(status)) return "";
  const shots = body.shots || [];
  const done = shots.filter((shot) => shot.status === "SUCCEEDED").length;
  if (status === "QUEUED" || status === "RUNNING") {
    if (!body.continuity_bible) {
      return "Planning the 4-beat story and locking the continuity bible…";
    }
  }
  if (status === "BIBLE_LOCKED" || status === "RUNNING") {
    if (done < 4) {
      return (
        `Next: generate shot ${done + 1} of 4 (${["setup", "development", "turn", "resolution"][done]}). ` +
        "Higgsfield often takes several minutes per shot — leave this tab open."
      );
    }
    return "Shots are in; scoring and assembling the 40s clip…";
  }
  if (status === "SHOTS_READY") {
    return "Shots ready; stitching assembled.mp4…";
  }
  return "Working…";
}

async function attachMedia(url, tenantId) {
  if (state.objectUrl) {
    URL.revokeObjectURL(state.objectUrl);
    state.objectUrl = null;
  }
  const player = $("player");
  const download = $("download");
  if (!url) {
    player.hidden = true;
    download.hidden = true;
    return;
  }
  const response = await fetch(url, { headers: { "X-Tenant-Id": tenantId } });
  if (!response.ok) {
    setError(await readError(response));
    return;
  }
  const blob = await response.blob();
  state.objectUrl = URL.createObjectURL(blob);
  player.src = state.objectUrl;
  player.hidden = false;
  download.href = state.objectUrl;
  download.download = "assembled.mp4";
  download.hidden = false;
}

async function refreshJob(jobId) {
  const response = await fetch(`/jobs/${jobId}`, { headers: headers() });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    stopPoll();
    $("generate").disabled = false;
    $("generate").setAttribute("aria-busy", "false");
    setPhase("");
    setError(body);
    return body;
  }
  $("status").textContent = body.status;
  $("job-meta").textContent = `job ${body.job_id} · $${Number(body.budget_used_usd).toFixed(4)} · ${body.budget_used_tokens} tokens`;
  renderBible(body.continuity_bible);
  const phase = phaseFor(body);
  renderShots(body.shots, phase ? activeBeatFor(body) : 0);
  setPhase(phase);
  $("generate").setAttribute("aria-busy", phase ? "true" : "false");
  if (isTerminal(body.status)) {
    stopPoll();
    $("generate").disabled = false;
    if (body.download_url) {
      await attachMedia(body.download_url, state.tenantId);
    }
    if (
      ["FAILED", "FAILED_NO_PROGRESS", "ESCALATED"].includes(body.status) &&
      !body.download_url
    ) {
      setError({
        code: body.last_error_code || body.status,
        message:
          body.last_error_message ||
          (body.continuity_bible && (!body.shots || body.shots.length === 0)
            ? "Story plan succeeded; Higgsfield video generation failed on shot 1. Check VIDEO_MCP_API_KEY (Higgsfield, not Gemini)."
            : "Job ended without a downloadable video."),
      });
    }
  }
  return body;
}

async function generate() {
  try {
    await generateJob();
  } catch (exc) {
    $("generate").disabled = false;
    $("generate").setAttribute("aria-busy", "false");
    setPhase("");
    setError({
      code: "CONSOLE_ERROR",
      message: exc && exc.message ? exc.message : String(exc),
    });
  }
}

async function generateJob() {
  setError(null);
  const prompt = $("prompt").value.trim();
  if (!prompt) {
    setError({ code: "PROMPT_EMPTY", message: "Write a story prompt first." });
    return;
  }
  if (!state.tenantId) {
    setError({
      code: "TENANT_ID_MISSING",
      message: "Set TENANT_ID in .env and run python -m app seed-tenant.",
    });
    return;
  }
  $("generate").disabled = true;
  $("generate").setAttribute("aria-busy", "true");
  $("status").textContent = "QUEUED";
  $("player").hidden = true;
  $("download").hidden = true;
  setPhase("Queuing the job. Planning the 4-beat story next…");
  stopPoll();
  const response = await fetch("/jobs", {
    method: "POST",
    headers: {
      ...headers(),
      "Idempotency-Key": crypto.randomUUID(),
    },
    body: JSON.stringify({ prompt }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    $("generate").disabled = false;
    $("generate").setAttribute("aria-busy", "false");
    setPhase("");
    setError(body);
    return;
  }
  $("job-meta").textContent = `job ${body.job_id}`;
  const latest = await refreshJob(body.job_id);
  if (!latest || isTerminal(latest.status)) {
    return;
  }
  state.pollTimer = setInterval(() => {
    refreshJob(body.job_id);
  }, 2000);
}

async function boot() {
  try {
    const response = await fetch("/ui/config");
    const cfg = await response.json();
    state.tenantId = cfg.tenant_id || "";
    state.featureShotGeneration = Boolean(cfg.feature_shot_generation);
    state.featureAssembleDeliver = Boolean(cfg.feature_assemble_deliver);
    if (!state.tenantId) {
      setHint(
        "Set TENANT_ID in .env, run python -m app seed-tenant, then restart uvicorn.",
        true
      );
      $("generate").disabled = true;
    }
  } catch {
    setHint("Could not load /ui/config. Is the API running?", true);
    $("generate").disabled = true;
  }
  $("generate").addEventListener("click", generate);
}

boot();
