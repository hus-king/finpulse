# Daily PEG Leaderboard Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to execute the approved scope continuously, with focused helper review.

**Goal:** Deploy a usable CSI300 low-forward-PEG leaderboard, refreshed daily at 21:00 Beijing time.
**Architecture:** Pinned Stock-Analyzer worker produces fresh rows; a snapshot service assesses, publishes and serves them from the existing research store. A React page reads the snapshot and opens existing stock workflows.
**Tech Stack:** Python/FastAPI/APScheduler, existing SQL research store, pandas/requests, React/TypeScript.
**Spec:** docs/superpowers/specs/2026-10-22-peg-leaderboard.md

## Global Constraints
- Exactly one successful scheduled daily publication; cron timezone Asia/Shanghai, 21:00.
- Eligible forward-PEG ordering and financial/nonfinancial separation match the user's current selection.py.
- API reads never run collection. Preserve last good snapshot on malformed/incomplete/source-wide failed batches.
- No source credentials or generated stock datasets in Git; preserve the original Stock-Analyzer checkout.
- Separate process and leased collection with bounded resource use, clean cancellation and shutdown.

## Review Focus
- Old/new snapshot differences in universe, method and group must not create misleading rank deltas.
- Restarts and concurrent instances must not duplicate paid or slow collection.
- Null/nonfinite metrics, negative/zero PEG and duplicate/invalid codes must not reach ranked rows.
- Incomplete and failed source responses must not erase good published data.
- Empty/local install, guest navigation, source times, mobile/light theme and failed fetches remain usable.

## Tasks
- [x] 1. Pin the four required upstream Python files and provenance; test snapshot mapping, eligibility, groups, ties, deltas, invalid/partial input and failure preservation. Implement backend/peg_snapshot.py.
- [x] 2. Test daily service with real temporary store and controlled upstream worker: publication, idempotence, lease conflicts/renewal, cancellation, 21:00 cron and restart catchup. Implement backend/peg_service.py, backend/peg_collect.py and read-only API/CSV integration in app.py.
- [x] 3. Implement src/PegLeaderboard.tsx with scoped styling and DTOs; integrate navigation and existing stock/watchlist actions. Validate interactions and cache/error states in a browser.
- [ ] 4. Run backend and frontend regressions/build, inspect a focused helper review, fix material findings. Commit and push, provision the pinned worker and persistent daily data path, create fresh first snapshot, activate release and verify live page/API/scheduler.

## Execution ledger
Ruling: Human explicitly approved the design and said to start construction; execute the implementation without another plan approval prompt.
Ruling: The native worktree tool cannot resolve the nested repository from the parent chat directory; use a verified ignored git worktree at .runtime/worktrees/csi300-peg-daily.
Ruling: Pin the user's current four Stock-Analyzer modules with SHA256 provenance rather than modifying or committing their unrelated uncommitted source work.

Review ruling: implement transactional lease fencing and atomic history/latest/completed-attempt publication, validate assessed membership, verify actual engine hashes at startup, and make scheduler startup idempotent. Four focused regressions were observed failing before their fixes. Explicit operator --force is a documented exception to scheduled daily idempotence; same-day forced publication intentionally retains the previous daily baseline rather than comparing against an earlier same-day attempt.
Browser checks: real300-row dataset, financial/nonfinancial tabs, code search, unranked observation rows, details, pagination, dark/light theme and navigation to the existing 000001 stock dashboard passed.
