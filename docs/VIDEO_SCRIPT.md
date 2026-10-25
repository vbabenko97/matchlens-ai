# Demo video script (target 1:55, hard limit < 2:00)

Version shown: tag `v1.2-submission` (`main` @ `6f8556b`), deployed on the Azure host. The shots below predate the match recap and the plain claim-check summary; both are on the page now. Voice-over: English, about 250 words: about 1:49 at 138 words per minute. That leaves only a few seconds for pauses, so keep the trimmed live wait short. On-screen labels below are the exact UI texts.

## Before recording
- **Host and capture.** Record the Azure host: <https://wonderful-hill-04967dd0f.5.azurestaticapps.net/>. Capture a 1920×1080 browser window at 110–125% zoom; hide bookmarks and extensions.
- **Tabs.** Open every tab listed below beforehand, so no page load or typing is recorded.
- **One live call.**
  - The live check has a shared daily cap. After the last review the API reported `remaining_today: 1`, so do **not** press the live button while rehearsing.
  - If it is used up, the button shows "Today's live-check budget is used up." The count resets at 00:00 UTC (02:00 Vienna). The operator can also raise `MATCHLENS_LIVE_DAILY_LIMIT` in the Static Web App settings for the recording.
  - A live call took 7–13 s end to end. Trim the wait in editing and caption it "wait shortened".
- **Speed.** Leave playback at 60× (the default).

| Tab | URL |
|---|---|
| A: replay | `https://wonderful-hill-04967dd0f.5.azurestaticapps.net/` |
| B: personalized | `https://wonderful-hill-04967dd0f.5.azurestaticapps.net/?t=5400&lang=uk&follow=away` |
| C: on-air graphic | `https://wonderful-hill-04967dd0f.5.azurestaticapps.net/broadcast.html?mode=casual&rundown=1&hold=5&bg=dark` |
| D: player and passing | `https://wonderful-hill-04967dd0f.5.azurestaticapps.net/?mode=analyst&t=5400&player=EVA-11` |

## Shots

| Time | Screen (what to do) | Voice-over |
|---|---|---|
| 0:00–0:10 | Tab A at kick-off: scoreboard, pitch, timeline. | "Football stories don't end with the scoreline. MatchLens turns synthetic match events into explanations you can check, for fans, analysts and broadcasters." |
| 0:10–0:30 | Press **▶ Watch the comeback (77'–81')**. Let the 77' and 81' cards appear; hover the 81' card's first line. | "Each card appears the moment its evidence exists, never earlier, and leads with why it matters. Eastvale were two–one down in minute seventy-one; goals at seventy-seven and eighty-one turned it around. Every line cites the events it came from." |
| 0:30–0:52 | Switch to **Analyst**. Press **See the AI get fact-checked (77')**. Show "Attempt 1: 3 of 4 claims verified, 1 rejected and sent back" and the red rejected claim, then attempt 2. | "In Analyst mode, a Microsoft Foundry agent explores the match with read-only tools and proposes structured claims. A deterministic validator decides. Here it rejected a claim that cited the wrong events; the agent's retry was verified. Only verified claims are published." |
| 0:52–1:08 | On the same card press **Check this card live with Microsoft Foundry** (the one live call). Cut the wait. Show the green line "Live result from Microsoft Foundry: N model call(s), N tokens, N s" and the "Live Microsoft Foundry agent call" label. | "On Azure, the same agent runs live. An Azure Function calls Foundry; a shared daily cap in Azure Table Storage is reserved before every call, so a public demo cannot run up a bill." |
| 1:08–1:28 | Tab B (Casual, **Українська**, **Follow: Eastvale Athletic**, full time). Pan over the full-time card: three Ukrainian lines, two English lines, and the note under the facts ("meaning reviewed by a critic agent"). | "A second agent rewrites fan cards for a language and a club. A program checks every number, score and name; a third agent, the critic, checks the meaning. Lines that fail stay in English, and the card says why. The critic is a model too; its misses are measured and published." |
| 1:28–1:38 | Tab C: the lower-third plays the rundown. Then briefly back to Tab A and point at **⬇ Overlay JSON**. | "The same moments export as timed overlay JSON, and this page plays them as a broadcast lower-third, ready for OBS or vMix." |
| 1:38–1:50 | Tab D (Analyst, full time, **Player: #11 Ugo Vale (FW)**). Scroll from the **Passing (open play, so far)** table to the player panel; point at a "heuristic, not calibrated" row, then click a value so its passes light up on the pitch. | "Analysts can follow one player and compare passing. The counts are facts from the events; the pass model is a synthetic heuristic, and it says so. Click any number to see its passes." |
| 1:50–1:55 | End card: "MatchLens AI · Microsoft Foundry · Azure Static Web Apps + Functions · six synthetic matches · github.com/vbabenko97/matchlens-ai". | "Synthetic data, open source. MatchLens: match intelligence you can verify." |

## After recording
- **Upload.** Publish the video on YouTube or Vimeo as public or unlisted; the rules require a public URL there.
- **Check the result.** Confirm the length is under 2:00 and that captions or the voice-over are in English.
- **Link the video.** Add the link to `README.md` (it replaces "link added at submission") and to the submission form.
