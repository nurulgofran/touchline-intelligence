# Submission checklists — verified state

> The two checklists from the build plan, with what was actually checked and
> when. Every green line below was verified against the live deployment, not
> assumed. The three open lines are the ones that need the project owner's
> account, and they are marked as such.

**Verified:** 2026-10-20, against
`https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io`.

---

## Stage One gate — pass/fail

| # | Requirement | State | Evidence |
|---|---|---|---|
| 1 | The project runs on Azure and the demo URL is live (ACA + Foundry) | ✅ | `/healthz` returns `status: ok`; the revision is Running with 1 replica; `scripts/verify-live.py` passes every check |
| 2 | Foundry is the only model provider the deployed demo uses, and the badge shows it with the live state chip | ✅ | `/healthz`: `configured_provider` and `last_call_provider` are both `Microsoft Foundry`, `last_call_state: live`. The badge renders that state, and falls back to a visible amber `fallback` chip if it ever did not |
| 3 | Exactly one public deployment exists — no second host left running | ✅ | `touchline-rg` holds 7 resources: the Foundry account and project, the registry, the log workspace, the Container Apps environment, the app and the identity. One app, one URL. The scratch probe app and its resource group from Day 1 were deleted |
| 4 | The published demo URL is on the project page | ⚠️ | The URL is the app's own FQDN — the Consumption environment refuses custom domains, so there is no `demo.nurulgofran.dev` to publish. The pitch carries the URL; **the page itself still needs the pitch pasted into it** |
| 5 | The app does not scale to zero, and the subscription is funded | ✅ | `minReplicas: 1`, `maxReplicas: 1`. **Credit balance to be re-checked on 1 and 8 November** |
| 6 | The managed identity — not a stored key — is what authenticates to Foundry | ✅ | The app's `identity` is `UserAssigned` (`touchline-identity`), and `properties.configuration.secrets` is `null`. The image is pulled with the same identity (`AcrPull`), and the registry's admin user is disabled |
| 7 | Agent Framework is the orchestration runtime, visibly | ✅ | The agent trace shows the typed hand-offs as they happen — `ingest → analyst`, `explainer → grounding`, `grounding → narrator`, `narrator → personalizer` — 340 of them |
| 8 | A2A is used for a real inter-agent call, with an agent card published | ✅ | `/.well-known/agent-card.json` returns 200 with the `explain-insight` skill, and the trace shows the `narrator → explainer` hop with an `A2A` chip |
| 9 | The five-stage pipeline is recognisable in the UI, labelled by stage | ✅ | The stages are named in the trace panel; the README maps them as (a) Ingest, (b) Interpret, (c) Explain, (d) Render, (e) Personalize |
| 10 | Synthetic data only, no real match data | ✅ | The feed comes from the seeded simulator (`seed 42`). No real match data is used anywhere |
| 11 | Fictional clubs and players, no third-party trademarks in the UI or the video | ✅ | Six fictional clubs (`Riverside United`, `Dovercrest Athletic`, …) and players generated from invented syllable pools. The repository contains **no image file of any kind** — no crest, no kit, no logo, no wordmark graphic. The Premier League name appears only in the README prose. The video carries no marks either |
| 12 | Repo public, video public, project linked to challenge 5078 | ⚠️ | The repo is public and stays public through 10 November. **The video still has to be uploaded publicly, and the project still has to be linked to challenge 5078** — both need the owner's account |
| 13 | The demo needs no login and no payment | ✅ | No auth, no signup, no paywall, no judge-blocking rate limit. Free of charge through the judging period |
| 14 | Pitch, README, testing instructions and video all in English | ✅ | [`PITCH.md`](./PITCH.md) and [`TESTING.md`](./TESTING.md) are in English. The video's narration is English on-screen text; the one non-English beat carries its English alongside it |

---

## Submission checklist

| # | Requirement | State | Evidence |
|---|---|---|---|
| 1 | Newly built project — no pre-existing codebase | ✅ | The repository was created on 2026-10-20 for this hackathon |
| 2 | Pitch: what you built, which Microsoft and Azure technologies, what it does, what problem it solves, with the hero-technology map | ✅ | [`PITCH.md`](./PITCH.md) |
| 3 | Demo video under 2:00, on the platform it was built for, public, no third-party trademarks, no copyrighted music | ⚠️ | The video is recorded from the live deployment with Chrome DevTools Protocol and captioned in English — see `scripts/record-demo.py`. **It still has to be uploaded to a public URL** |
| 4 | Public GitHub repository URL | ✅ | `https://github.com/nurulgofran/touchline-intelligence` |
| 5 | Project linked to challenge 5078 | ⚠️ | **Still to do** — needs the owner's account |
| 6 | All materials in English | ✅ | This file, the pitch, the testing instructions, the README and the video |
| 7 | Testing instructions for a judge who has never seen the project | ✅ | [`TESTING.md`](./TESTING.md) |
| 8 | Stage One gate checklist all green | ✅ | See above; the three open lines are account actions, not build work |
| 9 | LICENSE and THIRD_PARTY_NOTICES present; secret scan clean; no keys in git history | ✅ | Both files are committed. A scan of the full history returns only shell flags and GitHub Actions expressions — no literal credential. The deployment holds no secret at all |
| 10 | The demo URL is live and free of charge; the video is public | ⚠️ | The URL is live. **The video still has to be published** |

---

## Also on the list, from the plan

- [ ] Check the Azure credit balance and the demo URL on **1 November** and **8 November**
      — the demo has to stay live through **10 November 23:59 PT**
- [ ] Keep the repository public through 10 November
- [ ] Re-take the video if a template fallback ever appears in the badge — the recorder
      warns about it, and the take is not publishable if it happens

### The November check, exactly

```bash
# 1. the subscription is still enabled
az account show --query '{name:name, state:state}'

# 2. the container app is still running, with its warm replica
az containerapp revision list -n touchline-app -g touchline-rg \
    --query "[?properties.active].properties.runningState" -o tsv

# 3. the demo actually answers
python scripts/verify-live.py \
    https://touchline-app.icywave-bfcbf183.germanywestcentral.azurecontainerapps.io

# 4. the repository is still public
gh repo view nurulgofran/touchline-intelligence --json visibility
```

**The credit balance itself is not in the CLI.** The Azure for Students allowance
is shown in the Education portal (Microsoft Azure for Students → credit), and the
Cost Management and Consumption APIs report no rows for this subscription. So the
balance has to be read in the portal — the four commands above are the signals
that something has gone wrong, and a lapsed subscription stops the revision
before it stops answering for long.
