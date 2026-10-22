# Market indices in the existing research dashboard

Scope: add 上证指数、深证成指、沪深300、上证50、中证500、中证1000、科创50、创业板指. Search and watchlist follow the existing chart workflow, defaulting to intraday and allowing daily/weekly views.

Ruling: reuse the clean isolated manual worktree, fast-forwarded to main60d7d8b, on codex/market-indices. User's request authorizes this extension; prior Git push and deployment instructions persist. No account preference or strategy activation is inferred.

Identity: indices use whitelisted exchange-prefixed IDs (sh000001), equities retain six-digit IDs (000001). Each catalogue record exposes asset_type and display symbol. Quote responses must match the full requested exchange symbol, never just the number. Daily, minute and bundle caches remain isolated by canonical ID.

Sources: SSE https://www.sse.com.cn/ ; CSI https://www.csindex.com.cn/ ; SZSE https://www.cnindex.com.cn/zh_indices/sese/scale/index.html?act_menu=0&index_type=100 . Live read-only server probe with AkShare1.19.1 succeeded for all eight:27 daily bars over45 calendar days and1970 minute bars, latest2026-10-22. Installed stock_zh_a_hist_tx explicitly supports stocks and indices; stock_zh_a_minute accepts validated exchange symbols. Explicit index worker kinds route to these adapters. Index volumes retain source units rather than asserting equity shares.

Implementation:
- [x] Independent index registry, catalogue filter and discoverable index search tab.
- [x] Symbol-safe dual quote channels, explicit daily/minute source routing and cached chart support.
- [x] Index watchlist additions save without paid company/news jobs. Scheduled research, private digest and batch research skip indices; direct company-research launch rejects them.
- [x] Index dashboard hides company scoring and community panels; point/volume labels are instrument-aware. Paper stock picker excludes indices, and execution retains stock-only board validation.
- [x] Major index ticker and bounded minute prewarming; cached results remain immediately visible.
- [ ] Full regression, browser inspection, source-level final review and live application preflight.
- [ ] Commit/push, immutable release deployment with rollback and public endpoint verification.

Baseline:407 Python tests passed before changes. New RED test failed because backend.instruments did not exist; initial focused GREEN16 tests passed. Further regressions cover both000001 identities across both quote channels, persisted daily/minute isolation, all8 index catalogue records, sources, research exclusion and paper rejection. No news model training or research/provider credentials are changed.

Final review: Copilot requested_model=auto found a purported volume scaling bug. Rejected after verification:178043900 source units equals17804.39万源单位, a presentation abbreviation that preserves both quantity and unknown provider unit. No share conversion is asserted. PriceChart forwards the new volumeUnit prop through {...props}, confirmed in the full source. Existing and added formatting tests verify this. Local browser verified index ticker, index-only search, SH:000300 identity, points, daily and weekly chart switching, and hidden company/news panels.

Verification:416 backend tests and57 Node tests passed; TypeScript/Vite build passed. Actual application adapters on the server returned147 daily bars and1970 one-minute bars for each of8 indices, plus9 distinct quotes including indexsh000001 and equity000001. Latest source data was2026-10-22; pre-open2026-10-23 correctly leaves the current intraday grid empty.
