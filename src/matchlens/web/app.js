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
const NATIVE_KEYS = "input, select, textarea, button, a[href], summary, [role=radio], [role=button], .side";

const $ = (id) => document.getElementById(id);
const state = {
  bundle: null, t: 0, playing: false, speed: 60, mode: "casual",
  selected: [], lastFrame: null, cardsKey: "", eventsById: new Map(),
  openChains: new Set(), openReviews: new Set(), spotlight: null, // UI state kept across card re-renders
  announced: { cards: -1, mode: "", fullTime: false }, announceTimer: null,
  live: {}, // card_id -> { pending } | { ok, message, review? }: live Foundry checks this visit
  index: null, entry: null, // data/matches.json and the match shown (null on older exports)
  variant: "en|neutral", // personalizer variant "lang|focus" (casual cards); en|neutral = source
  player: null, playerKey: "", // focused player (bundle.players record) and its rendered state
  passingKey: "",
  recapOpen: false, recapKey: "", recapReturn: null, // match recap panel and where focus goes on close
  view: "live", lineupKey: "", // pitch view: "live" (ball events) or "lineups" (both squads)
  barsKey: "", teamTotals: null, // casual comparison bars; team totals summed from the player stats
  openMore: new Set(), // casual cards whose "More" is open
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

function drawMarkings(g = $("markings")) {
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

  // Casual: the title and why the moment matters; the other checked lines wait under "More".
  const lines = (loc ? loc.facts : card.facts).filter((f) => f.role !== "chain");
  const lead = analyst ? null : lines.find((f) => f.role === "why") || lines[0];
  const facts = el("ul", { class: "facts", "aria-label": "Verified facts" });
  (lead ? [lead] : lines).forEach((f) => facts.appendChild(factItem(f, analyst)));
  art.appendChild(facts);
  const rest = lead ? lines.filter((f) => f !== lead) : [];
  const note = loc ? el("p", { class: "hint personalized-note" }, personalizedNote(loc)) : null;
  if (rest.length || note) {
    const det = el("details", { class: "more" });
    det.open = state.openMore.has(card.card_id);
    det.addEventListener("toggle", () => {
      if (det.open) state.openMore.add(card.card_id); else state.openMore.delete(card.card_id);
    });
    det.appendChild(el("summary", {}, rest.length ? `More (${rest.length})` : "About this wording"));
    if (rest.length) {
      const more = el("ul", { class: "facts", "aria-label": "More verified facts" });
      rest.forEach((f) => more.appendChild(factItem(f, analyst)));
      det.appendChild(more);
    }
    if (note) det.appendChild(note);
    art.appendChild(det);
  }
  if (card.kind === "full_time" && state.bundle.recap) {
    const open = el("button", { type: "button", class: "recap-open", "data-recap-open": card.card_id });
    open.append(el("span", { "aria-hidden": "true" }, "▤ "), "Match recap: copy or download");
    open.addEventListener("click", openRecap);
    art.appendChild(open);
  }

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
    // Template wording is the design here, not a fallback: say what it is, not what it is not.
    nar.appendChild(el("span", {
      class: `status ${unverified ? "unverified" : "template"}`,
      title: unverified ? n.label : "Rendered from match events by deterministic templates; no model wrote this wording.",
    }, unverified ? "AI wording · NOT VERIFIED" : "Template wording · no AI"));
  }
  const duplicate = n.text === card.facts.map((f) => f.text).join(" ");
  // A rewritten card hides the English narrative: the note above already names its wording.
  if (!loc && unverified) {
    nar.appendChild(el("p", { class: "text" }, n.text));
  } else if (!loc && !duplicate && analyst) {
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

// Claim check: a plain summary first (built in Python from the verdicts), then every proposed
// claim with the validator's verdict, attempt by attempt, folded unless this is the demo's
// spotlighted card. Only ✓ claims and corrections rendered from computed metrics are
// published; the browser decides nothing.
function renderReview(r, spotlight) {
  const sec = el("section", { class: `review${spotlight ? " spotlight" : ""}`, "aria-label": "Claim check", tabindex: "-1", "data-review": r.card_id });
  sec.appendChild(el("h4", {}, "Claim check"));
  if (r.explanation && r.explanation.length) sec.appendChild(el("p", { class: "explain" }, r.explanation.join(" ")));
  sec.appendChild(el("p", { class: "hint" }, r.proposer_label));
  if (r.fallback_reason) sec.appendChild(el("p", { class: "warn" }, r.fallback_reason));
  sec.appendChild(el("p", { class: "summary" }, reviewSummary(r)));
  const det = el("details", { class: "claims" });
  det.open = spotlight || state.openReviews.has(r.card_id);
  det.addEventListener("toggle", () => {
    if (det.open) state.openReviews.add(r.card_id); else state.openReviews.delete(r.card_id);
  });
  det.appendChild(el("summary", {}, "Claim by claim: the validator's reasons and the response IDs"));
  const calls = r.attempts.flatMap((a) => a.calls);
  if (calls.length) {
    det.appendChild(el("p", { class: "hint" },
      "The agent proposes structured claims (metric, value, event IDs) using read-only tools " +
      "that see the match only up to this card; the validator accepts or rejects each one, and " +
      "rejections go back to the agent once. Only verified claims are published."));
    const tools = calls.reduce((n, c) => n + c.tool_calls.length, 0);
    const ids = el("details", { class: "calls" });
    ids.appendChild(el("summary", {},
      `${r.attempts.length} attempt(s), ${calls.length} model call(s), ${tools} tool call(s): response IDs`));
    ids.appendChild(el("p", { class: "hint" }, calls.map((c) => c.response_id).join(", ")));
    det.appendChild(ids);
  }
  const published = new Set(r.published.map((f) => f.text));
  const groups = r.attempts.map((_, i) => [
    i === 0 ? "Attempt 1 — proposed claims" : `Attempt ${i + 1} — after the validator's feedback`,
    r.verdicts.filter((v) => v.source !== INJECTED && v.attempt === i + 1),
  ]);
  groups.push(["Test claim — deliberately wrong", r.verdicts.filter((v) => v.source === INJECTED)]);
  for (const [title, verdicts] of groups) {
    if (!verdicts.length) continue;
    det.appendChild(el("h5", {}, title));
    const list = el("ul", { class: "verdicts" });
    verdicts.forEach((v) => list.appendChild(verdictItem(r, v, published)));
    det.appendChild(list);
  }
  sec.appendChild(det);
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
  if (active && state.view === "live") {
    lt.hidden = false;
    $("lt-kind").textContent = KIND_LABEL[active.kind] || active.kind;
    // Template wording only, never the personalizer's (it carries no AI label here); the cards
    // keep their language. The caption says why the moment matters, without evidence brackets.
    $("lt-title").textContent = active.title;
    const summary = active.facts.find((f) => f.role === "why") || active.facts[0];
    $("lt-fact").textContent = summary ? summary.text.replace(EVIDENCE_RE, "") : "";
  } else {
    lt.hidden = true;
  }
}

// --- Match recap: lines of the casual cards (matchlens.recap), never new wording ---

const RECAP_LINE_STATUS = { verified: "verified_fact", rewrite: "ai_rewrite_checked", kept: "english_fact_kept" };
const RECAP_GOALS = { en: "Goals", de: "Tore", uk: "Голи" }; // section label (UI text, not a fact)
const recapLang = () => (state.mode === "casual" ? state.variant.split("|")[0] : "en");

// One recap line in the chosen language and club, as localized() does for a card: the checked
// rewrite, or the English fact when the rewrite was rejected.
function recapLine(line, role) {
  const f = {
    text: line.text, kind: line.kind, event_ids: line.event_ids, metric_ids: line.metric_ids,
    role, card_id: line.card_id, index: line.index, status: RECAP_LINE_STATUS.verified,
  };
  const p = state.bundle.personalized;
  if (state.mode !== "casual" || !p || state.variant === "en|neutral") return f;
  const v = p.variants[state.variant];
  const rec = v && v.cards[line.card_id];
  const ln = rec && rec.lines.find((l) => l.index === line.index);
  if (!ln) return { ...f, lang: "en" };
  if (ln.status !== "verified") return { ...f, text: ln.text, lang: "en", status: RECAP_LINE_STATUS.kept };
  return { ...f, text: ln.text, lang: v.lang, rewritten: true, status: RECAP_LINE_STATUS.rewrite };
}

function recapView() {
  const r = state.bundle.recap;
  return {
    headline: recapLine(r.headline, "headline"),
    summary: r.summary.map((ln) => recapLine(ln, "why")),
    goals: r.goals.map((ln) => recapLine(ln, "what")),
  };
}

// Who wrote the shown wording: empty for the English source (verified facts only).
function recapNote(view) {
  const p = state.bundle.personalized;
  if (state.mode !== "casual" || !p || state.variant === "en|neutral") return "";
  const lines = [view.headline, ...view.summary, ...view.goals];
  const rewritten = lines.filter((f) => f.rewritten).length;
  const kept = lines.length - rewritten;
  const run = p.proposers.includes("recorded_foundry_personalizer") ? "recorded run" : "live";
  const critic = (p.critics || []).length ? " and meaning reviewed by a critic agent (also AI)" : "";
  return `${rewritten} of ${lines.length} lines are AI rewrites by the Microsoft Foundry personalizer ` +
    `(${run}), with numbers and names checked against the English facts${critic}` +
    (kept ? `; ${kept} stay in English because the rewrite was rejected.` : ".");
}

// Plain text for copying; for the English source it equals matchlens.recap.format_recap.
function recapText(view = recapView()) {
  const r = state.bundle.recap;
  const parts = [view.headline.text, view.summary.map((f) => f.text).join(" ")];
  if (view.goals.length) parts.push([RECAP_GOALS[recapLang()] || "Goals", ...view.goals.map((f) => `- ${f.text}`)].join("\n"));
  const note = recapNote(view);
  if (note) parts.push(note);
  parts.push(`Synthetic match ${r.match_id} (seed ${r.seed}) between fictional teams. ` +
    "Every line is checked against the match events; synthetic xG is a heuristic, not calibrated.");
  return `${parts.join("\n\n")}\n`;
}

function renderRecap() {
  const r = state.bundle.recap;
  if (r && state.recapOpen && state.t < r.publish_clock_s) state.recapOpen = false; // no recap before full time
  const shown = !!r && state.recapOpen;
  $("recap").hidden = !shown;
  if (!shown) { state.recapKey = ""; return; }
  const key = [state.mode, state.variant, state.selected.join(",")].join("|");
  if (key === state.recapKey) return;
  state.recapKey = key;
  const body = $("recap-body");
  const a = document.activeElement;
  const attr = a && body.contains(a) && ["data-event-id", "data-metric-id"].find((n) => a.hasAttribute(n));
  const refocus = attr ? `[${attr}="${CSS.escape(a.getAttribute(attr))}"]` : null;
  const view = recapView();
  const analyst = state.mode === "analyst";
  body.replaceChildren(el("h3", view.headline.lang ? { lang: view.headline.lang } : {}, view.headline.text));
  const summary = el("ul", { class: "facts", "aria-label": "Summary" });
  view.summary.forEach((f) => summary.appendChild(factItem(f, analyst)));
  body.appendChild(summary);
  if (view.goals.length) {
    const lang = RECAP_GOALS[recapLang()] ? recapLang() : "en";
    body.appendChild(el("h4", { lang }, RECAP_GOALS[lang]));
    const goals = el("ul", { class: "facts", "aria-label": "Goals" });
    view.goals.forEach((f) => goals.appendChild(factItem(f, analyst)));
    body.appendChild(goals);
  }
  const note = recapNote(view);
  if (note) body.appendChild(el("p", { class: "hint personalized-note" }, note));
  if (refocus) {
    const target = body.querySelector(refocus);
    if (target) target.focus();
  }
}

// Scroll the card panel (desktop) or the page (narrow layout) to a node and focus it.
function reveal(node) {
  const side = node.closest(".side");
  if (side.scrollHeight > side.clientHeight + 1) {
    side.scrollTop += node.getBoundingClientRect().top - side.getBoundingClientRect().top - 8;
  } else {
    node.scrollIntoView({ block: "start" });
  }
  node.focus({ preventScroll: true });
}

function openRecap(ev) {
  const r = state.bundle.recap;
  state.recapReturn = ev && ev.currentTarget && ev.currentTarget.id ? ev.currentTarget.id : "jump-recap";
  setPlaying(false);
  state.recapOpen = true;
  state.recapKey = "";
  $("recap-status").textContent = "";
  seek(Math.max(state.t, r.publish_clock_s));
  reveal($("recap"));
}

function closeRecap() {
  state.recapOpen = false;
  render();
  const back = $(state.recapReturn) || $("jump-recap");
  if (back && !back.hidden) back.focus();
}

// A visible status line under the buttons, also spoken through the page's one status region.
function recapStatus(text) {
  $("recap-status").textContent = text;
  announce(text);
}

async function copyRecap() {
  try {
    await navigator.clipboard.writeText(recapText());
    recapStatus("Recap copied as plain text.");
  } catch (err) {
    recapStatus("This browser blocked copying; use Recap JSON instead.");
  }
}

// The shown recap with its sources: one entry per line, with event and metric IDs.
function downloadRecap() {
  const r = state.bundle.recap;
  const { home, away } = state.bundle.meta;
  const [lang, focus] = state.mode === "casual" ? state.variant.split("|") : ["en", "neutral"];
  const view = recapView();
  const line = (f) => ({
    text: f.text, lang: f.lang || "en", status: f.status, kind: f.kind,
    event_ids: f.event_ids, metric_ids: f.metric_ids, card_id: f.card_id, index: f.index,
  });
  const doc = {
    schema: "matchlens.recap-export/v1",
    match_id: r.match_id, seed: r.seed, synthetic: true,
    home: home.name, away: away.name, final_score: state.bundle.final_score,
    published_at_match_s: r.publish_clock_s, lang, focus,
    wording: recapNote(view) || "Verified facts rendered from structured event data; no AI wording.",
    headline: line(view.headline), summary: view.summary.map(line), goals: view.goals.map(line),
    text: recapText(view),
  };
  const url = URL.createObjectURL(new Blob([`${JSON.stringify(doc, null, 2)}\n`], { type: "application/json" }));
  const a = el("a", { href: url, download: `matchlens-recap-${r.match_id}-${lang}-${focus}.json` });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  recapStatus("Recap JSON downloaded: every line with its event and metric IDs.");
}

function bindRecap() {
  if (!state.bundle.recap) return;
  $("recap-copy").addEventListener("click", copyRecap);
  $("recap-download").addEventListener("click", downloadRecap);
  $("recap-close").addEventListener("click", closeRecap);
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

// --- Casual comparison bars: one bar per stat, as it stood at the replay time ---

// Team totals summed from the players' running stats (matchlens.players): [seq, total] after
// each contributing event, so a bar never runs ahead of the replay.
function teamTotals(name) {
  const out = {};
  for (const side of ["home", "away"]) {
    const deltas = [];
    for (const p of state.bundle.players.players.filter((q) => q.team === side)) {
      const stat = p.stats.find((x) => x.name === name);
      let prev = 0;
      for (const [seq, value] of stat ? stat.steps : []) { deltas.push([seq, value - prev]); prev = value; }
    }
    deltas.sort((a, b) => a[0] - b[0]);
    const steps = [];
    let total = 0;
    for (const [seq, d] of deltas) {
      total += d;
      if (steps.length && steps[steps.length - 1][0] === seq) steps[steps.length - 1][1] = total;
      else steps.push([seq, total]);
    }
    out[side] = steps;
  }
  return out;
}

function totalAt(steps, seq) {
  const i = stepIndex(steps, seq);
  return i >= 0 ? steps[i][1] : 0;
}

const BARS = [
  ["shots", "Shots"], ["on_target", "On target"], ["xg", "Synthetic xG"],
  ["pass_acc", "Pass accuracy"], ["pressures", "Pressures"], ["ball_wins", "Ball wins"],
];

function barValues(seq) {
  const s = stepAt(seq) || {};
  const t = state.teamTotals;
  const out = {};
  for (const side of ["home", "away"]) {
    const pass = state.bundle.passing ? passTotals(side, seq) : null;
    out[side] = {
      shots: s[`${side}.shots`] || 0,
      on_target: totalAt(t.shots_on_target[side], seq),
      xg: s[`${side}.xg_heuristic`] || 0,
      pass_acc: pass ? pass.completion : null,
      pressures: totalAt(t.pressures[side], seq),
      ball_wins: totalAt(t.ball_wins[side], seq),
    };
  }
  return out;
}

function fmtBar(key, x) {
  if (x === null) return "–";
  if (key === "xg") return x.toFixed(2);
  if (key === "pass_acc") return `${Math.round(100 * x)}%`;
  return String(x);
}

function renderBars(idx) {
  const box = $("bars");
  if (box.hidden) return;
  const seq = idx >= 0 ? state.bundle.events[idx].seq : -1;
  const v = barValues(seq);
  const key = BARS.map(([k]) => `${v.home[k]}:${v.away[k]}`).join("|");
  if (key === state.barsKey) return;
  state.barsKey = key;
  const { home, away } = state.bundle.meta;
  const head = el("div", { class: "bars-head", "aria-hidden": "true" });
  head.append(el("span", { class: "home" }, home.name), el("span", { class: "away" }, away.name));
  const rows = BARS.map(([k, label]) => {
    const [h, a] = [v.home[k], v.away[k]];
    const heuristic = k === "xg";
    const row = el("div", { class: `bar-row${heuristic ? " heuristic" : ""}`, "data-bar": k });
    const lab = el("span", { class: "bar-label", "aria-hidden": "true" }, label);
    if (heuristic) lab.title = "Synthetic heuristic, not empirically calibrated";
    row.append(el("span", { class: "bar-val home", "aria-hidden": "true" }, fmtBar(k, h)), lab,
      el("span", { class: "bar-val away", "aria-hidden": "true" }, fmtBar(k, a)));
    const sum = (h || 0) + (a || 0);
    const share = sum > 0 ? (h || 0) / sum : 0.5;
    const bar = el("div", { class: `bar${sum ? "" : " empty"}`, "aria-hidden": "true" });
    const bh = el("span", { class: "bar-home" });
    const ba = el("span", { class: "bar-away" });
    bh.style.width = `${(100 * share).toFixed(1)}%`;
    ba.style.width = `${(100 * (1 - share)).toFixed(1)}%`;
    bar.append(bh, ba);
    const note = heuristic ? " (synthetic heuristic, not calibrated)" : "";
    row.append(bar, el("span", { class: "sr-only" }, `${label}${note}: ${home.name} ${fmtBar(k, h)}, ${away.name} ${fmtBar(k, a)}`));
    return row;
  });
  box.replaceChildren(head, ...rows);
}

// --- Lineups: both squads in their formation (match metadata), with goals as of now ---

const POSITION_NAME = { GK: "goalkeeper", DF: "defender", MF: "midfielder", FW: "forward" };
const LINE_X = { GK: 4.5, DF: 16, MF: 31, FW: 43.5 }; // the home half; the away side is mirrored
// "E. Dunmore": the generated squads share surnames, so the initial tells two players apart.
const shortName = (name) => { const parts = name.split(" "); return `${parts[0][0]}. ${parts.slice(1).join(" ")}`; };

function formationOf(side) {
  const n = (pos) => state.bundle.meta[side].players.filter((p) => p.position === pos).length;
  return ["DF", "MF", "FW"].map(n).join("-");
}

function goalsAt(seq) {
  const out = new Map();
  for (const p of state.bundle.players.players) {
    const stat = p.stats.find((x) => x.name === "goals");
    const i = stat ? stepIndex(stat.steps, seq) : -1;
    if (i >= 0 && stat.steps[i][1]) out.set(p.player_id, stat.steps[i][1]);
  }
  return out;
}

function lineupPlayer(p, side, x, y, goals, followed) {
  const label = `#${p.number} ${p.name}, ${POSITION_NAME[p.position] || p.position}` +
    `${goals ? `, ${plural(goals, "goal", "goals")}` : ""}`;
  const g = svg("g", { class: `lp ${side}${followed ? " followed" : ""}`, role: "button", tabindex: "0",
    "aria-pressed": String(followed), "aria-label": `${label}: follow this player`, "data-player-id": p.player_id });
  g.appendChild(svg("title")).textContent = label;
  g.appendChild(svg("circle", { cx: x, cy: y, r: 2.8, class: "lp-dot" }));
  g.appendChild(svg("text", { x, y: y + 0.95, class: "lp-num" })).textContent = String(p.number);
  const short = shortName(p.name);
  g.appendChild(svg("text", { x, y: y + 5.6, class: `lp-name${short.length > 13 ? " long" : ""}` })).textContent = short;
  for (let k = 0; k < goals; k++) g.appendChild(svg("circle", { cx: x + 2.7 + 1.6 * k, cy: y - 2.7, r: 0.95, class: "lp-goal" }));
  const toggle = () => { pickPlayer(followed ? "" : p.player_id); render(); };
  g.addEventListener("click", toggle);
  g.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); }
  });
  return g;
}

function renderLineup(idx) {
  if (state.view !== "lineups") return;
  const seq = idx >= 0 ? state.bundle.events[idx].seq : -1;
  const goals = goalsAt(seq);
  const followed = state.player ? state.player.player_id : "";
  const key = ["lineup", followed, ...goals.entries()].join("|"); // never "": that means not drawn
  if (key === state.lineupKey) return;
  state.lineupKey = key;
  const box = $("lineup-players");
  const a = document.activeElement;
  const refocus = a && box.contains(a) ? a.dataset.playerId : null;
  box.replaceChildren();
  for (const side of ["home", "away"]) {
    const squad = state.bundle.meta[side].players;
    for (const pos of ["GK", "DF", "MF", "FW"]) {
      const line = squad.filter((p) => p.position === pos);
      line.forEach((p, i) => {
        let x = LINE_X[pos];
        let y = (PITCH_H * (i + 1)) / (line.length + 1);
        if (side === "away") { x = PITCH_W - x; y = PITCH_H - y; }
        box.appendChild(lineupPlayer(p, side, x, y, goals.get(p.player_id) || 0, p.player_id === followed));
      });
    }
  }
  if (refocus) {
    const target = box.querySelector(`[data-player-id="${CSS.escape(refocus)}"]`);
    if (target) target.focus();
  }
}

function setView(view) {
  state.view = view;
  for (const [id, v] of [["view-live", "live"], ["view-lineups", "lineups"]]) $(id).setAttribute("aria-pressed", String(view === v));
  // SVG elements have no .hidden property: toggle the attribute itself.
  for (const [id, on] of [["pitch", "live"], ["live-legend", "live"], ["lineup", "lineups"], ["lineup-legend", "lineups"]]) {
    $(id).toggleAttribute("hidden", view !== on);
  }
  state.lineupKey = "";
  if (state.bundle) render();
}

function bindViewToggle(params) {
  if (!state.bundle.players) return;
  drawMarkings(document.querySelector("#lineup .lineup-markings"));
  const { home, away } = state.bundle.meta;
  $("lineup-home").textContent = `${home.name} · ${formationOf("home")}`;
  $("lineup-away").textContent = `${away.name} · ${formationOf("away")}`;
  $("lineup").setAttribute("aria-label",
    `Lineups: ${home.name} ${formationOf("home")}, ${away.name} ${formationOf("away")}. Select a player to follow them.`);
  $("view-live").addEventListener("click", () => setView("live"));
  $("view-lineups").addEventListener("click", () => setView("lineups"));
  $("view-toggle").hidden = false;
  if (params.get("view") === "lineups") setView("lineups");
}

// The followed player (bundle.players record), or none; the picker and the lineup share it.
function pickPlayer(id) {
  const data = state.bundle.players;
  state.player = (data && data.players.find((p) => p.player_id === id)) || null;
  $("player").value = state.player ? state.player.player_id : "";
  $("legend-player-name").textContent = state.player ? state.player.name : "";
  state.selected = [];
  state.cardsKey = "";
  state.playerKey = "";
  state.lineupKey = "";
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
  pickPlayer(params.get("player"));
  select.addEventListener("change", () => { pickPlayer(select.value); render(); });
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
  renderLineup(idx);
  renderBars(idx);
  renderPassing(idx);
  renderPlayer(idx);
  renderCards();
  renderRecap();
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
  const bars = !!state.teamTotals; // casual: comparison bars; analyst: the detailed tiles
  $("bars").hidden = !(bars && mode === "casual");
  $("stats").hidden = bars && mode === "casual";
  state.barsKey = "";
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
  if (sec) reveal(sec);
}

function bindShortcuts(h, recap) {
  const add = (id, label, action, glyph) => {
    const b = $(id);
    b.replaceChildren(el("span", { "aria-hidden": "true" }, glyph), document.createTextNode(label));
    b.hidden = false;
    b.addEventListener("click", action);
  };
  if (h.turning_point) add("jump-turning", h.turning_point.label, () => { seek(h.turning_point.start_s); setPlaying(true); }, "▶ ");
  if (h.ai_check) add("jump-ai", h.ai_check.label, () => showAiCheck(h.ai_check), "✓ ");
  if (recap) add("jump-recap", "Match recap (full time)", openRecap, "▤ ");
  $("shortcuts").hidden = !(h.turning_point || h.ai_check || recap);
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
      ? "English card wording comes from deterministic templates, not from a model (no AI)."
      : "Narrative wording comes from Azure OpenAI and is NOT verified; the ✓ facts are authoritative.") +
    (Object.values(bundle.reviews || {}).some((r) => r.proposer === "recorded_foundry_agent")
      ? " Analyst claim checks replay a recorded Microsoft Foundry agent run (not a live call); " +
        "every claim was re-checked by the deterministic validator when this page was built."
      : "") +
    (extra
      ? " This match was not picked or tuned for the demo; its claim checks use the offline proposer (no AI)."
      : "") +
    (bundle.stream
      ? ` Checked when this page was built: fed one event at a time to the stream processor ` +
        `(matchlens stream), this match gives exactly these ${bundle.stream.cards} cards, each ` +
        "published as its last evidence event arrives."
      : "") +
    (bundle.live_check
      ? " Analyst claim checks can also be run live: the button calls Microsoft Foundry through a " +
        "server-side function (the key never reaches the browser), within per-visitor and daily limits."
      : "");
  drawMarkings();
  $("stat-pass-tile").hidden = !bundle.passing;
  if (bundle.players) {
    state.teamTotals = Object.fromEntries(["shots_on_target", "pressures", "ball_wins"].map((n) => [n, teamTotals(n)]));
  }
  bindMatchPicker(index, entry);
  bindControls();
  bindShortcuts(bundle.highlights || {}, bundle.recap);
  bindRecap();
  const params = new URLSearchParams(location.search);
  bindPersonalize(params);
  bindPlayerPicker(params);
  bindViewToggle(params);
  const speed = Number(params.get("speed"));
  if ([15, 30, 60, 120, 240].includes(speed)) { state.speed = speed; $("speed").value = String(speed); }
  setMode(params.get("mode") === "analyst" ? "analyst" : "casual");
  if (params.has("t")) seek(Number(params.get("t")) || 0);
  requestAnimationFrame(frame);
  document.body.dataset.ready = "true";
}

window.matchlens = { state, seek, setMode, setPlaying, recapText };

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
