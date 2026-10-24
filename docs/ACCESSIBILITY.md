# Accessibility: replay UI

Target: **WCAG 2.2 AA** for the replay UI (`src/matchlens/web/`). The audit was done on 2026-10-24 by reading the code, running axe-core and adding browser tests. A screen-reader pass with real assistive technology is still to do (see the end of this file).

## Automated results (2026-10-24)
- **axe-core 4.10.2** (`wcag2a/aa`, `wcag21a/aa`, `wcag22aa`, `best-practice`), Chromium, three states: start (1400×900), Analyst mode at full time with a metric inspected (1400×900), and mid-match on a phone (390×844).
  - **0 violations** in all three.
  - The only item flagged "needs review" is colour contrast on glyph-only elements (◎, ✓, ⓘ). They were measured by hand at 6.1:1 or higher.
  - Before the fixes, axe reported a target-size violation at 390px (overlapping timeline markers) and two prohibited `aria-label`s on plain `div`s.
  - axe was run ad hoc, injected into a test browser with CSP bypassed. It is not a project dependency.
- **Re-run after the match-story redesign** (axe-core 4.10.2, WCAG tags only, Chromium): start, Casual at full time, Analyst at full time (1400×900), after "See the AI get fact-checked" with every `<details>` open (1400×900), and Casual at full time on a phone (390×844). **0 violations** in all five. New controls: the two shortcut buttons have visible text (the ▶/✓ glyphs are `aria-hidden`); the analyst event chain is a native `<details>` whose open state survives re-renders, so focus returns to the chip that was activated (browser test); the AI-check shortcut moves focus to the claim check it opens; the 24px target test now skips elements inside closed `<details>`, which are not rendered.
- **Contrast**, computed from the CSS tokens:
  - text is at least 5.66:1 (`--warn` on `--panel-2`), and body text is 13.3:1;
  - control borders (`--control-border`) are at least 3.6:1;
  - the focus ring is at least 6.3:1;
  - team colours on the pitch are 4.25:1 (home) and 5.46:1 (away).
- **Browser tests** (`tests/test_e2e_browser.py`, accessibility section), run on both local targets in CI and against the public URL by the smoke workflow:
  - one `h1`, and one polite status region;
  - every control has an accessible name;
  - arrow keys and roving tabindex in the mode radio group;
  - keyboard focus survives a card re-render;
  - new moments are announced;
  - the slider speaks the match clock;
  - shortcuts leave native controls alone;
  - interactive targets are at least 24px;
  - the page reflows at 320, 390 and 500px with no horizontal scroll, no overlapping markers or dots, and no truncated stat values;
  - a goal dot is drawn only for goals without a card.
  - 12 of the 14 new test runs failed on the pre-fix UI.

## Fixes
| # | Issue | WCAG | Fix |
|---|---|---|---|
| 1 | The scoreboard was an `aria-live` region whose clock changes every frame. `#cards` was live too and re-announced every card on each re-render. | 4.1.3 | Both live regions removed. One `role="status"` region (`#announcer`) now speaks only the latest new moment ("Goal, minute 23: …") and full time, debounced by 600 ms. |
| 2 | Activating an evidence or metric chip re-rendered the cards and dropped keyboard focus to `<body>`. | 2.4.3 | Focus is restored to the same control (card + event/metric ID) after each re-render. |
| 3 | The `role="radio"` mode toggle had no arrow-key support or roving tabindex. Its focus ring was clipped by the pill's `overflow: hidden`. | 2.1.1, 2.4.7 | ←/→/↑/↓/Home/End move the selection, with a single tab stop. The ring is now drawn inset. |
| 4 | Global Space/← → shortcuts also fired on `summary`, links and radios, which broke native keys. | 2.1.1 | Shortcuts skip interactive elements and the scrollable card panel. They also ignore modifier keys. |
| 5 | No `h1`. The ⓘ xG hint was title-only, and the ✓ marks had no text equivalent. | 1.3.1, 1.1.1 | Brand is the `h1`. Hidden text reads "(synthetic heuristic, not empirically calibrated)" and "Verified:" / "Verified, heuristic value:". The glyphs are `aria-hidden`. |
| 6 | The seek slider announced raw seconds. | 1.3.1 | `aria-valuetext` is set to e.g. "10:00, 1H". |
| 7 | Timeline markers were 17.6px and chips about 18px. Markers also overlapped on phones. | 2.5.8 | Markers are 24px and chips have `min-height: 24px`. Markers that would overlap stack into extra rows, recomputed on resize. |
| 8 | Control borders were 1.38:1. | 1.4.11 | New `--control-border` token (≥ 3:1 on all panels). |
| 9 | The scrollable card panel was not keyboard-reachable in Casual mode. | 2.1.1 | `tabindex="0"` with a visible focus ring, labelled by its heading. |
| 10 | `aria-label` was set on plain `div`s (stats, controls). | 4.1.2 | Changed to `role="group"`. |
| 11 | Horizontal scroll at 320px (long team names in the scoreboard). | 1.4.10 | The scoreboard wraps. |
| 12 | Shortcuts were only documented in the README. | 3.3.2 | Visible hint under the controls. |
| 13 | "Last event" was cut off with an ellipsis when zoomed ("1' Northbridge Rovers: …" at 400%). Found in the operator zoom check. | 1.4.10 | The value wraps (`overflow-wrap: anywhere`) and is never truncated. |
| 14 | A plain goal dot overlapped a "G" marker once that marker stacked into a second row (400% zoom). | 1.4.10 | No dot is drawn for goals that already have a "G" card marker; overlap tests now include the dots. |

Also: the lower-third graphic is `aria-hidden`, because it duplicates the active card. Reduced-motion users get no transitions or animations; playback only ever starts on request.

## Known limitations (accepted)
- On the pitch, team is shown by colour only (blue/amber, legend below). The pitch is a supplementary graphic. The same information is given as text in "Last event" and on every card, which always names the team.
- When a card list re-renders while a screen-reader virtual cursor is inside it, the cursor may move. The announcer gives the new moment, and focus is preserved for keyboard users.

## Still to do (operator)
- [ ] **Screen-reader pass:** VoiceOver + Safari (macOS) and/or NVDA + Firefox/Chrome (Windows). Script:
  1. Tab through the header, controls and timeline, and check that each control's name makes sense.
  2. Press Play at 240× and check that each new moment is spoken once and the clock is never read on its own.
  3. In Analyst mode, activate a chip and check that focus stays on it and the inspector content is reachable.
  4. Check that the narrative's "not verified" / "offline template" label is read with each card.
- [x] **Zoom check (operator, 2026-10-24, Chrome on macOS, live site):** at 200% and 400% the page reflows to one column with no horizontal scroll, and every region is reachable: scoreboard, mode toggle, pitch, stats, controls, timeline, Key moments and footer. Two defects were found and fixed (#13, #14 above). Regression tests: 320/390/500px with a long "Last event", no clipped stat values, and no overlapping timeline markers or dots.
