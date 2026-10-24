// MatchLens replay UI. Displays a precomputed bundle; it never decides what is verified.
"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const PITCH_W = 105;
const PITCH_H = 68;
const BALL_TYPES = new Set(["pass", "shot", "tackle", "interception", "recovery", "clearance"]);
const TRAIL = 7; // recent ball events drawn on the pitch
const HOLD_REAL_S = 5; // keep the lower-third up at least this long in real time
const EVIDENCE_RE = /\s*\[evidence:[^\]]*\]/g;
const KIND_LABEL = { goal: "Goal", big_chance_missed: "Big chance", press_win: "Press win", full_time: "Full time" };
const MARKER_GLYPH = { goal: "G", big_chance_missed: "!", press_win: "P", full_time: "FT" };
const SOURCE_LABEL = {
  foundry_agent: "Foundry agent",
  recorded_foundry_agent: "Foundry agent (recorded run)",
  offline_proposer: "Offline proposer",
  injected_test_claim: "Injected test claim (deliberately wrong)",
};
// Why a live check did not run or finish (matchlens.live fallback reasons).
const LIVE_REASON = {
  not_configured: "Live checks are not configured on this deployment.",
  client_limit: "You have used your live checks for now (3 per 10 minutes).",
  daily_limit: "Today's live-check budget is used up.",
  busy: "Another live check is running; try again in a moment.",
  deadline: "Microsoft Foundry did not finish within 30 seconds.",
  foundry_unavailable: "Microsoft Foundry is unavailable right now.",
  counter_unavailable: "The live-check quota store is unavailable, so no live call was made.",
};
const LIVE_KEEP = " The recorded run below stays on the card — not a live call.";
const MARKER_GAP_PX = 26; // 24px targets must not overlap (WCAG 2.5.8)
const MARKER_LANE_REM = 1.75;
const ANNOUNCE_DELAY_MS = 600; // coalesce announcements while scrubbing or at high speed
// Focus inside these keeps its native keys (incl. scrolling the card panel); shortcuts apply elsewhere.
const NATIVE_KEYS = "input, select, textarea, button, a[href], summary, [role=radio], .side";

const $ = (id) => document.getElementById(id);
const state = {
  bundle: null, t: 0, playing: false, speed: 60, mode: "casual",
  selected: [], lastFrame: null, cardsKey: "", eventsById: new Map(),
  openChains: new Set(), spotlight: null, // UI state kept across card re-renders
  announced: { cards: -1, mode: "", fullTime: false }, announceTimer: null,
  live: {}, // card_id -> { pending } | { ok, message, review? }: live Foundry checks this visit
  index: null, entry: null, // data/matches.json and the match shown (null on older exports)
  variant: "en|neutral", // personalizer variant "lang|focus" (casual cards); en|neutral = source
  player: null, playerKey: "", // focused player (bundle.players record) and its rendered state
  passingKey: "",
};

const LANG_NAME = { en: "English", de: "Deutsch", uk: "Українська" };
const TITLE_INDEX = -1;

// A casual card in the chosen language and point of view, from the recorded personalizer run.
// Each line is either the checked rewrite or, when the checker rejected it twice, the English
// source (marked lang="en"). The facts keep their own kind, role and event IDs.
function localized(card) {
  const p = state.bundle.personalized;
  if (state.mode !== "casual" || !p || state.variant === "en|neutral") return null;
  const v = p.variants[state.variant];
  const rec = v && v.cards[card.card_id];
  if (!rec) return null;
  const byIndex = new Map(rec.lines.map((ln) => [ln.index, ln]));
  const langOf = (ln) => (ln && ln.status === "verified" ? v.lang : "en");
  const title = byIndex.get(TITLE_INDEX);
  return {
    lang: v.lang, focus: v.focus, label: p.label,
    title: title ? title.text : card.title, titleLang: langOf(title),
    facts: card.facts.map((f, i) => {
      const ln = byIndex.get(i);
      const rewritten = !!ln && ln.status === "verified";
      return ln ? { ...f, text: ln.text, lang: langOf(ln), rewritten } : { ...f, lang: "en" };
    }),
    kept: rec.lines.filter((ln) => ln.status !== "verified").length,
  };
}

function personalizedNote(loc) {
  const p = state.bundle.personalized;
  const critic = (p.critics || []).length > 0;
  const team = loc.focus === "neutral" ? "" : `, for ${state.bundle.meta[loc.focus].name} fans`;
  const who = critic ? "the checker, the critic or the editorial review" : "the checker or the editorial review";
  const kept = loc.kept ? ` ${loc.kept} line(s) kept in English: ${who} rejected the rewrite.` : "";
  const checks = critic
    ? "numbers and names checked against the English facts; meaning reviewed by a critic agent (also AI); tone not checked."
    : "numbers and names checked against the English facts; meaning and tone not checked.";
  return `${LANG_NAME[loc.lang]}${team}: AI wording by the Microsoft Foundry personalizer ` +
    `(${p.proposers.includes("recorded_foundry_personalizer") ? "recorded run" : "live"}); ${checks}${kept}`;
}

// One polite status region for screen readers; only the latest message is spoken.
function announce(text) {
  clearTimeout(state.announceTimer);
  state.announceTimer = setTimeout(() => { $("announcer").textContent = text; }, ANNOUNCE_DELAY_MS);
}

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  if (text !== undefined) node.textContent = text;
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

const sx = (x) => x;
const sy = (y) => PITCH_H - y; // data y grows up, SVG y grows down

function fmtClock(t) {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
const minuteOf = (t) => Math.floor(t / 60) + 1;
const whenOf = (card) => (card.kind === "full_time" ? "FT" : `${minuteOf(card.anchor_clock_s)}'`);

// Index of the last event with clock_s <= t (-1 if none).
function indexAt(t) {
  const ev = state.bundle.events;
  let lo = 0;
  let hi = ev.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (ev[mid].clock_s <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

// Last stat step (one per shot) with seq <= seq, or null before the first shot.
function stepAt(seq) {
  const steps = state.bundle.stat_steps;
  let lo = 0;
  let hi = steps.length - 1;
  let ans = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (steps[mid].seq <= seq) { ans = steps[mid]; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

function drawMarkings() {
  const g = $("markings");
  const add = (tag, attrs) => g.appendChild(svg(tag, attrs));
  add("rect", { x: 0, y: 0, width: PITCH_W, height: PITCH_H });
  add("line", { x1: PITCH_W / 2, y1: 0, x2: PITCH_W / 2, y2: PITCH_H });
  add("circle", { cx: PITCH_W / 2, cy: PITCH_H / 2, r: 9.15 });
  add("circle", { cx: PITCH_W / 2, cy: PITCH_H / 2, r: 0.4, class: "spot" });
  for (const left of [true, false]) {
    const x0 = left ? 0 : PITCH_W;
    const dir = left ? 1 : -1;
    const box = (depth, width) =>
      add("rect", { x: left ? 0 : PITCH_W - depth, y: (PITCH_H - width) / 2, width: depth, height: width });
    box(16.5, 40.32);
    box(5.5, 18.32);
    add("circle", { cx: x0 + dir * 11, cy: PITCH_H / 2, r: 0.4, class: "spot" });
    add("rect", { x: left ? -2 : PITCH_W, y: PITCH_H / 2 - 3.66, width: 2, height: 7.32 });
  }
}

function drawEvent(g, e, opacity, highlight) {
  if (!e.start) return;
  const [x1, y1] = e.start;
  const [x2, y2] = e.end || e.start;
  const team = e.team || "";
  if (highlight) {
    if (e.type === "pass" || e.type === "shot" || e.type === "clearance") {
      g.appendChild(svg("line", { x1: sx(x1), y1: sy(y1), x2: sx(x2), y2: sy(y2), class: "hl" }));
    }
    g.appendChild(svg("circle", { cx: sx(x1), cy: sy(y1), r: 1.1, class: "hl-dot" }));
    return;
  }
  const style = { opacity: String(opacity) };
  const mine = state.player && e.player_id === state.player.player_id ? " mine" : "";
  if (e.type === "pass" || e.type === "clearance") {
    const line = svg("line", { x1: sx(x1), y1: sy(y1), x2: sx(x2), y2: sy(y2), class: `ev pass ${team}${mine}` });
    Object.assign(line.style, style);
    g.appendChild(line);
  } else if (e.type === "shot") {
    const line = svg("line", { x1: sx(x1), y1: sy(y1), x2: sx(x2), y2: sy(y2), class: `ev shot ${team}${mine}` });
    Object.assign(line.style, style);
    g.appendChild(line);
    if (e.outcome === "goal") {
      g.appendChild(svg("circle", { cx: sx(x2), cy: sy(y2), r: 1.3, class: "goal-mark" }));
    }
  } else if (e.type === "pressure") {
    const ring = svg("circle", { cx: sx(x1), cy: sy(y1), r: 1.6, class: `ev ${team}${mine}` });
    Object.assign(ring.style, style);
    g.appendChild(ring);
  } else {
    const dot = svg("circle", { cx: sx(x1), cy: sy(y1), r: 0.9, class: `dot ${team}${mine}` });
    Object.assign(dot.style, style);
    g.appendChild(dot);
  }
}

function renderPitch(idx) {
  const trail = $("trail");
  trail.replaceChildren();
  const ev = state.bundle.events;
  const recent = [];
  for (let i = idx; i >= 0 && recent.length < TRAIL; i--) {
    if (BALL_TYPES.has(ev[i].type) || ev[i].type === "pressure") recent.push(ev[i]);
    if (ev[i].type === "period_start") break;
  }
  recent.reverse().forEach((e, i) => drawEvent(trail, e, 0.25 + (0.75 * (i + 1)) / recent.length, false));
  const lastBall = recent.filter((e) => BALL_TYPES.has(e.type)).pop();
  if (lastBall) {
    const [bx, by] = lastBall.end || lastBall.start;
    trail.appendChild(svg("circle", { cx: sx(bx), cy: sy(by), r: 0.8, class: "ball" }));
  }
  if (state.player) { // where the focused player last acted, up to now
    for (let i = idx; i >= 0; i--) {
      const e = ev[i];
      if (e.player_id === state.player.player_id && e.start) {
        const [px, py] = e.start;
        trail.appendChild(svg("circle", { cx: sx(px), cy: sy(py), r: 2.2, class: "player-pos" }));
        break;
      }
    }
  }
  const hl = $("highlight");
  hl.replaceChildren();
  for (const id of state.selected) {
    const e = state.eventsById.get(id);
    if (e) drawEvent(hl, e, 1, true);
  }
}

function renderScoreboard(idx) {
  const e = idx >= 0 ? state.bundle.events[idx] : null;
  $("home-score").textContent = e ? e.home_score : 0;
  $("away-score").textContent = e ? e.away_score : 0;
  $("clock").textContent = fmtClock(state.t);
  const half = state.bundle.meta.period_length_s;
  $("period").textContent = state.t >= state.bundle.duration_s ? "FT" : state.t >= half ? "2H" : "1H";
  // Totals after the last shot at or before the current event: in step with the scoreboard.
  const s = stepAt(e ? e.seq : 0) || { "home.shots": 0, "away.shots": 0, "home.xg_heuristic": 0, "away.xg_heuristic": 0 };
  $("stat-shots").textContent = `${s["home.shots"]} – ${s["away.shots"]}`;
  $("stat-xg").textContent = `${s["home.xg_heuristic"].toFixed(2)} – ${s["away.xg_heuristic"].toFixed(2)}`;
  if (e && e.team) {
    const team = e.team === "home" ? state.bundle.meta.home.name : state.bundle.meta.away.name;
    const what = (e.subtype && e.subtype !== "open_play" ? e.subtype : e.type).replace("_", " ");
    $("stat-last").textContent = `${e.minute}' ${team}: ${what}${e.outcome ? ` (${e.outcome.replace("_", " ")})` : ""}`;
  } else if (e && e.type === "period_end") {
    $("stat-last").textContent = e.period === 2 ? "Full time" : "Half time";
  } else {
    $("stat-last").textContent = "—";
  }
}

function cardsForMode() {
  return state.bundle.cards[state.mode] || [];
}

function activeCard() {
  const holdMatch = HOLD_REAL_S * state.speed;
  const visible = cardsForMode().filter((c) => c.publish_clock_s <= state.t);
  const last = visible[visible.length - 1];
  if (!last) return null;
  const until = Math.max(last.end_clock_s, last.publish_clock_s + holdMatch);
  return state.t <= until ? last : null;
}

function selectEvidence(ids) {
  state.selected = ids;
  state.cardsKey = "";
  render();
}

function inspectEvent(id) {
  const e = state.eventsById.get(id);
  const body = $("inspector-body");
  body.replaceChildren();
  $("inspector-hint").hidden = true;
  if (!e) { body.appendChild(el("p", { class: "hint" }, `Unknown event ${id}`)); return; }
  body.appendChild(el("h3", {}, `Event ${id}`));
  const jump = el("button", { type: "button" }, `Jump to ${e.minute}'`);
  jump.addEventListener("click", () => seek(e.clock_s));
  body.appendChild(jump);
  body.appendChild(el("pre", { id: "inspector-json" }, JSON.stringify(e, null, 2)));
  selectEvidence([id]);
}

function inspectMetric(id) {
  const m = state.bundle.metrics[id];
  const body = $("inspector-body");
  body.replaceChildren();
  $("inspector-hint").hidden = true;
  if (!m) { body.appendChild(el("p", { class: "hint" }, `Unknown metric ${id}`)); return; }
  body.appendChild(el("h3", {}, `Metric ${id}`));
  const dl = el("dl");
  const value = m.value === null ? "undefined" : String(m.value);
  const rows = [["Label", m.label], ["Value", value], ["Kind", m.kind], ["Definition", m.definition]];
  if (m.as_of_event_id) rows.push(["Counted up to", `event ${m.as_of_event_id} (inclusive)`]);
  rows.push(["Sources", `${m.source_event_ids.length} events`]);
  for (const [k, v] of rows) {
    dl.appendChild(el("dt", {}, k));
    dl.appendChild(el("dd", {}, v));
  }
  body.appendChild(dl);
  if (m.kind === "heuristic") body.appendChild(el("p", { class: "hint" }, "Synthetic heuristic — not empirically calibrated."));
  const chips = el("div", { class: "chips" });
  for (const sid of m.source_event_ids) chips.appendChild(eventChip(sid));
  body.appendChild(chips);
  selectEvidence(m.source_event_ids);
}

function eventChip(id) {
  const b = el("button", { type: "button", class: "chip", "data-event-id": id, "aria-pressed": String(state.selected.includes(id)) }, id);
  b.addEventListener("click", () => inspectEvent(id));
  return b;
}

function metricChip(id) {
  const b = el("button", { type: "button", class: "chip metric", "data-metric-id": id }, id);
  b.addEventListener("click", () => inspectMetric(id));
  return b;
}

// One verified fact. "why" facts (why the moment matters) lead and stand out.
function factItem(f, analyst) {
  const heuristic = f.kind === "heuristic";
  const li = el("li", { class: [f.role === "why" ? "why" : "", heuristic ? "heuristic" : ""].join(" ").trim() });
  if (f.lang) li.setAttribute("lang", f.lang);
  const checkTitle = f.rewritten
    ? "AI rewrite of a verified fact: numbers and names checked against it"
    : "Verified from structured event data";
  li.appendChild(el("span", { class: "check", title: checkTitle, "aria-hidden": "true" }, "✓"));
  const body = el("div");
  const prefix = f.rewritten ? "AI rewrite, numbers and names checked" : "Verified";
  body.appendChild(el("span", { class: "sr-only", lang: "en" }, heuristic ? `${prefix}, heuristic value: ` : `${prefix}: `));
  body.appendChild(el("span", {}, f.text));
  if (analyst && (f.event_ids.length || f.metric_ids.length)) body.appendChild(factChips(f));
  li.appendChild(body);
  return li;
}

function renderCard(card, isActive) {
  const analyst = state.mode === "analyst";
  const art = el("article", { class: `card ${card.team} ${card.kind}${isActive ? " active" : ""}`, "data-card-id": card.card_id });
  const loc = localized(card);
  const head = el("header");
  head.appendChild(el("span", { class: "kind" }, KIND_LABEL[card.kind] || card.kind));
  head.appendChild(el("span", { class: "minute" }, whenOf(card)));
  if (featured(card)) head.appendChild(el("span", { class: "features" }, `Features ${state.player.name}`));
  head.appendChild(el("h3", loc ? { lang: loc.titleLang } : {}, loc ? loc.title : card.title));
  art.appendChild(head);

  const facts = el("ul", { class: "facts", "aria-label": "Verified facts" });
  (loc ? loc.facts : card.facts).filter((f) => f.role !== "chain").forEach((f) => facts.appendChild(factItem(f, analyst)));
  art.appendChild(facts);
  if (loc) art.appendChild(el("p", { class: "hint personalized-note" }, personalizedNote(loc)));

  // Analyst: the move event by event, folded so the card reads as an explanation first.
  const chain = card.facts.filter((f) => f.role === "chain");
  if (analyst && chain.length) {
    const det = el("details", { class: "chain" });
    det.open = state.openChains.has(card.card_id);
    det.addEventListener("toggle", () => {
      if (det.open) state.openChains.add(card.card_id); else state.openChains.delete(card.card_id);
    });
    det.appendChild(el("summary", {}, `Event chain (${chain.length} event${chain.length > 1 ? "s" : ""})`));
    const list = el("ul", { class: "facts", "aria-label": "Evidence events" });
    chain.forEach((f) => list.appendChild(factItem(f, analyst)));
    det.appendChild(list);
    art.appendChild(det);
  }

  const live = state.live[card.card_id];
  const recorded = analyst && state.bundle.reviews ? state.bundle.reviews[card.card_id] : null;
  const review = recorded && live && live.review ? live.review : recorded;
  if (review) {
    if (state.bundle.live_check) art.appendChild(liveControls(card.card_id));
    art.appendChild(renderReview(review, state.spotlight === card.card_id));
  }

  if (analyst) {
    const all = el("button", { type: "button", class: "chip", "data-evidence": card.card_id },
      `Show all ${card.evidence_event_ids.length} evidence events on pitch`);
    all.addEventListener("click", () => selectEvidence(card.evidence_event_ids));
    art.appendChild(all);
  }

  // One label line: facts are checked; the wording's source is always named (never a fact).
  const n = card.narrative;
  const nar = el("div", { class: "narrative", "data-status": n.status });
  const unverified = n.status === "unverified_ai_wording";
  nar.appendChild(el("span", { class: "verified-note" }, "✓ checked against match events"));
  if (loc) {
    nar.appendChild(el("span", { class: "status template", title: state.bundle.personalized.label },
      (state.bundle.personalized.critics || []).length
        ? "AI wording (Foundry personalizer) · numbers & names checked · critic-reviewed"
        : "AI wording (Foundry personalizer) · numbers & names checked"));
  } else {
    nar.appendChild(el("span", { class: `status ${unverified ? "unverified" : "template"}`, title: n.label },
      unverified ? "AI wording · NOT VERIFIED" : "Offline template · not an Azure response"));
  }
  const duplicate = n.text === card.facts.map((f) => f.text).join(" ");
  // A rewritten card hides the English narrative: the note above already names its wording.
  if (!loc && unverified) {
    nar.appendChild(el("p", { class: "text" }, n.text));
  } else if (!loc && !duplicate) {
    const det = el("details");
    det.appendChild(el("summary", {}, "Wording"));
    det.appendChild(el("p", { class: "text" }, n.text));
    nar.appendChild(det);
  }
  if (n.fallback_reason) nar.appendChild(el("p", { class: "warn" }, n.fallback_reason));
  for (const w of n.warnings) nar.appendChild(el("p", { class: "warn" }, `⚠ ${w} (heuristic check only)`));
  art.appendChild(nar);
  return art;
}

function claimText(c) {
  const parts = [];
  if (c.metric_id) parts.push(`${c.metric_id} = ${c.value === null ? "(no value)" : c.value}`);
  if (c.cited_event_ids.length) parts.push(`events ${c.cited_event_ids.join(", ")}`);
  return parts.join("; ") || "(cites nothing)";
}

function factChips(f) {
  const chips = el("div", { class: "chips" });
  f.event_ids.forEach((id) => chips.appendChild(eventChip(id)));
  f.metric_ids.forEach((id) => chips.appendChild(metricChip(id)));
  return chips;
}

const INJECTED = "injected_test_claim";

function reviewSummary(r) {
  const parts = [];
  for (let a = 1; a <= r.attempts.length; a++) {
    const vs = r.verdicts.filter((v) => v.attempt === a && v.source !== INJECTED);
    const bad = vs.filter((v) => v.status !== "verified").length;
    let text = `Attempt ${a}: ${vs.length - bad} of ${vs.length} claims verified`;
    if (bad) text += a < r.attempts.length ? `, ${bad} rejected and sent back` : `, ${bad} rejected`;
    parts.push(text);
  }
  const tests = r.verdicts.filter((v) => v.source === INJECTED);
  if (tests.length) parts.push(`test claim ${tests.every((v) => v.status === "verified") ? "verified" : "rejected"}`);
  return `${parts.join(" · ")}.`;
}

function verdictItem(r, v, published) {
  const ok = v.status === "verified";
  const li = el("li", { class: ok ? "ok" : "rejected", "data-source": v.source });
  li.appendChild(el("span", { class: "check", "aria-hidden": "true" }, ok ? "✓" : "✗"));
  const body = el("div");
  const where = v.source === INJECTED ? "" : ` · attempt ${v.attempt}`;
  body.appendChild(el("span", { class: "source" }, `${SOURCE_LABEL[v.source] || v.source}${where}`));
  body.appendChild(el("span", { class: "sr-only" }, ok ? " Verified: " : " Rejected: "));
  body.appendChild(el("code", { class: "claim" }, claimText(v.claim)));
  for (const reason of v.reasons) body.appendChild(el("p", { class: "reason" }, `Rejected: ${reason}`));
  if (!ok && v.attempt === 1 && r.attempts.length > 1) {
    body.appendChild(el("p", { class: "hint" }, "Sent back to the agent with this reason; see attempt 2."));
  } else if (v.correction && published.has(v.correction.text)) {
    const fix = el("p", { class: "correction" });
    fix.appendChild(el("strong", {}, "Published instead: "));
    fix.appendChild(el("span", {}, v.correction.text));
    body.appendChild(fix);
    body.appendChild(factChips(v.correction));
  }
  li.appendChild(body);
  return li;
}

// Claim check: every proposed claim with the validator's verdict, attempt by attempt. Only
// ✓ claims and corrections rendered from computed metrics are published; the browser decides nothing.
function renderReview(r, spotlight) {
  const sec = el("section", { class: `review${spotlight ? " spotlight" : ""}`, "aria-label": "Claim check", tabindex: "-1", "data-review": r.card_id });
  sec.appendChild(el("h4", {}, "Claim check"));
  sec.appendChild(el("p", { class: "hint" }, r.proposer_label));
  if (r.fallback_reason) sec.appendChild(el("p", { class: "warn" }, r.fallback_reason));
  sec.appendChild(el("p", { class: "summary" }, reviewSummary(r)));
  const calls = r.attempts.flatMap((a) => a.calls);
  if (calls.length) {
    sec.appendChild(el("p", { class: "hint" },
      "The agent proposes structured claims (metric, value, event IDs) using read-only tools " +
      "that see the match only up to this card; the validator accepts or rejects each one, and " +
      "rejections go back to the agent once. Only verified claims are published."));
    const tools = calls.reduce((n, c) => n + c.tool_calls.length, 0);
    const ids = el("details", { class: "calls" });
    ids.appendChild(el("summary", {},
      `${r.attempts.length} attempt(s), ${calls.length} model call(s), ${tools} tool call(s): response IDs`));
    ids.appendChild(el("p", { class: "hint" }, calls.map((c) => c.response_id).join(", ")));
    sec.appendChild(ids);
  }
  const published = new Set(r.published.map((f) => f.text));
  const groups = r.attempts.map((_, i) => [
    i === 0 ? "Attempt 1 — proposed claims" : `Attempt ${i + 1} — after the validator's feedback`,
    r.verdicts.filter((v) => v.source !== INJECTED && v.attempt === i + 1),
  ]);
  groups.push(["Test claim — deliberately wrong", r.verdicts.filter((v) => v.source === INJECTED)]);
  for (const [title, verdicts] of groups) {
    if (!verdicts.length) continue;
    sec.appendChild(el("h5", {}, title));
    const list = el("ul", { class: "verdicts" });
    verdicts.forEach((v) => list.appendChild(verdictItem(r, v, published)));
    sec.appendChild(list);
  }
  for (const n of r.notes) sec.appendChild(el("p", { class: "warn" }, n));
  return sec;
}

// "Check live": the server builds the Foundry request from the card ID and runs the same
// bounded agent and validator; the page only shows the reply, or keeps the recorded run.
function liveControls(cardId) {
  const s = state.live[cardId] || {};
  const box = el("div", { class: "live" });
  const btn = el("button", { type: "button", class: "live-check", "data-live-button": cardId },
    s.pending ? "Checking live with Microsoft Foundry…" : "Check this card live with Microsoft Foundry");
  if (s.pending) { btn.disabled = true; btn.setAttribute("aria-busy", "true"); }
  btn.addEventListener("click", () => runLiveCheck(cardId));
  box.appendChild(btn);
  if (s.message) box.appendChild(el("p", { class: s.ok ? "live-ok" : "warn", "data-live-status": cardId, tabindex: "-1" }, s.message));
  return box;
}

async function runLiveCheck(cardId) {
  if (state.live[cardId] && state.live[cardId].pending) return;
  state.live[cardId] = { pending: true };
  state.cardsKey = "";
  render();
  let result;
  try {
    const resp = await fetch(state.bundle.live_check.path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_id: cardId }), cache: "no-store",
    });
    const reply = await resp.json();
    if (reply.status === "live" && reply.review && reply.review.card_id === cardId) {
      Object.assign(state.bundle.metrics, reply.metrics || {});
      const tokens = reply.usage.input_tokens + reply.usage.output_tokens;
      result = { ok: true, review: reply.review,
        message: `Live result from Microsoft Foundry: ${reply.model_calls} model call(s), ${tokens} tokens, ` +
          `${(reply.latency_ms / 1000).toFixed(1)} s, at ${reply.checked_at_utc}.` };
    } else {
      result = { ok: false, message: (LIVE_REASON[reply.reason] || "The live check is unavailable.") + LIVE_KEEP };
    }
  } catch (err) {
    result = { ok: false, message: "Could not reach the live-check service." + LIVE_KEEP };
  }
  state.live[cardId] = result;
  state.cardsKey = "";
  render();
  const status = document.querySelector(`[data-live-status="${CSS.escape(cardId)}"]`);
  if (status) status.focus(); // the button was replaced: move focus to the result
}

// Identify the focused control inside the cards so a re-render can restore focus to it.
function focusKey(box) {
  const a = document.activeElement;
  if (!a || !box.contains(a)) return null;
  const card = a.closest("[data-card-id]");
  const attr = ["data-event-id", "data-metric-id", "data-evidence"].find((n) => a.hasAttribute(n));
  if (!card || !attr) return null;
  return `[data-card-id="${CSS.escape(card.dataset.cardId)}"] [${attr}="${CSS.escape(a.getAttribute(attr))}"]`;
}

function announceCards(visible) {
  const a = state.announced;
  if (a.mode !== state.mode) { a.mode = state.mode; a.cards = visible.length; return; }
  if (visible.length > a.cards && visible.length) {
    const c = visible[visible.length - 1];
    const when = c.kind === "full_time" ? "full time" : `minute ${minuteOf(c.anchor_clock_s)}`;
    announce(`${KIND_LABEL[c.kind] || c.kind}, ${when}: ${c.title}`);
  }
  a.cards = visible.length;
}

function renderCards() {
  const active = activeCard();
  // A card appears only once its last evidence event has happened (publish_clock_s).
  const visible = cardsForMode().filter((c) => c.publish_clock_s <= state.t);
  announceCards(visible);
  const key = [state.mode, state.variant, state.player ? state.player.player_id : "", visible.length,
    active ? active.card_id : "", state.selected.join(",")].join("|");
  if (key !== state.cardsKey) {
    state.cardsKey = key;
    const box = $("cards");
    const refocus = focusKey(box);
    box.replaceChildren();
    [...visible].reverse().forEach((c) => box.appendChild(renderCard(c, active && c.card_id === active.card_id)));
    $("cards-hint").hidden = visible.length > 0;
    if (refocus) {
      const target = box.querySelector(refocus);
      if (target) target.focus();
    }
  }
  const lt = $("lower-third");
  if (active) {
    lt.hidden = false;
    $("lt-kind").textContent = KIND_LABEL[active.kind] || active.kind;
    const loc = localized(active);
    $("lt-title").textContent = loc ? loc.title : active.title;
    // The caption says why the moment matters; evidence brackets are dropped on the graphic.
    const facts = loc ? loc.facts : active.facts;
    const summary = facts.find((f) => f.role === "why") || facts[0];
    $("lt-fact").textContent = summary ? summary.text.replace(EVIDENCE_RE, "") : "";
  } else {
    lt.hidden = true;
  }
}

// --- Player focus: precomputed running stats (matchlens.players), shown as they stood now ---

// Index of the last step with seq <= seq, or -1 before the first one.
function stepIndex(steps, seq) {
  let lo = 0;
  let hi = steps.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (steps[mid][0] <= seq) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

function featured(card) {
  const p = state.player;
  return !!p && (p.featured_cards[state.mode] || []).includes(card.card_id);
}

function playerStats(seq) {
  const out = {};
  for (const s of state.player.stats) {
    const i = stepIndex(s.steps, seq);
    out[s.name] = {
      stat: s,
      value: i >= 0 ? s.steps[i][1] : s.unit === "count" || s.unit === "xg" ? 0 : null,
      ids: s.steps.slice(0, i + 1).flatMap((step) => step[2]),
    };
  }
  return out;
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function playerSummary(v, cards) {
  const items = [];
  if (v.goals.value) items.push([plural(v.goals.value, "goal", "goals")]);
  if (v.shots.value) items.push([`${plural(v.shots.value, "shot", "shots")}, ${v.shots_on_target.value} on target`]);
  if (v.passes.value) {
    items.push([`${v.passes_completed.value} of ${plural(v.passes.value, "pass", "passes")} completed ` +
      `(${Math.round(100 * v.pass_completion.value)}%)`]);
  }
  if (v.progressive_completed && v.progressive_completed.value) {
    items.push([plural(v.progressive_completed.value, "progressive pass", "progressive passes")]);
  }
  if (v.ball_wins.value) items.push([plural(v.ball_wins.value, "ball win", "ball wins")]);
  if (v.pressures.value) items.push([plural(v.pressures.value, "pressure", "pressures")]);
  if (v.goal_involvements.value) items.push([`involved in ${plural(v.goal_involvements.value, "goal move", "goal moves")}`]);
  if (v.shots.value) items.push([`synthetic xG ${v.xg_heuristic.value.toFixed(2)} (heuristic, not calibrated)`, "heuristic"]);
  if (cards) items.push([`in ${plural(cards, "key-moment card", "key-moment cards")}`]);
  return items;
}

function statValue(entry) {
  const { stat, value } = entry;
  return fmtValue(stat, value);
}

function fmtValue(stat, value) {
  if (value === null || value === undefined) return "—";
  if (stat.unit === "ratio") return `${(100 * value).toFixed(1)}%`;
  if (stat.unit === "xg") return value.toFixed(2);
  if (stat.unit === "xpass") return stat.name === "completed_minus_xpass" && value > 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
  if (stat.unit === "m") return `${value.toFixed(1)} m`;
  if (stat.unit === "s") return `${value.toFixed(1)} s`;
  return String(value);
}

function playerTable(v) {
  const table = el("table", { class: "player-table" });
  const head = el("tr");
  for (const h of ["Stat", "Value", "Kind", "Evidence"]) head.appendChild(el("th", { scope: "col" }, h));
  table.appendChild(el("thead")).appendChild(head);
  const body = table.appendChild(el("tbody"));
  for (const entry of Object.values(v)) {
    const { stat, ids } = entry;
    const tr = el("tr");
    tr.appendChild(el("th", { scope: "row", title: stat.definition }, stat.label));
    tr.appendChild(el("td", {}, statValue(entry)));
    const heuristic = stat.kind === "heuristic";
    tr.appendChild(el("td", { class: `kind-${stat.kind}` }, heuristic ? "heuristic, not calibrated" : "fact"));
    const td = el("td");
    if (ids.length) {
      const b = el("button", { type: "button", class: "chip", "data-stat-id": stat.stat_id, title: stat.stat_id,
        "aria-label": `Show ${plural(ids.length, "event", "events")} for ${stat.label} on the pitch` },
      plural(ids.length, "event", "events"));
      b.addEventListener("click", () => selectEvidence(ids));
      td.appendChild(b);
    } else {
      td.textContent = "—";
    }
    tr.appendChild(td);
    body.appendChild(tr);
  }
  return table;
}

function renderPlayer(idx) {
  const panel = $("player-panel");
  const p = state.player;
  $("legend-player").hidden = !p;
  if (!p) { panel.hidden = true; state.playerKey = ""; return; }
  const seq = idx >= 0 ? state.bundle.events[idx].seq : -1;
  const v = playerStats(seq);
  const cards = cardsForMode().filter((c) => c.publish_clock_s <= state.t && featured(c)).length;
  const key = [p.player_id, state.mode, cards, ...Object.values(v).map((e) => e.ids.length)].join("|");
  if (key === state.playerKey) return;
  state.playerKey = key;
  const a = document.activeElement;
  const refocus = a && panel.contains(a) && a.dataset.statId;
  panel.hidden = false;
  panel.className = `player-panel ${p.team}`;
  const team = state.bundle.meta[p.team].name;
  const h = $("player-heading");
  h.replaceChildren(document.createTextNode(`#${p.number} ${p.name} `), el("span", { class: "team-line" }, `${team} · ${p.position}`));
  $("player-scope").textContent = "Counted from this player's events up to the current replay time; no AI involved.";
  const body = $("player-body");
  body.replaceChildren();
  if (state.mode === "analyst") {
    body.appendChild(el("div", { class: "table-wrap" })).appendChild(playerTable(v));
  } else {
    const items = playerSummary(v, cards);
    if (!items.length) {
      body.appendChild(el("p", { class: "hint" }, "No touches yet."));
    } else {
      const ul = el("ul", { class: "player-facts", "aria-label": "Player statistics so far" });
      for (const [text, cls] of items) ul.appendChild(el("li", cls ? { class: cls } : {}, text));
      body.appendChild(ul);
    }
  }
  if (refocus) {
    const target = panel.querySelector(`[data-stat-id="${CSS.escape(refocus)}"]`);
    if (target) target.focus();
  }
}

// --- Passing: precomputed team steps (matchlens.passing), one per open-play pass ---

const PASS_ROW = { seq: 0, id: 1, team: 2, completed: 3, length: 4, progressive: 6, pressed: 7 };

// The team's totals after its last open-play pass at or before seq, or null before the first.
function passTotals(side, seq) {
  const p = state.bundle.passing;
  const steps = p.teams[side];
  const i = stepIndex(steps, seq);
  if (i < 0) return null;
  const out = {};
  p.stats.forEach((s, k) => { out[s.name] = steps[i][k + 1]; });
  return out;
}

// Which passes a stat is about: the evidence shown on the pitch.
function passFilter(name) {
  const long = state.bundle.passing.thresholds.long_m;
  const done = (r) => r[PASS_ROW.completed];
  if (name === "completed") return done;
  if (name === "long_attempted") return (r) => r[PASS_ROW.length] >= long;
  if (name === "long_completed") return (r) => r[PASS_ROW.length] >= long && done(r);
  if (name === "progressive_attempted") return (r) => r[PASS_ROW.progressive];
  if (name === "progressive_completed") return (r) => r[PASS_ROW.progressive] && done(r);
  if (name === "pressed_attempted") return (r) => r[PASS_ROW.pressed];
  if (name === "pressed_completed") return (r) => r[PASS_ROW.pressed] && done(r);
  return () => true;
}

function passEvidence(side, name, seq) {
  const keep = passFilter(name);
  return state.bundle.passing.passes
    .filter((r) => r[PASS_ROW.team] === side && r[PASS_ROW.seq] <= seq && keep(r))
    .map((r) => r[PASS_ROW.id]);
}

function renderPassing(idx) {
  const p = state.bundle.passing;
  if (!p) return;
  const seq = idx >= 0 ? state.bundle.events[idx].seq : -1;
  const home = passTotals("home", seq);
  const away = passTotals("away", seq);
  const acc = (t) => (t ? `${Math.round(100 * t.completion)}%` : "–");
  $("stat-pass").textContent = `${acc(home)} – ${acc(away)}`;
  const panel = $("passing-panel");
  panel.hidden = state.mode !== "analyst";
  if (panel.hidden) { state.passingKey = ""; return; }
  const key = `${home ? home.attempted : 0}|${away ? away.attempted : 0}`;
  if (key === state.passingKey) return;
  state.passingKey = key;
  const a = document.activeElement;
  const refocus = a && panel.contains(a) && a.dataset.passStat;
  const { home: h, away: w } = state.bundle.meta;
  const table = el("table", { class: "player-table" });
  const head = el("tr");
  for (const t of ["Stat", h.name, w.name, "Kind"]) head.appendChild(el("th", { scope: "col" }, t));
  table.appendChild(el("thead")).appendChild(head);
  const body = table.appendChild(el("tbody"));
  for (const stat of p.stats) {
    const tr = el("tr");
    tr.appendChild(el("th", { scope: "row", title: stat.definition }, stat.label));
    for (const [side, totals] of [["home", home], ["away", away]]) {
      const td = el("td", { class: "num" });
      const value = fmtValue(stat, totals ? totals[stat.name] : null);
      const ids = totals ? passEvidence(side, stat.name, seq) : [];
      if (ids.length) {
        const b = el("button", { type: "button", class: "chip", "data-pass-stat": `${side}.${stat.name}`,
          "aria-label": `${value}: show ${plural(ids.length, "pass", "passes")} for ${stat.label} on the pitch` }, value);
        b.addEventListener("click", () => selectEvidence(ids));
        td.appendChild(b);
      } else {
        td.textContent = value;
      }
      tr.appendChild(td);
    }
    const heuristic = stat.kind === "heuristic";
    tr.appendChild(el("td", { class: `kind-${stat.kind}` }, heuristic ? "heuristic, not calibrated" : "fact"));
    body.appendChild(tr);
  }
  $("passing-body").replaceChildren(table);
  if (refocus) {
    const target = panel.querySelector(`[data-pass-stat="${CSS.escape(refocus)}"]`);
    if (target) target.focus();
  }
}

// Player picker: both squads, grouped by team. ?player=<id> preselects one.
function bindPlayerPicker(params) {
  const data = state.bundle.players;
  if (!data || !data.players.length) return;
  const select = $("player");
  for (const side of ["home", "away"]) {
    const group = el("optgroup", { label: state.bundle.meta[side].name });
    for (const p of data.players.filter((q) => q.team === side)) {
      group.appendChild(el("option", { value: p.player_id }, `#${p.number} ${p.name} (${p.position})`));
    }
    select.appendChild(group);
  }
  const pick = (id) => {
    state.player = data.players.find((p) => p.player_id === id) || null;
    select.value = state.player ? state.player.player_id : "";
    $("legend-player-name").textContent = state.player ? state.player.name : "";
    state.selected = [];
    state.cardsKey = "";
    state.playerKey = "";
  };
  pick(params.get("player"));
  select.addEventListener("change", () => { pick(select.value); render(); });
  $("player-pick").hidden = false;
}

function renderMarkers() {
  const box = $("markers");
  box.replaceChildren();
  const d = state.bundle.duration_s;
  // A goal with its own card already has a "G" button; a second dot would only collide with it.
  const goalCards = new Set(cardsForMode().filter((c) => c.kind === "goal").map((c) => c.anchor_clock_s));
  for (const e of state.bundle.events) {
    if (e.type === "shot" && e.outcome === "goal" && !goalCards.has(e.clock_s)) {
      const tick = el("span", { class: "marker goal-tick", title: `Goal ${e.minute}'` });
      tick.style.left = `${(100 * e.clock_s) / d}%`;
      box.appendChild(tick);
    }
  }
  for (const c of cardsForMode()) {
    const b = el("button", { type: "button", class: `marker ${c.kind}`, "aria-label": `${KIND_LABEL[c.kind]} ${whenOf(c)}: ${c.title}`, title: c.title },
      MARKER_GLYPH[c.kind] || "•");
    b.style.left = `${(100 * c.anchor_clock_s) / d}%`;
    b.addEventListener("click", () => seek(c.start_clock_s));
    box.appendChild(b);
  }
  layoutMarkers();
}

// Stack markers that would overlap at the current width into extra rows.
function layoutMarkers() {
  const box = $("markers");
  const width = box.clientWidth;
  const lanes = []; // x of the last marker placed in each lane
  for (const b of box.querySelectorAll("button.marker")) {
    const x = (parseFloat(b.style.left) / 100) * width;
    let lane = lanes.findIndex((last) => x - last >= MARKER_GAP_PX);
    if (lane < 0) { lane = lanes.length; lanes.push(x); } else lanes[lane] = x;
    b.style.top = `${lane * MARKER_LANE_REM}rem`;
  }
  box.style.height = `${Math.max(1, lanes.length) * MARKER_LANE_REM}rem`;
}

function render() {
  const idx = indexAt(state.t);
  renderScoreboard(idx);
  renderPitch(idx);
  renderPassing(idx);
  renderPlayer(idx);
  renderCards();
  $("seek").value = String(Math.floor(state.t));
  $("seek").setAttribute("aria-valuetext", `${fmtClock(state.t)}, ${$("period").textContent}`);
  const fullTime = state.t >= state.bundle.duration_s;
  if (fullTime && !state.announced.fullTime) {
    const { home, away } = state.bundle.meta;
    announce(`Full time: ${home.name} ${$("home-score").textContent}, ${away.name} ${$("away-score").textContent}`);
  }
  state.announced.fullTime = fullTime;
  $("play").textContent = state.playing ? "⏸" : "▶";
  $("play").setAttribute("aria-label", state.playing ? "Pause" : "Play");
}

function seek(t) {
  state.t = Math.max(0, Math.min(state.bundle.duration_s, t));
  render();
}

function setMode(mode) {
  state.mode = mode;
  for (const b of document.querySelectorAll(".mode-toggle button")) {
    const on = b.dataset.mode === mode;
    b.setAttribute("aria-checked", String(on));
    b.tabIndex = on ? 0 : -1; // roving tabindex: one tab stop for the radio group
  }
  document.body.dataset.mode = mode;
  $("personalize").hidden = !(state.bundle && state.bundle.personalized && mode === "casual");
  $("broadcast").href = `broadcast.html?mode=${mode}&rundown=1&bg=dark${seedQuery()}`;
  $("inspector").hidden = mode !== "analyst";
  state.selected = [];
  state.spotlight = null;
  state.cardsKey = "";
  state.playerKey = "";
  state.passingKey = "";
  renderMarkers();
  render();
}

function setPlaying(on) {
  if (on && state.t >= state.bundle.duration_s) state.t = 0;
  state.playing = on;
  state.lastFrame = null;
  render();
}

function frame(now) {
  if (state.playing) {
    if (state.lastFrame !== null) {
      state.t += ((now - state.lastFrame) / 1000) * state.speed;
      if (state.t >= state.bundle.duration_s) { state.t = state.bundle.duration_s; state.playing = false; }
    }
    state.lastFrame = now;
    render();
  }
  requestAnimationFrame(frame);
}

function jumpMoment(dir) {
  const cards = cardsForMode();
  const target = dir > 0
    ? cards.find((c) => c.publish_clock_s > state.t + 0.5)
    : [...cards].reverse().find((c) => c.publish_clock_s < state.t - 0.5);
  if (target) seek(target.publish_clock_s);
}

// Demo shortcut: the analyst card whose claims the validator checked (and the agent fixed).
function showAiCheck(target) {
  setPlaying(false);
  setMode("analyst");
  state.spotlight = target.card_id;
  seek(target.publish_s);
  const sec = document.querySelector(`[data-review="${CSS.escape(target.card_id)}"]`);
  if (!sec) return;
  const side = sec.closest(".side");
  if (side.scrollHeight > side.clientHeight + 1) { // desktop: scroll the card panel only
    side.scrollTop += sec.getBoundingClientRect().top - side.getBoundingClientRect().top - 8;
  } else {
    sec.scrollIntoView({ block: "start" });
  }
  sec.focus({ preventScroll: true });
}

function bindShortcuts(h) {
  const add = (id, label, action, play) => {
    const b = $(id);
    b.replaceChildren(el("span", { "aria-hidden": "true" }, play ? "▶ " : "✓ "), document.createTextNode(label));
    b.hidden = false;
    b.addEventListener("click", action);
  };
  if (h.turning_point) add("jump-turning", h.turning_point.label, () => { seek(h.turning_point.start_s); setPlaying(true); }, true);
  if (h.ai_check) add("jump-ai", h.ai_check.label, () => showAiCheck(h.ai_check), false);
  $("shortcuts").hidden = !(h.turning_point || h.ai_check);
}

function bindControls() {
  $("play").addEventListener("click", () => setPlaying(!state.playing));
  $("restart").addEventListener("click", () => { seek(0); setPlaying(false); });
  $("prev").addEventListener("click", () => jumpMoment(-1));
  $("next").addEventListener("click", () => jumpMoment(1));
  $("speed").addEventListener("change", (ev) => { state.speed = Number(ev.target.value); });
  $("seek").addEventListener("input", (ev) => seek(Number(ev.target.value)));
  window.addEventListener("resize", layoutMarkers);
  const radios = [...document.querySelectorAll(".mode-toggle button")];
  for (const b of radios) {
    b.addEventListener("click", () => setMode(b.dataset.mode));
    b.addEventListener("keydown", (ev) => {
      const i = radios.indexOf(b);
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[ev.key];
      let next = null;
      if (step) next = radios[(i + step + radios.length) % radios.length];
      else if (ev.key === "Home") next = radios[0];
      else if (ev.key === "End") next = radios[radios.length - 1];
      if (!next) return;
      ev.preventDefault();
      setMode(next.dataset.mode);
      next.focus();
    });
  }
  document.addEventListener("keydown", (ev) => {
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
    if (ev.target.closest && ev.target.closest(NATIVE_KEYS)) return;
    if (ev.code === "Space") { ev.preventDefault(); setPlaying(!state.playing); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); seek(state.t + 30); }
    else if (ev.key === "ArrowLeft") { ev.preventDefault(); seek(state.t - 30); }
  });
}

// Language and club for the casual cards; only variants the bundle has can be chosen.
function bindPersonalize(params) {
  const p = state.bundle.personalized;
  if (!p) return;
  const has = (lang, focus) => (lang === "en" && focus === "neutral") || `${lang}|${focus}` in p.variants;
  const lang = $("p-lang");
  const focus = $("p-focus");
  $("p-focus-home").textContent = state.bundle.meta.home.name;
  $("p-focus-away").textContent = state.bundle.meta.away.name;
  const sync = () => {
    for (const o of lang.options) o.disabled = !["neutral", "home", "away"].some((f) => has(o.value, f));
    for (const o of focus.options) o.disabled = !has(lang.value, o.value);
    if (!has(lang.value, focus.value)) focus.value = [...focus.options].find((o) => !o.disabled).value;
    state.variant = `${lang.value}|${focus.value}`;
    state.cardsKey = "";
  };
  if (params.has("lang")) lang.value = params.get("lang");
  if (!lang.value || lang.selectedOptions[0].disabled) lang.value = "en";
  if (params.has("follow")) focus.value = params.get("follow");
  if (!focus.value) focus.value = "neutral";
  sync();
  for (const s of [lang, focus]) s.addEventListener("change", () => { sync(); render(); });
}

// Match picker from data/matches.json. Labels name the seed only: no score, no spoilers.
function bindMatchPicker(index, entry) {
  if (!index || index.matches.length < 2) return;
  const select = $("match");
  index.matches.forEach((m, i) => {
    const label = `Seed ${m.seed}` + (i === 0 ? " (demo)" : " (untuned)");
    const opt = el("option", { value: String(m.seed) }, label);
    if (m === entry) opt.selected = true;
    select.appendChild(opt);
  });
  select.addEventListener("change", () => {
    const url = new URL(location.href);
    url.search = "";
    if (select.value !== String(index.matches[0].seed)) url.searchParams.set("seed", select.value);
    if (state.mode === "analyst") url.searchParams.set("mode", "analyst");
    location.assign(url.toString()); // a fresh page per match: no state carries over
  });
  $("match-pick").hidden = false;
}

function seedQuery() {
  return state.entry && state.index && state.entry !== state.index.matches[0] ? `&seed=${state.entry.seed}` : "";
}

function init(bundle, index, entry) {
  state.bundle = bundle;
  state.index = index;
  state.entry = entry;
  for (const e of bundle.events) state.eventsById.set(e.event_id, e);
  const { home, away, match_id: matchId } = bundle.meta;
  for (const [id, text] of [["home-name", home.name], ["away-name", away.name], ["legend-home", home.name], ["legend-away", away.name]]) {
    $(id).textContent = text;
  }
  $("seek").max = String(bundle.duration_s);
  $("download").setAttribute("download", `matchlens-overlays-${matchId}.json`);
  if (entry) $("download").setAttribute("href", entry.overlays);
  const extra = index && entry && entry !== index.matches[0];
  const offline = bundle.provider === "offline_template";
  $("disclosure").textContent =
    `Synthetic match ${matchId} (seed ${bundle.meta.seed}) between fictional teams. ` +
    "✓ facts are rendered from verified, structured event data and cite event/metric IDs. " +
    "xG is a synthetic heuristic, not empirically calibrated. " +
    (offline
      ? "Narrative provider: offline deterministic template — not an Azure response."
      : "Narrative wording comes from Azure OpenAI and is NOT verified; the ✓ facts are authoritative.") +
    (Object.values(bundle.reviews || {}).some((r) => r.proposer === "recorded_foundry_agent")
      ? " Analyst claim checks replay a recorded Microsoft Foundry agent run (not a live call); " +
        "every claim was re-checked by the deterministic validator when this page was built."
      : "") +
    (extra
      ? " This match was not picked or tuned for the demo; its claim checks use the offline proposer (no AI)."
      : "") +
    (bundle.live_check
      ? " Analyst claim checks can also be run live: the button calls Microsoft Foundry through a " +
        "server-side function (the key never reaches the browser), within per-visitor and daily limits."
      : "");
  drawMarkings();
  $("stat-pass-tile").hidden = !bundle.passing;
  bindMatchPicker(index, entry);
  bindControls();
  bindShortcuts(bundle.highlights || {});
  const params = new URLSearchParams(location.search);
  bindPersonalize(params);
  bindPlayerPicker(params);
  const speed = Number(params.get("speed"));
  if ([15, 30, 60, 120, 240].includes(speed)) { state.speed = speed; $("speed").value = String(speed); }
  setMode(params.get("mode") === "analyst" ? "analyst" : "casual");
  if (params.has("t")) seek(Number(params.get("t")) || 0);
  requestAnimationFrame(frame);
  document.body.dataset.ready = "true";
}

window.matchlens = { state, seek, setMode, setPlaying };

// The match index is optional (older exports have none); without it, the default bundle.
async function load() {
  const getJson = async (path) => {
    const r = await fetch(path, { cache: "no-cache" }); // revalidate: data changes per deploy
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  };
  let index = null;
  try {
    index = await getJson("data/matches.json");
    if (!index || !Array.isArray(index.matches) || !index.matches.length) index = null;
  } catch (err) {
    index = null;
  }
  const want = new URLSearchParams(location.search).get("seed");
  const entry = index ? index.matches.find((m) => String(m.seed) === want) || index.matches[0] : null;
  init(await getJson(entry ? entry.file : "data/bundle.json"), index, entry);
}

load().catch((err) => { $("disclosure").textContent = `Could not load match data: ${err.message}`; });
