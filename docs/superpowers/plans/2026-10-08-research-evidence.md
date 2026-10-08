# Evidence-based AI research implementation plan

> For agentic workers: use superpowers:executing-plans inline; one independent whole-change review at the end.

Goal: improve the approved research design: business context, broader evidence, distinct insufficient/mixed states, finer impact scores, and a company overview of opportunities and risks.
Architecture: preserve legacy saved analyses with explicit scale/version; validate the new model contract separately. Cache verified company business descriptions, use bounded multi-topic search and materiality ranking, and build the overview deterministically from independently analyzed events without extra paid synthesis.
Tech stack: existing Python/FastAPI/Pydantic/MySQL/SQLite and React/TypeScript; no new dependencies.
Spec: approved four-part design in the preceding conversation, with permission to implement, push and update the existing server.

## Global constraints
- Do not merge concurrent user modifications or overwrite secrets; keep shared research writes serialized.
- Scores -100..100 in increments of 5; insufficient and mixed use null, neutral uses 0; confidence high/medium/low is evidence quality, not a probability.
- Business metadata has verified stock identity, source, timestamp and bounded main-business text; do not invent revenue shares.
- Retrieval bounded to four company topic queries, 12 results each, 12 extracted documents and 6 default deep analyses; retain partial-source results.
- Explicit Shanghai analysis timestamp and publication date; cache changes in business facts, prompt or model invalidate analysis.
- Legacy +/-2 results remain marked legacy; never relabel them as new scores or reclassify their ambiguous zeroes.

## Review focus
- Opposite effects must not become a neutral zero by averaging.
- Bad or missing business profile must not block company news or fabricate exposure.
- Partial multi-query source failures must remain visible without losing successful results.
- Legacy saved analyses, briefing ranking, manual analysis and JSON exports must preserve scoring scale.
- Invalid model state/score combinations must fail validation and retry, rather than silently invent scores.

## Tasks
1. New analysis contract and overview (`backend/prompts.py`, new `backend/evidence.py`, `tests/test_evidence.py`). RED: insufficient positive mismatch rejected, mixed not neutral, legacy separated, important announcement outranks routine price bulletin; implement schema/summary/ranking; GREEN.
2. Business metadata and multi-topic sources (`backend/providers.py`, new `backend/business_profile.py`, provider tests). RED: wrong code rejected, no revenue guessed, one failed topic does not erase good topic results, root URLs excluded by existing cleaning; implement verified F10 profile and bounded cached calls; GREEN.
3. Integrate context/time/cache/ranking/default six (`backend/research.py`, `backend/research_api.py`, `src/App.tsx`, test model fixtures and service regression tests). RED: new facts invalidate cached analysis, time/version/profile reach model, insufficient completed results preserved with null score; GREEN full unittest suite.
4. Frontend (`src/types.ts`, new analysis presentation helpers/overview, NewsFeed/AiDrawer/briefing/export). Show state, confidence/horizon, positive and negative evidence and watch points, /100 or explicit legacy /2. Build TypeScript/Vite; independent review; test full suite.
5. Commit/push, stage exact commit and built asset hashes, run relevant server tests, atomic deploy with rollback. Refresh representative oil/bank/rail/semiconductor dashboards on server with scheduler disabled; verify real API states and browser screenshot. Expand refresh to existing dashboards when representative results validate the contract. No email or push notifications.

## Progress
- Native worktree API cannot identify nested repo; isolated ignored git worktree created at `.runtime/research-evidence` from `1a3fa1f`; user changes remain outside it.
