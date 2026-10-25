# Demo video script (target 1:50, hard limit < 2:00)

Version shown: the deployed `main` after the last code change. That commit becomes tag `v1.3-submission` when the form is submitted (see [SUBMISSION](SUBMISSION.md)).

One idea carries the video: a producer gets the *why* of a moment, with its evidence. The shots follow that producer, so the video tells one story instead of touring features:
1. a key moment, explained;
2. the AI caught by the validator;
3. the evidence;
4. a fan's version;
5. the recap and the on-air graphic;
6. Azure.

Voice-over: English, about 220 words, so about 1:40 at 130 words per minute plus pauses. No shot is faster than about 130 words per minute. On-screen labels below are the exact UI texts.

Every claim in the voice-over matches what the page does:
- the 77' rejection was evidence that did not fit a pressing statistic, not a wrong score;
- the agent runs are recorded except the one live call, and the screen labels say so;
- the on-air graphic shows template facts only, never AI wording;
- the telemetry shot shows the row of the live call made in the same recording; every request, refused or not, writes one.

Do not say "every AI line is proven": the critic is a model, and the voice-over says only what it does.

## Before recording
- **Host and capture.**
  - Record the Azure host: <https://wonderful-hill-04967dd0f.5.azurestaticapps.net/>.
  - Capture a 1920×1080 browser window at 110–125% zoom; hide bookmarks and extensions.
  - Nothing else on screen should show a third-party trademark or logo.
- **Tabs.** Open every tab listed below beforehand, so no page load or typing is recorded.
- **Ukrainian lines.**
  - The lines in Tab B passed the checker, the critic and two AI editorial reviews. No human has read them yet. If a Ukrainian speaker reads them first and finds a wrong line, it must be added to the editorial review file (it then shows in English) and the version redeployed before recording.
  - Scores stay home first by design ("відставали 2-1" is Eastvale's 2-1 deficit as the scoreboard shows it).
  - Do not say on camera that a person checked the lines unless one did.
- **One live call.**
  - The live check has a shared daily cap, so do **not** press the live button while rehearsing.
  - If the cap is used up, the button shows "Today's live-check budget is used up." The count resets at 00:00 UTC (01:00 Vienna in winter time). The operator can also raise `MATCHLENS_LIVE_DAILY_LIMIT` in the Static Web App settings for the recording.
  - A live call took 7–13 s end to end. Trim the wait in editing and caption it "wait shortened".
- **Captions.** Burn in English captions with the voice-over text: judges may watch without sound.
- **Music.** None, or a track you hold a license for.
- **Speed.** Leave playback at 60× (the default).

| Tab | URL | State before recording |
|---|---|---|
| A: replay | `https://wonderful-hill-04967dd0f.5.azurestaticapps.net/?t=4862` | Paused just after 81': the "Comeback! Eastvale Athletic take the lead" card and the lower-third on the pitch |
| B: fan view | `https://wonderful-hill-04967dd0f.5.azurestaticapps.net/?t=4862&lang=uk&follow=away` | Casual, Українська, Follow: Eastvale Athletic, paused just after 81' |
| C: on-air graphic | `https://wonderful-hill-04967dd0f.5.azurestaticapps.net/broadcast.html?mode=casual&rundown=1&hold=5&bg=dark` | Rundown playing |
| D: live check | `https://wonderful-hill-04967dd0f.5.azurestaticapps.net/?mode=analyst&t=4606` | Analyst, just after 77'; the 77' card on top |
| E: recap | `https://wonderful-hill-04967dd0f.5.azurestaticapps.net/?t=5400` | Casual, English, full time |
| G: telemetry | Application Insights `matchlens-demo` → *Monitoring → Logs*, *Agent* off, **KQL mode**, query 5 from [telemetry.kql](telemetry.kql) pasted | Run it about 5 min **after** the live call in Tab D: rows reach Logs with a 2–5 min delay. Zoom so the results grid fills the frame, or crop in editing: the portal header shows the account e-mail and the address bar the subscription ID. Never open the connection string or the environment variables while recording. |
| F: diagram | `docs/images/architecture-azure.png` in an image viewer, or the README section "How the demo runs on Azure" | Full screen |

## Shots

| Time | Screen (what to do) | Voice-over |
|---|---|---|
| 0:00–0:10 | Tab A, paused at 81': the comeback card, the lower-third and the 2–3 scoreboard. | "A producer has seconds to explain a moment, and no room for a wrong number. MatchLens gives the why, with its evidence." |
| 0:10–0:26 | Press **▶ Watch the comeback (77'–81')**. Let the 77' and 81' cards appear; hover the 81' line "Eastvale Athletic were 2-1 down in minute 71. Goals in minutes 77 and 81 turned it around." | "Each card appears once its last piece of evidence has happened, never earlier, and leads with why the moment matters: two–one down in minute seventy-one, then two goals turned it around." |
| 0:26–0:46 | Press **✓ See the AI get fact-checked (77')**. Hold on the plain summary at the top of **Claim check** ("The checker rejected “Pressures applied: 131”: the events it cited as evidence (a tackle, 2 passes and a goal) are not counted by that statistic."). Scroll a little to the red claim under **Attempt 1 — proposed claims**, then to **Attempt 2 — after the validator's feedback**. | "Three Microsoft Foundry agents work around these facts. In this recorded run, the claim agent backed a pressing statistic with a tackle, two passes and a goal. The validator rejected it, the agent retried with actual pressure events, and only checked claims were published." |
| 0:46–0:51 | On the same 77' card, click the metric chip `home.pressures@4500-5400:asof:ml1-01206` ("Northbridge Rovers — Pressures applied: 3"). The **Evidence inspector** shows the label, value and definition, and the pitch highlights the pressures. | "Every number opens its definition and the events behind it." |
| 0:51–1:10 | Tab B. On the 81' card (Eastvale's comeback, from their fans' side) open **More (3)**: three Ukrainian lines, the title and one line kept in English, and the note ("meaning reviewed by a critic agent (also AI)"). The full-time card is not used here: after the second review it is mostly English for Eastvale fans. Then switch the pitch to **Lineups** and click #11 U. Vale: the ring, and the "Features Ugo Vale" badge on his cards. | "For fans, a personalizer agent rewrote the same facts in their language, from their club's side. A program checks numbers and names, a critic agent reviews meaning, and lines that fail stay in English. Fans can follow one player, too." |
| 1:10–1:26 | Tab E. Press **▤ Match recap (full time)**, then **Copy recap** (the status reads "Recap copied as plain text.") and **⬇ Recap JSON**. Cut to Tab C: the lower-third plays. | "At full time the recap is ready, built only from checked lines: copy it as text, or download it with every source. On air, the lower-third shows template facts only." |
| 1:26–1:42 | Tab D: on the 77' card press **Check this card live with Microsoft Foundry** (the one live call). Cut the wait. Show the green line "Live result from Microsoft Foundry: N model call(s), N tokens, N s" and the plain summary of the live run. Cut to Tab G for 3 s: the row of this call (`live`, model calls, tokens, claims rejected per attempt), recorded separately once it has arrived. Cut to Tab F for 2 s. | "On Azure, a Functions endpoint runs the claim agent live against Microsoft Foundry, behind a daily spend cap reserved before every call. Each request lands in Application Insights with its tokens and verdicts." |
| 1:42–1:50 | End card: "MatchLens AI · Microsoft Foundry · Azure Static Web Apps + Functions · Application Insights · synthetic data · github.com/vbabenko97/matchlens-ai". | "Synthetic data, open source, every line traceable. MatchLens: match intelligence you can verify." |

**Optional: the feed in a terminal** (`matchlens stream`, on `main` since #27).
- **Recording.** Open the 0:10–0:26 shot with 4 s of a terminal running `python -m matchlens feed --seed 1 --speed 120 | python -m matchlens stream -`, as it prints "event 1206 arrived: shot (goal), 2-2" and "▸ casual card published". Start the command about 40 s earlier, because 77' arrives after 38 s at 120×. Then cut to the page.
- **Voice-over for that shot**, the same length: "Events arrive one at a time; each card is published as its last piece of evidence arrives, never earlier, and leads with why the moment matters: two–one down in minute seventy-one, then two goals turned it around."

Left out on purpose (it is in the README and on the page): the comparison bars, the passing table, the spend-cap mechanics, response IDs, the five extra matches, and test counts.

## After recording
- **Upload.** Publish the video on YouTube or Vimeo as public. The rules ask for a public URL; an unlisted video opens from its link, but public is the safe reading.
- **Check the result.** Confirm that the length is under 2:00, that the captions are in English, and that the live-call wait is marked "wait shortened".
- **Link the video.** Add the link to `README.md` (it replaces "link added at submission") and to the submission form.
