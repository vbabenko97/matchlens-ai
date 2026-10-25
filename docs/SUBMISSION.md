# Submission package (Innovation Studio)

The form asks for a pitch, the Microsoft technologies used, a public GitHub link, a public demo video under 2 minutes, and testing instructions. All texts below are in English, ready to paste.

## Links
- **Public repository:** <https://github.com/vbabenko97/matchlens-ai>. Submission version: tag [`v1.3-submission`](https://github.com/vbabenko97/matchlens-ai/tree/v1.3-submission). It is created once, on `main`, right before the form is submitted, after the last code change; the link then resolves to that commit, so no document has to be updated after tagging. Earlier tags mark earlier versions; `v1.2-submission` is the fallback if the final version is not ready.
- **Demo (primary, Azure):** <https://wonderful-hill-04967dd0f.5.azurestaticapps.net/>
- **Demo (backup, GitHub Pages, static only):** <https://vbabenko97.github.io/matchlens-ai/>
- **Demo video:** *add the YouTube/Vimeo URL*

## Elevator pitch
MatchLens AI is explainable match intelligence for the people who put words on screen: a studio editor or stream producer who has to say why a moment matters without risking a wrong number on air. From synthetic football match events it builds a card for every key moment that says why it matters (a comeback, an equaliser, what a missed chance would have meant) and cites the events it comes from. Cards appear only once their evidence exists. The same facts become two different experiences: a story-first view for casual fans, in English, German or Ukrainian and from either club's side, and an analyst view with event chains, metrics and an evidence inspector. They also go out as timed overlay JSON that a broadcast lower-third plays in OBS or vMix, and as a full-time recap to copy or download with its sources.

Facts are rendered from structured data, never written by a model. Three Microsoft Foundry agents work around them: a claim agent explores the match with read-only tools and proposes structured claims that a deterministic validator accepts or rejects; a personalizer rewrites fan cards for a language and a club; a critic reviews each rewrite for meaning. Rejections go back once with reasons, each claim check says in plain words what was rejected and why, and AI wording is always labelled; the on-air graphic shows template facts only. The problem it solves is trust: a broadcaster or league cannot put model output on screen unless every number and name is traceable. MatchLens makes that the default, with the controls an enterprise rollout needs (an audit trail of recorded runs, fail-closed spend limits, smoke tests) and a pilot plan that states what is still missing.

## Microsoft technologies
- **Microsoft Foundry:** Azure OpenAI `gpt-4.1-mini`, Responses API v1, function calling and strict JSON-schema outputs, `store: false`. Three agents (claim agent, personalizer, critic), plus recorded live runs with response IDs (`docs/evidence/`).
- **Azure Static Web Apps:** hosting, plus a managed **Azure Functions** API (Python 3.11) for live Foundry checks.
- **Azure Table Storage:** the shared daily spend cap, reserved before every model call.
- **GitHub Actions:** CI on Python 3.11 and 3.13, deploys to both hosts, and Playwright smoke tests after every deploy and daily through 10 November.

## Testing instructions
No account, sign-in or API key is needed. Everything runs in the browser. The data is synthetic and the teams and players are fictional.

1. Open <https://wonderful-hill-04967dd0f.5.azurestaticapps.net/> and press **▶ Watch the comeback (77'–81')**. Insight cards appear as the moments happen; each leads with why it matters.
2. Switch to **Analyst** and press **See the AI get fact-checked (77')**. The claim check opens with a plain summary of a recorded Foundry agent run: the checker rejected one claim because its evidence did not fit the statistic, and the agent's retry was verified. The claim-by-claim list and the response IDs are below it. Click any event or metric ID on a card to inspect it and highlight it on the pitch.
3. On an analyst card, press **Check this card live with Microsoft Foundry** to run the agent live. It takes about 10 s. A shared daily cap protects the demo: if it is used up, the page says so and keeps the recorded run.
4. Switch back to **Casual Fan**, set **Language** to Deutsch or Українська and **Follow** to a club. Lines that failed the checker, the critic agent or an editorial review show the English fact, and each card says so.
5. In **Analyst**, the **Passing** table compares the teams' pass quality so far: facts (accuracy, length, progressive and pressed passes, tempo) next to a clearly labelled synthetic xPass heuristic. Click a value to see those passes on the pitch.
6. Pick a **Player** to follow one player: their running numbers (facts, with synthetic xG labelled as a heuristic), their last touch on the pitch, and a badge on every card that cites their events.
7. Press **▤ Match recap (full time)**: the full-time story and the scorers, built only from checked card lines. **Copy recap** copies plain text; **Recap JSON** keeps every line with its event and metric IDs. With a language and club set, the recap follows them.
8. Press **▣ On-air graphic** for the broadcast lower-third, or **⬇ Overlay JSON** for the timed cues. The **Match** picker offers five more matches the demo was not tuned for.

To run locally (Python 3.11+, no runtime dependencies): `pip install -e . && python -m matchlens serve`, then open <http://127.0.0.1:8000/>. To process a match as a feed, one event at a time: `python -m matchlens feed --seed 1 --speed 60 | python -m matchlens stream -`.

## Category
Enter **Best Enterprise Solution** (with the Grand Prize). Decided on 2026-10-25: the description asks for "a clear path to enterprise adoption, balancing innovation with the reliability, transparency, and controls required for use in production", and that is MatchLens's case. The README's [From demo to production](../README.md#from-demo-to-production) section is written for it: the controls already in place, the Azure deployment, a pilot plan and the gaps before production. If the form takes a second category, Best Use of Microsoft Foundry is the next fit.

## Images for the form
From `docs/images/` (taken from the local static build of this version):
1. `comeback.png`: the replay at 81', with the comeback card and the lower-third.
2. `ai-check.png`: the 77' claim check, with the plain summary above the claim list.
3. `fan-uk.png`: the fan view in Ukrainian, following Eastvale, at the 81' comeback. Lines whose rewrite was rejected stay in English.
4. `recap.png`: the match recap at full time.
5. `on-air.png`: the on-air lower-third.
6. `architecture-pipeline.png` and `architecture-azure.png`: the two diagrams from the README.

## Before pressing submit
- [ ] Video under 2:00, public on YouTube or Vimeo, link in the form and in `README.md`.
- [ ] Both demo URLs open in a private window (no sign-in).
- [ ] The code is final and deployed (both deploy runs green, smoke included), and the video was recorded from that version.
- [ ] Application Insights: enabled on the Static Web App and checked with the free `telemetry-probe` request ([DEPLOYMENT](DEPLOYMENT.md), "Telemetry of the live check"). Only then add it to "Microsoft technologies" above; otherwise leave it out.
- [ ] Tag the final version once: `git fetch origin main && git tag -a v1.3-submission FETCH_HEAD -m "Submission version" && git push origin v1.3-submission`. The repository is public and the tag is visible. A docs-only commit after the tag needs no new tag.
- [ ] Category in the form: Best Enterprise Solution.
- [ ] The project page shows as submitted, not as a draft.
- [ ] Keep both hosts up through the end of judging, 10 November 2026 23:59 PT (11 November 08:59 Vienna); the daily smoke test covers both, but not the paid live-check button.
