# Daily CSI300 PEG leaderboard

Approved in chat: independent FinPulse leaderboard; positive valid forward PEG ascending; financial and nonfinancial ranked separately; confidence and risk remain independent; price immediately after stock name. Display all 300 constituents and explain ineligible rows. Clicking a stock opens existing market/news; adding a watchlist entry remains an explicit user action.

Every day at 21:00 Asia/Shanghai, one designated backend instance collects fresh market, forecast and financial inputs through the existing Stock-Analyzer engine. Store published, immutable daily snapshots; reads never trigger upstream collection. Publish an entire assessed batch atomically; preserve the last successful snapshot on failure. Show last success, source collection dates, forecast report dates, next scheduled update and update errors. Preserve historical snapshots for comparable rank movement. The initial installation may explicitly build its first snapshot immediately.

Reuse the user's current, uncommitted Stock-Analyzer source as a pinned integration with file hashes and provenance. Preserve the original checkout unchanged. The integration runs in a separate process, bounded in time and concurrency. Do not copy secrets or historical datasets. All public data is read-only; updates are scheduler/CLI operations. Daily leases prevent concurrent duplicate jobs and are renewed throughout collection. Local and test schedulers default off.

The frontend follows existing dark/light theme styles, supports financial/nonfinancial grouping, status/industry/confidence/search filters, pagination, expandable source/risk details and CSV export. Daily ranking data remains a snapshot; the existing intraday stock view is independent.
