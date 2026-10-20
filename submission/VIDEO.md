# The demo video

## What it is

A silent, captioned recording of the live deployment, made with Chrome DevTools
Protocol by [`scripts/record-demo.py`](../scripts/record-demo.py) — no editing, no
re-creating, no mock. Every frame is the real studio on the real Container App
talking to the real Foundry deployment.

- **Length:** under 2:00 (the rules' limit is 2:00; the plan's target was 1:50)
- **Sound:** silent. The rules allow silence and forbid copyrighted music, and
  English on-screen text is the safest form of the narration.
- **Marks:** none. The repository contains no image file of any kind, so there
  is no crest, no kit, no logo and no wordmark graphic to appear.
- **Trademarks:** "Microsoft Foundry" appears as plain text in the provider badge
  and in the closing caption. That is the hackathon's own required technology
  name, not a third party's mark, and the Premier League name appears nowhere.
- **Language:** the interface chrome is English throughout. The one beat in
  Deutsch carries its English alongside it in the caption.

## How it was made

```bash
python scripts/record-demo.py --out build/touchline-demo.mp4
```

The recorder launches Chrome, waits for the provider badge to report `live`,
then drives the studio over CDP while capturing a screencast into ffmpeg. It
reads the provider badge after every beat and refuses a take that shows a
template fallback — that check is in the script, not in someone's memory.

Re-record it with one command whenever the deployment changes.

## Before uploading

1. **Check the badge.** The recording log must show `provider badge: Microsoft
   Foundry | live` and no line reading `WARNING: the provider fell back`.
2. **Check the length.** `ffprobe -v error -show_entries format=duration -of
   csv=p=0 build/touchline-demo.mp4` — it must be under 120 seconds.
3. **Check the frames.** Cut a few and look at them, especially the opening
   title card, the agent trace beat, the Deutsch beat and the closing caption.

## Uploading

Public, not unlisted — the rules are explicit about that. YouTube, Vimeo, Facebook
Video or Youku all qualify.

**Title**

```
Touchline Intelligence — five agents that explain a football match, live
```

**Description**

```
Touchline Intelligence turns a synthetic football event stream into explainable
intelligence: 1,051 events over 90 minutes, 45 explained moments, every claim
cited to the event ids it was computed from, and a grounding pass that recomputes
each cited metric from the log and vetoes any claim that does not hold up.

The numbers are computed in deterministic code. The model only writes the words.
Five agents share one live match state, orchestrated with the Microsoft Agent
Framework, with the Explainer and Narrator talking over A2A.

Live demo: https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io
Repository: https://github.com/nurulgofran/touchline-intelligence

Microsoft Foundry · Microsoft Agent Framework · A2A · Azure Container Apps ·
Azure Container Registry · managed identity, keyless.

All data is synthetic. The clubs and every player are fictional, and no real
match data, competition, club, player or commercial mark is used or represented.
```

## Then

- Paste [`PITCH.md`](./PITCH.md) into the project page.
- Link the project to **challenge 5078** — *The Synthetic Match Insights Engine
  for Premier League Studio*.
- Put the public video URL into the pitch's links table and into the project page.
- Work down [`CHECKLIST.md`](./CHECKLIST.md) — the three open lines are the
  upload, the challenge link, and the project page itself.
