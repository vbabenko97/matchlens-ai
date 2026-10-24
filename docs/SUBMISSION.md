# Submission package (Innovation Studio)

The form asks for a pitch, the Microsoft technologies used, a public GitHub link, a public demo video under 2 minutes, and testing instructions. All texts below are in English, ready to paste.

## Links
- **Public repository:** <https://github.com/vbabenko97/matchlens-ai>. Submission version: tag [`v1.1-submission`](https://github.com/vbabenko97/matchlens-ai/tree/v1.1-submission), commit `c79b15e` (the earlier tag `v1.0-submission` is `2b9da2a`).
- **Demo (primary, Azure):** <https://wonderful-hill-04967dd0f.5.azurestaticapps.net/>
- **Demo (backup, GitHub Pages, static only):** <https://vbabenko97.github.io/matchlens-ai/>
- **Demo video:** *add the YouTube/Vimeo URL*

## Elevator pitch
MatchLens AI turns synthetic football match events into explainable match intelligence that can be checked line by line. Every card says why a moment matters (a comeback, an equaliser, what a missed chance would have meant) and cites the events it comes from. Cards appear only once their evidence exists, so they can sit beside a live feed. The same intelligence becomes two different experiences: a story-first view for casual fans, in English, German or Ukrainian and from either club's side, and an analyst view with event chains, metrics and an evidence inspector. It is also exported as timed overlay JSON that a broadcast lower-third plays in OBS or vMix.

Three Microsoft Foundry agents do the language work. A claim agent explores the match with read-only tools and proposes structured claims; a personalizer rewrites fan cards for a language and a club; a critic reviews each rewrite for meaning. Deterministic checks decide what is published, and rejections from the checks or the critic go back once with reasons. The critic is measured against a labelled run, and its misses are published. The problem it solves is trust: a broadcaster or league cannot put model output on screen unless every number and name is traceable, and MatchLens makes that the default.

## Microsoft technologies
- **Microsoft Foundry:** Azure OpenAI `gpt-4.1-mini`, Responses API v1, function calling and strict JSON-schema outputs, `store: false`. Three agents (claim agent, personalizer, critic), plus recorded live runs with response IDs (`docs/evidence/`).
- **Azure Static Web Apps:** hosting, plus a managed **Azure Functions** API (Python 3.11) for live Foundry checks.
- **Azure Table Storage:** the shared daily spend cap, reserved before every model call.
- **GitHub Actions:** CI on Python 3.11 and 3.13, deploys to both hosts, and Playwright smoke tests after every deploy and daily through 10 November.

## Testing instructions
No account, sign-in or API key is needed. Everything runs in the browser. The data is synthetic and the teams and players are fictional.

1. Open <https://wonderful-hill-04967dd0f.5.azurestaticapps.net/> and press **▶ Watch the comeback (77'–81')**. Insight cards appear as the moments happen; each leads with why it matters.
2. Switch to **Analyst** and press **See the AI get fact-checked (77')**. The claim check shows a recorded Foundry agent run: one claim rejected by the validator, then a verified retry, with response IDs in the disclosure. Click any event or metric ID on a card to inspect it and highlight it on the pitch.
3. On an analyst card, press **Check this card live with Microsoft Foundry** to run the agent live. It takes about 10 s. A shared daily cap protects the demo: if it is used up, the page says so and keeps the recorded run.
4. Switch back to **Casual Fan**, set **Language** to Deutsch or Українська and **Follow** to a club. Lines that failed the checker, the critic agent or an editorial review show the English fact, and each card says so.
5. In **Analyst**, the **Passing** table compares the teams' pass quality so far: facts (accuracy, length, progressive and pressed passes, tempo) next to a clearly labelled synthetic xPass heuristic. Click a value to see those passes on the pitch.
6. Pick a **Player** to follow one player: their running numbers (facts, with synthetic xG labelled as a heuristic), their last touch on the pitch, and a badge on every card that cites their events.
7. Press **▣ On-air graphic** for the broadcast lower-third, or **⬇ Overlay JSON** for the timed cues. The **Match** picker offers five more matches the demo was not tuned for.

To run locally (Python 3.11+, no runtime dependencies): `pip install -e . && python -m matchlens serve`, then open <http://127.0.0.1:8000/>.

## Category
The README maps MatchLens to the Grand Prize and to Best Use of Microsoft Foundry, Best Multi-Agent System and Best Azure Cloud Native Integration. The category chosen in the form is the operator's decision. The strongest evidence is the Foundry integration: three agents, validator- and critic-gated publishing, and recorded and live runs with response IDs.

## Before pressing submit
- [ ] Video under 2:00, public on YouTube or Vimeo, link in the form and in `README.md`.
- [ ] Both demo URLs open in a private window (no sign-in).
- [ ] The repository is public and the tag `v1.1-submission` is visible.
- [ ] The project page shows as submitted, not as a draft.
- [ ] Keep both hosts up through 10 November 2026 (daily smoke test on both).
