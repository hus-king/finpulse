# Historical expansion and one split-task retraining

**Goal:** Continue the approved pilot by adding source-supported own events and conditional/distant mechanisms, review weak labels, and run one split-task GPU experiment if the fixed dataset gate passes.

**Architecture:** Reuse existing offline tools on the same isolated research branch. A new v6 archive declares discovery slots and roles before labeling; original v5/v4/v3 artifacts remain immutable. Own event selection uses official securities disclosure; conditional mechanisms use regional/eligibility documents, with all seven company contrasts, without conditioning selection on model labels.

**Spec:** The approved flow and previous result report `docs/news-processing-pilot-2026-10-11.md`. This is the next historical development batch, not an independent performance evaluation. True human labels remain pending and are required for production.

**Global Constraints:** No website/DB/scheduler/automatic filtering; retain every material. Preserve old roles and source groups, don't turn any seen pair into independent test. New model cannot replace the two models of the old frozen prospective protocol. Credentials remain server-local. One declared training run, pinned base weights, model labels never enter reference inputs. Reviewer notes/evidence stay outside features. API cap100, observed token stop500000, all AI decisions human_verified=false. Date-only announcement records are not actual midnight publication instants. Parse PDFs sequentially. Missing documents remain pending.

**Review Focus:** source/code/date mismatches, duplicate announcement variants crossing roles, article-reference leakage, unsupported causal labels, literal table fidelity, partial caches, class support by event groups, strong-label claims, checkpoint selection and calibration on separate groups.

### Task 1: Source intake and frozen historical batch
**Interfaces:** Produces declared official-source materials and exact-state inputs; consumed by weak review and data fusion. Prior source metadata is preserved.
- [ ] Add failing intake tests for issuer-code mismatch, same event across roles, loss of source/date quality, table-only material and group counts.
- [ ] Implement bounded source/provenance helpers; collect predeclared historical slots and archive failures.
- [ ] Validate original PDFs/HTML and dates; freeze states and company backgrounds before teacher/student labels.
- [ ] Run new module tests and commit the intake helpers.

### Task 2: Independent weak review and fixed fusion
**Interfaces:** Uses exact input hashes and frozen teacher prompts; produces weak evidence references and role-disjoint datasets with at least2 distinct event groups for every class in every role.
- [ ] Run bounded new-pair DeepSeek labeling using existing server-only config.
- [ ] Run independent blind source reviews authorized by the user's independent-review request; do not show labels or student outputs.
- [ ] Preserve raw decisions. Any source adjudication must cite exact news/business evidence, declare disagreements, stay human_verified=false, and never be forced to fill missing classes.
- [ ] Reuse old development roles; group same events and full/summary variants. Freeze train/validation/calibration and settings before student predictions.
- [ ] Test fusion provenance and class-group support; commit tools.

### Task 3: One GPU run, diagnosis and delivery
**Interfaces:** Consumes only frozen development datasets/settings; produces selected checkpoint, paired base comparison, calibration and full retained shadow replay.
- [ ] If gate passes, run one declared GPU experiment; otherwise document exact gap and keep material pending.
- [ ] Confirm exact selected/exported tensor hash and input/option token budgets; no independent-test prediction or production write.
- [ ] Compare per-question own-event recall, clear-industry retention, irrelevant promotion and macroF1 on development weak references; report counts and limitations.
- [ ] Fresh independent whole-delta code review, meaningful RED→GREEN fixes, full backend unittest suite.
- [ ] Hash-check old archives, back up new artifacts on GPU, write report and push/update existing draft PR without merge/deploy.
