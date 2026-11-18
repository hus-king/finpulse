# News processing shadow pilot

This package is not imported by FastAPI. It stores every news/company pair and exports suggestions and a human review page. It does not delete or hide news, update production scores, or predict returns.

The additive [evidence pipeline](../../docs/news-evidence-pipeline-2026-10-13.md) extracts article facts first, freezes them, then links every fact to each target company. Evidence IDs resolve to literal visible-text coordinates; input-cost and output-price roles are per fact. It shares article inputs between targets and reserves a conservative token bound before requests, including inflight and unobserved requests. `evidence_review` exports a read-only review page; `evidence_replay` explicitly records post-hoc development revalidation without overwriting the original. It is not part of the trainer, and every result remains an AI weak label. Use a new output directory and keep API credentials separate.

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

The initial `assemble` command produces teacher-only candidates. Training additionally requires `fusion.assemble_reviewed` output: evidence-bound blind source checks (or inherited development consensus bound to a frozen ID/input/expected-label registry), preserved disagreements, and optional explicit three-actor AI source adjudications. Ordinary source references are revalidated at admission, not only adjudications. A coarse source disagreement remains pending. Adjudications require two distinct source reviewers plus a distinct root reviewer, literal evidence, matching input hashes, and an explicit rationale; all remain weak references. Do not overwrite raw teachers. Financial PDF excerpts identified by their table-page provenance must contain complete logical metric rows, correctly aligned headers/values and explicit units; unknown schemas and split rows stay pending. Archived older runs keep their frozen runner and inputs.

Every role must contain all classes of both questions in **at least two distinct event groups per class**. The trainer recomputes group support and checks it against the manifest before reserving a run. Use Laya's actual loss spelling `soft-ce`; library configuration validation runs before reservation/base inference. A failed gate stops training; do not force labels or recycle a test set to satisfy it. The trainer uses the declared base, checks full token budgets, balances classes per question, selects the earliest best epoch by mean question macro-F1, fits temperature on separate calibration groups, and compares to the untrained base on the same development validation. Such results measure agreement with weak references, not real-world accuracy. Training/replay predictions are never added to reference labels.

CLI replay requires the matching import protocol and teacher archive in its root. For a separately frozen combined training root, use `pipeline.render_review` and `pipeline.route` with its retained input/consensus/prediction artifacts, as in the archived expansion runner. The [expansion report](../../docs/news-processing-expansion-2026-10-11.md) records the first completed split-task training and its recall failures.

Labeling freezes prompt, provider model/endpoint identity, inputs and budget. It holds an exclusive process lock and durably records reservations before requests. Resumes reuse matching answers, including invalid evidence retained for review; each mode/input cache identity uses at most two attempts across restarts. Fatal HTTP 400/401/403/404/422 stops the provider run instead of consuming the whole budget. The request cap is hard; token stop uses observed usage and can be exceeded by in-flight requests or missing usage reporting. Raw API answers stay in the experiment directory; credentials are never included. Do not share a server config with this directory.

Next stages need more event groups and company contrasts, real human core labels, Chinese reranker and direct-LLM comparisons under the same inputs, then a separately frozen new prospective evaluation. Existing frozen tests and model artifacts cannot be repurposed as independent evidence after this development work.

A training gate failure does not prevent diagnostic replay with the unchanged base model. Declare a separate inference freeze containing tool hashes before predictions, then run `python -m experiments.news_processing.infer --root EXPERIMENT --checkpoint BASE --weight-sha SHA256` and pass `base_shadow_predictions.jsonl` to replay. This is untrained, uncalibrated development inference, not a replacement training run.

The [next development comparison](../../docs/news-processing-comparison-2026-10-12.md) repairs complete financial rows and compares a fixed checkpoint, an abstaining issuer rule and direct DeepSeek against separately recorded source reviews. `financial.reconstruct` only accepts explicitly reviewed column schemas. `issuer.attribute(record, verified_sources)` requires a source registry binding an archived PDF digest, a source-side issuer code and the exact excerpt digest. Prepare that registry from inspected originals and freeze it; a URL or caller-supplied hash alone is not source verification. The rule has no industry prediction and retains every input.

Fixed-checkpoint comparison, without optimizer steps:

```sh
python -m experiments.news_processing.benchmark --root NEW_DEVELOPMENT_ROOT --candidate fixed_v6
```

This requires `comparison_freeze.json` pinning inputs, separate references, tools, actor registry, full checkpoint/tokenizer artifacts and token budgets. `comparison.summarize(..., actor_registry=...)` binds candidate/source roles and canonical identities to this frozen registry. Registry identities document recorded generations; they do not prove distinct model backends, particularly for Copilot's `auto` routing. Missing predictions and deliberate abstention have separate counts, with positive support retained in both cases. Report newly collected sources separately from repaired, previously seen sources. Source disagreements remain pending, and agreement with AI sources is never human-reference accuracy. Parser fixes discovered after comparison need a separate declared derivative; do not overwrite original rules, predictions or results.

The [bulk dataset report](../../docs/news-bulk-dataset-2026-10-26.md) records 3,000 real article requests and their source, label and review counts. `bulk_dataset` reuses the bounded journal, records all raw answers, and selects one contiguous event interval per article before judging the supplied company contrasts. It does not infer whole-article irrelevance from a single NONE event.

For a newly frozen bulk input directory containing `articles.jsonl`, `company_catalog.json` and `protocol.json`:

```sh
python -m experiments.news_processing.bulk_dataset NEW_ROOT --config /protected/provider-config.json --limit-batches 1
python -m experiments.news_processing.bulk_dataset NEW_ROOT --config /protected/provider-config.json
```

The second call skips already reserved batches, including invalid results, and preserves the declared request/token caps. Historical runs retain their exact source snapshots; current code rejects a resume with different executable sources. Credentials stay outside run directories. `model_packet` withholds primary semantic labels from a review request; `checked_pairs` records independent target failures, and `export_dataset` writes all eligible weak candidates separately from review-agreed silver. A later revalidation must retain its original raw outputs and declare its own code/input hashes. None of these files is production gold or an independent test set.

The [bulk GPU training report](../../docs/news-bulk-training-2026-10-26.md) records two completed fixed two-epoch runs over 609 train/141 development rows. Frozen per-experiment launchers set the previously audited 4096/512 input limits consistently for preparation and execution. Uniform and task/class-weighted runs have identical data, training order and base predictions; each covers all 609 original rows exactly once per epoch. Weighting improves relation macro-F1 but reduces total development agreement and NONE recall. Both exported checkpoints remain research artifacts.

The [fixed eight-epoch learning check](../../docs/news-bulk-learning-check-2026-10-27.md) retains the same data/weights and verifies all 4,872 loss calls. Training fit improves strongly while distant-link development remains 0/8. A blind diagnostic of training mismatches also identifies a stock quote overview admitted as article text; future bulk exports quarantine the explicit Xueqiu A-share quote-page route, preserving the source and historical frozen datasets. AI reviewer suggestions remain separate from reference labels.
