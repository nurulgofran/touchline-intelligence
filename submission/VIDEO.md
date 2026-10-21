# The demo video

## Status: validated MP4 ready for owner upload

Recorded from deployed revision `7ba3d3a` on 2026-10-21. The accepted take is
`build/touchline-demo-final.mp4`; `build/touchline-demo.mp4` is an identical copy.
The earlier, unapproved Oct 20 take is retained separately as
`build/touchline-demo-20261020-unapproved.mp4`.

- **76.333 seconds**, 1600 × 900, H.264, 30 fps, silent; 6,046,119 bytes.
- All 11 recorder beats passed. Capture counters: 210 → 214 Foundry calls,
  with fallback and substitution counts both remaining zero.
- The complete file decodes without errors. A storyboard sampled every second
  and full-resolution evidence, recovery, recap, personalization and closing
  frames were reviewed. The owner should watch the complete MP4 before upload.
- SHA-256: `595fe1887ffdc0710b65995e8257b4c86e326d300cc295cd7de3fe39b668ed05`.

Cleanup restored Analyst/English and verified the feed had settled.

The owner selected delivery of the MP4 for manual upload. It has **not** been
published, and there is no public video URL yet.

## What it is

A silent, captioned recording of the live deployment, made with Chrome DevTools
Protocol by [`scripts/record-demo.py`](../scripts/record-demo.py) — no editing, no
re-creating, no mock. Every frame is the real studio on the real Container App
talking to the real Foundry deployment.

- **Length:** accepted take 76.333 seconds; recorder budget 110 seconds, hard limit under 120 seconds (the rules' limit is
  2:00 — judges are not required to watch beyond it).
- **Sound:** silent. There is no music or third-party audio. English on-screen
  captions carry the narration.
- **Language:** English throughout — the captions, the interface and the explanation
  the model writes. The studio has no language switcher, so nothing on screen is
  in a language the judge has to read twice.
- **Graphics:** no club or sponsor crests, kits, logos or wordmark graphics.
- **Technology identification:** "Microsoft Foundry" appears as plain text in the
  provider badge and closing caption to identify the actual stack. No sponsor or
  club logos, crests, or brand graphics are used.

## The six beats (from the plan, §6)

1. **The problem and the live studio.** A scoreline explains nothing; an event log
   explains nothing without a human. The studio: the pitch, the momentum curve, the
   insights as they are explained, the agent trace — all live at once.
2. **One explanation, with the evidence inspector.** Open one insight, show its
   cited events and metrics, then *Inspect evidence*: the recomputed values, the
   baseline, the grounding verdict — calculated facts distinguished from model
   interpretation.
3. **Handoffs and the labelled recovery replay.** The agent trace, with the A2A
   chip on the Narrator → Explainer hop; then the explicitly labelled
   *Recovery example — recorded test run*: an invalid claim rejected, routed back
   to the Explainer, corrected, accepted.
4. **The full-time recap.** Jump to the end of the match: the deterministic recap.
5. **The same moment, personalized.** The same moment rewritten for *My team*
   (Riverside United), with the figures recomputed for the chosen side from the same synthetic match.
6. **Closing value and technology credits.** One value statement; then the credits:
   Microsoft Foundry, Microsoft Agent Framework, A2A, Azure Container Apps, Azure
   Container Registry, managed identity (keyless).

## How it is made

```bash
python scripts/record-demo.py --out build/touchline-demo.mp4
```

The recorder launches Chrome, waits for the provider badge to report `live`, then
drives the studio over CDP while capturing a screencast into ffmpeg. A take is
written to a temporary path and promoted to the final filename **only after
validation**: the recorder targets 110 seconds and **rejects any take at or over
120 seconds**; it rejects a take for missing required beats, a beat that could
not show what its caption claims, a provider fallback during capture (it
compares counters across the capture interval rather than sampling the badge),
or prose substitution. A rejected take is kept at the temporary path for
inspection. Cleanup restores Analyst/English and verifies the restore —
cleanup failure is reported, never described as a successful reset.

Before recording, wait for the deployment's analysis to complete (historically
about 17 minutes) and restore Analyst/English.

## Before uploading

1. **Check the badge.** The recording log must show `provider badge: Microsoft
   Foundry | live` and no fallback warning — the recorder rejects such a take
   itself, so a promoted file is clean.
2. **Check the length.** `ffprobe -v error -show_entries format=duration -of
   csv=p=0 build/touchline-demo.mp4` — it must be under 120 seconds.
3. **Check the frames.** Cut a few and look at them, especially the opening
   title card, the evidence inspector beat, the recorded recovery replay, and the
   closing caption. **Watch the entire finished video, not just screenshots.**

## Uploading (needs the owner's account)

Public, not unlisted — the rules are explicit about that. **YouTube** is the
chosen host (Vimeo, Facebook Video or Youku would also qualify).

**Title**

```
Touchline Intelligence — three agents that explain a football match, live
```

**Description**

```
Touchline Intelligence turns a synthetic football event stream into explainable
intelligence: 1,051 events over 90 minutes, 45 explained moments plus one full-time
recap, every claim cited to the event ids it was computed from, and a grounding pass
that recomputes each cited metric from the log and vetoes any claim that does not
hold up.

The numbers are computed in deterministic code. The model only writes the words.
Three model-driven agents share one live match state, orchestrated with the
Microsoft Agent Framework, with the Explainer and Narrator talking over A2A.

Live demo: https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io
Repository: https://github.com/nurulgofran/touchline-intelligence

Microsoft Foundry · Microsoft Agent Framework · A2A · Azure Container Apps ·
Azure Container Registry · managed identity, keyless.

All data is synthetic. The clubs and every player are fictional, and no real
match data, competition, club, player or commercial mark is used or represented.
```

## Then

- Paste [`PITCH.md`](./PITCH.md) into the project page.
- Put the public video URL into the pitch's links table and into the project page.
- Link the project to **challenge 5078** — *The Synthetic Match Insights Engine
  for Premier League Studio*.
- Work down [`CHECKLIST.md`](./CHECKLIST.md).
