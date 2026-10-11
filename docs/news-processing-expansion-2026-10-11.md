# News-processing expansion and single GPU experiment

The split-task model improved development agreement, but still misses important news. It remains an offline shadow candidate. Nothing in this branch modifies production news filtering, jobs, the website, or its database.

## Materials and reference review

The new historical batch contains **20 articles × 7 companies = 140 pairs**, assigned to 19 event groups before labeling. It includes six first-quarter reports, three repurchase announcements, regional procurement/eligibility documents, and two national production-material price releases. Source-level excerpts are identical across company contrasts; issuer codes and table headers/units were checked against original PDFs/HTML. Parsing was sequential. The original documents and longer extracted bodies are archived.

Six collected materials did not enter labeling: three failed-body fetches, two missing essential attachments, and a notice actually published in 2023 despite its page being regenerated in 2026. The Qinghai drug notice's six attachments were downloaded and searched across all 33 pages; no target-company name was found. Its complete notice remains a conditional eligibility example, rather than a claim that HengRui's medicines were listed. Shanghai policy variants and Yantai funding/response documents share event groups. URL dates did not override the statistical releases' actual publication dates.

DeepSeek produced two answers per new pair in **70 requests / 315,724 observed tokens**. Evidence validation passed for 130 primary and 128 audit answers. These are weak labels from one provider, not independent truth. Two independently dispatched source reviewers, blind to teachers and student predictions, reviewed the 140 new pairs plus the 23 previously pending pairs. Both identified the same 11 own-company events. Seven detailed industry decisions differed, including two coarse-class differences, which remain pending.

The strict teacher/source agreement gate already passed before adjudication. Root then inspected 27 malformed-quote/JSON cases and two obvious wrong-issuer attributions. Their 29 new source adjudications retain both raw teacher outputs, two independently identified source reviews, literal quotes, input hashes, and an explicit root rationale. Every reference remains `human_verified=false`; the root had seen older development results, so these adjudications are development material.

The combined batch retains **273 pairs from 39 articles in 31 event groups**. Training/validation/calibration contain **123 / 65 / 63** reviewed weak pairs, with **22 pending pairs** retained for review. Every class in every role has at least two distinct event groups. Train own-event support is only seven positives, validation three and calibration three; CLEAR/POSSIBLE train support is eight each. The same seven companies and recurring source series are used across roles. This is not a test of unseen companies or broad industry coverage.

## Frozen run and recovery

The declared run starts from the original multilingual checkpoint, not an earlier fine-tuned candidate. Settings: four epochs, seed42, encoder learning rate1e-5, head learning rate5e-5, micro-batch4, accumulation4, BF16, gradient checkpointing, soft cross entropy and smoothing0.04. Within each question, class-balanced resampling was fixed before training. It increases exposure to sparse classes but does not create additional independent events.

Complete-state token inspection found maximum sequence length2,267, head122, option25. The sequence budget is3,072, with a512-token head budget; no example is silently truncated. Validation selects the earliest best mean of the two macroF1 scores; calibration uses separate event groups.

The initial launch failed **before optimization** because Laya0.4.1 requires the API spelling `soft-ce`, while the inherited draft configuration contained `soft_ce`. It had already produced baseline predictions. Its directory, reservation, freeze and log are preserved. A separately declared recovery fixes only the spelling and adds validation before reservation/inference; data, roles, seed, epochs and learning rates are unchanged. There is **one completed optimizer training run**, not a search over new settings. No changes were selected from baseline predictions.

On the RTX4090D, optimization plus epoch-validation/checkpoint selection took **66.5s**, reserving **20.04GiB** at peak. This timer excludes initial loading/base inference and final calibration/export/replay. Epoch1 was selected; later validation macroF1 values decreased. The selected and exported tensor SHA256 are identical:

`e4bd1b5aa386ad85dbe96fca0419a9a3d965c39a03c37fdd805693ad285e8445`

GPU checkpoint: `/home/husking/finpulse-experiments/news-processing-v6-20261011/training_recovery_01/trained/checkpoint`. Recovery freeze SHA256: `0da8fd28eba5e9ef51bda22b30b43615f14e1238de454665eff831f687e81086`.

## Development results

The following are agreements against **65 development weak references**, used for epoch selection. They are not independently measured accuracy. Original base and candidate use identical questions, states, validation pairs and option decoding.

| Measure | Original base | Selected candidate |
| --- | ---: | ---: |
| Own-event macroF1 | 0.077 | 0.553 |
| Industry macroF1 | 0.194 | 0.334 |
| Mean question macroF1 | 0.135 | 0.443 |
| Own events correctly identified | 3/3 | **1/3** |
| Non-own news falsely attributed to target | 60/62 | **7/62** |
| CLEAR correctly classified | 5/5 | **1/5** |
| CLEAR retained as CLEAR or POSSIBLE | 5/5 | **3/5** |
| POSSIBLE correctly classified | 2/4 | **0/4** |
| NONE falsely promoted to CLEAR | 46/56 | **5/56** |

The improvement mostly comes from fewer unrelated promotions. It comes with poor own-event and industry recall, including two missed issuer reports, a feed-cost example and a container-port expansion. Seven non-own stories are still falsely attributed to the company, including wrong-issuer financial reports. A high majority-class accuracy would hide these errors.

The replay retains all273 inputs:172 large-model review suggestions,86 lower-priority review suggestions,5 industry suggestions and10 own-event suggestions. **Deleted/hidden news:0.** Replay includes training cases; it is not evaluation. Suggested routes do not execute any production action. Calibration has63 pairs per question and is not a production confidence guarantee.

## Next experiment and independent evidence

The existing prospective protocol keeps its originally frozen models and materials. The v6 model is ineligible for substitution there. A separately declared v6 prospective diagnostic window is **2026-10-13 through2026-11-12, Asia/Shanghai**, after both checkpoint and protocol freeze. It compares this fixed candidate with the original multilingual base on new, timestamped articles and frozen source references. It is a diagnostic test, not a promotion of a model that already fails development recall gates. No collector, scheduler or future inference has been started.

The separate declaration fixes primary sources, deterministic first-seen selection, event/source/near-duplicate exclusions, company profiles, questions, preprocessing, model identities, scoring, and missing-material rules. It excludes all previously seen material, including this expansion. First-seen time and content revisions must be append-only; a date-only source cannot establish an exact availability instant. An independent test score requires references frozen before predictions. AI-reviewed references must be reported as weak agreement; human review is required for a human-reference performance claim.

Further development should add **many more distinct own-event and conditional-transmission groups**, especially issuer/target contrasts, product mismatch, region/qualification conditions, and one-step versus multi-step chains. A source-side issuer/code attribution check is also a useful separately declared baseline. Do not increase epochs on these validation pairs and call the result independent. Do not interpret news relevance as direction, magnitude, or predicted stock return.

## Artifacts and verification

Local archive: `.runtime/news-processing-v6-20261011/` in the isolated research worktree. Key review pages: `human_review.html`, `pending_review_22.html`, `development_errors.html` (26 pairs with at least one development error). Original teachers, independent source reviews, adjudications, frozen roles/settings/code, the failed pre-optimizer launch, actual GPU results and separate prospective declaration are preserved. Large model weights remain on the authorized GPU server.

Hash checks confirmed **549 older archived files unchanged**, including both previous prospective freezes. Offline safeguards have regression tests for literal quotes/input identity, duplicate reviewer identity, role/source/group separation, genuine group support, false strong-label claims, and early config validation. Production integration remains false.
