# News processing shadow pilot

This package is not imported by FastAPI. It stores every news/company pair and exports suggestions and a human review page. It does not delete or hide news, update production scores, or predict returns.

The model has separate own-event and coarse industry questions. The DeepSeek review retains DIRECT, CONDITIONAL, DISTANT, SENTIMENT, NONE and UNKNOWN with news/business quotes and missing conditions. A name mention is not sufficient own-event evidence. Missing bodies, dates or company products remain pending.

Two prompts from the same DeepSeek endpoint are **weak references**, even when they agree and quotes match. Quote validation checks attribution and literal presence, not whether the causal interpretation is true. The pilot produces zero human-verified labels.

Use explicit historical input paths and a new output directory:

```sh
python -m experiments.news_processing import --source historical.jsonl --expected-sha SHA256 --group-roles group_roles.json --profiles profiles.json --root .runtime/news-shadow-pilot
python -m experiments.news_processing label --root .runtime/news-shadow-pilot --config /path/to/server-local/config.local.json
python -m experiments.news_processing assemble --root .runtime/news-shadow-pilot
python -m experiments.news_processing replay --root .runtime/news-shadow-pilot
```

Group roles must be declared before labeling. Import accepts only train/validation/calibration roles; there is no independent-test input command. Old labels are omitted from the projected state. Source dates are preserved as historical metadata, not independently reverified by this importer. Excerpts are explicitly identified as excerpts; this pilot does not claim to refill missing original bodies.

Before optional GPU training, create `training_config.json` (Laya TrainConfig fields plus base_checkpoint, base_weight_sha256) and `training_freeze.json` containing relative-path hashes for the input datasets, manifest, protocol, settings, and experiment source files copied into the run. Run with the existing GPU environment:

```sh
python -m experiments.news_processing.train --root /path/to/new/experiment
python -m experiments.news_processing replay --root /path/to/new/experiment --predictions /path/to/new/experiment/student_shadow_predictions.jsonl
```

Every role must contain all classes of both questions. A failed gate stops training; do not force labels or recycle a test set to satisfy it. The trainer uses the declared base, checks full token budgets, balances classes per question, selects the earliest best epoch by mean question macro-F1, fits temperature on separate calibration groups, and compares to the untrained base on the same development validation. Such results measure agreement with weak references, not real-world accuracy. Training/replay predictions are never added to reference labels.

Labeling freezes prompt, provider model/endpoint identity, inputs and budget. It holds an exclusive process lock and durably records reservations before requests. Resumes reuse matching answers, including invalid evidence retained for review; failed batches use at most two attempts. The request cap is hard; token stop uses observed usage and can be exceeded by in-flight requests or missing usage reporting. Raw API answers stay in the experiment directory; credentials are never included. Do not share a server config with this directory.

Next stages need more event groups and company contrasts, real human core labels, Chinese reranker and direct-LLM comparisons under the same inputs, then a separately frozen new prospective evaluation. Existing frozen tests and model artifacts cannot be repurposed as independent evidence after this development work.
