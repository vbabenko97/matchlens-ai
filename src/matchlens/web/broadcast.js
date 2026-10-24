"use strict";
// Broadcast lower-third for a browser source (OBS, vMix, a CasparCG HTML template): reads
// data/overlays.json (schema matchlens.overlays/v1), the same machine-readable cue list a
// graphics system would consume, and draws the active cue on a transparent page. Only the
// verified "why" fact goes on air; narrative wording never does.
//
// URL parameters:
//   mode=casual|analyst          audience mode (default casual)
//   bg=transparent|green|dark    page background (default transparent, for compositing)
//   cue=<cue_id>                 show one cue and hold it (stills, manual cueing)
//   rundown=1                    play every cue of the mode in turn, looping
//   hold=<s>                     real seconds per cue in a rundown (default 8)
//   from=<s>, speed=<x>          otherwise follow the match clock from `from` match seconds

const SCHEMA = "matchlens.overlays/v1";
const EVIDENCE_RE = /\s*\[evidence:[^\]]*\]/g;
const KIND_LABEL = { goal: "Goal", big_chance_missed: "Big chance", press_win: "Press win", full_time: "Full time" };
const HOLD_REAL_S = 5; // at high speeds, keep a cue up at least this long in real time
const GAP_MS = 600; // blank between rundown cues so each entry reads as a new graphic
const MAX_IDS = 3; // analyst source line
const SCORE_RE = /(\d)-(\d)/g; // "2-3" must not wrap as "2-" / "3" on a graphic

const params = new URLSearchParams(location.search);
const $ = (id) => document.getElementById(id);

function num(name, fallback, lo, hi) {
  const v = Number(params.get(name));
  return params.has(name) && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback;
}

function fail(message) {
  const line = $("status-line");
  line.textContent = message;
  line.hidden = false;
  document.body.dataset.error = message;
}

function onAir(text) {
  return text.replace(EVIDENCE_RE, "").replace(SCORE_RE, "$1\u2011$2"); // non-breaking hyphen
}

function note(cue, fact) {
  const parts = [fact.kind === "heuristic" ? "Synthetic xG heuristic, checked against match events" : "✓ Checked against match events"];
  if (cue.mode === "analyst") {
    const ids = fact.event_ids.length ? fact.event_ids : cue.evidence_event_ids;
    const more = ids.length > MAX_IDS ? ` +${ids.length - MAX_IDS}` : "";
    parts.push(`Events ${ids.slice(0, MAX_IDS).join(", ")}${more}`);
  }
  return parts.join(" · ");
}

function show(cue, doc) {
  const lt = $("lower-third");
  if (!cue) {
    lt.hidden = true;
    delete lt.dataset.cue;
    return;
  }
  if (lt.dataset.cue === cue.cue_id) return;
  const fact = cue.facts.find((f) => f.role === "why") || cue.facts[0];
  $("lt-kind").textContent = KIND_LABEL[cue.kind] || cue.kind;
  $("lt-team").textContent = doc.teams[cue.team] || "";
  $("lt-title").textContent = onAir(cue.title);
  $("lt-fact").textContent = fact ? onAir(fact.text) : "";
  $("lt-note").textContent = fact ? note(cue, fact) : "";
  lt.dataset.team = cue.team || "";
  lt.dataset.cue = cue.cue_id;
  lt.hidden = false;
  lt.classList.remove("enter");
  void lt.offsetWidth; // restart the entry animation
  lt.classList.add("enter");
}

function followClock(doc, cues) {
  const from = num("from", 0, 0, 1e6);
  const speed = num("speed", 1, 0.1, 240);
  const t0 = performance.now();
  let current = null;
  let shownAt = 0;
  function frame(now) {
    const t = from + (speed * (now - t0)) / 1000;
    let next = null;
    for (const c of cues) if (c.start_s <= t && t < c.end_s) next = c; // latest to start wins
    if (!next && current && now - shownAt < HOLD_REAL_S * 1000) next = current;
    if (next !== current) {
      current = next;
      shownAt = now;
      show(next, doc);
    }
    document.body.dataset.clock = t.toFixed(1);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function rundown(doc, cues) {
  const hold = num("hold", 8, 2, 60) * 1000;
  let i = 0;
  const step = () => {
    show(cues[i], doc);
    setTimeout(() => {
      show(null, doc);
      i = (i + 1) % cues.length;
      setTimeout(step, GAP_MS);
    }, hold);
  };
  step();
}

function init(doc) {
  if (!doc || doc.schema !== SCHEMA || !Array.isArray(doc.cues)) {
    throw new Error(`expected ${SCHEMA}`);
  }
  const mode = params.get("mode") === "analyst" ? "analyst" : "casual";
  const cues = doc.cues.filter((c) => c.mode === mode).sort((a, b) => a.start_s - b.start_s);
  document.body.dataset.mode = mode;
  document.title = `MatchLens AI — ${doc.teams.home} v ${doc.teams.away} (${mode} overlay)`;
  if (params.has("cue")) {
    const cue = doc.cues.find((c) => c.cue_id === params.get("cue"));
    if (cue) show(cue, doc);
    else fail(`No cue "${params.get("cue")}" in this overlay file.`);
  } else if (!cues.length) {
    fail(`No ${mode} cues in this overlay file.`);
  } else if (params.get("rundown") === "1") {
    rundown(doc, cues);
  } else {
    followClock(doc, cues);
  }
  document.body.dataset.ready = "true";
}

const BACKGROUNDS = ["transparent", "green", "dark"];
document.body.dataset.bg = BACKGROUNDS.includes(params.get("bg")) ? params.get("bg") : "transparent";

fetch("data/overlays.json", { cache: "no-cache" })
  .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then(init)
  .catch((err) => {
    fail(`Could not load overlay cues: ${err.message}`);
    document.body.dataset.ready = "true";
  });
