# News processing shadow pilot

**Goal:** Implement the approved news processing flow as a reproducible research pilot, separating own-company events from industry transmission. Preserve every article and uncertain case; generate human review material before considering production routing.

**Architecture:** A standalone stdlib Python experiment package, outside the production backend, imports previously seen historical inputs. Two DeepSeek prompts produce evidence-validated weak references. Laya learns two questions (own event and coarse industry connection); detailed conditional, distant and sentiment distinctions remain in the review record. A replay produces routing suggestions and an HTML review queue, never deletion.

**Stack:** Python unittest, urllib, optional GPU Laya/Torch already installed on the authorized GPU. No new web-server dependencies.

**Spec:** The user's approved pipeline in this conversation: program material quality → documented business facts → small classifier → large-model review and explanation → human review. The initial 133 existing historical pairs are a pilot, not the final 1000–3000-pair corpus.

**Global Constraints:**
- Frozen v3/v4/v4b archives and the Oct12–Nov1 prospective protocol remain byte-identical. No prospective discovery or test tuning.
- No production API/DB/job/site integration; all routing is shadow. NONE/C remains stored and reviewable.
- AI agreement and valid quotes do not establish truth. Every generated reference stays human_verified=false.
- Keep publication dates and business provenance outside model features; preserve visible tables. Do not invent missing business exposure.
- API credentials remain on the authorized server. Durable request cap, input/prompt/model keyed cache, resumable locked runs. Token cap is explicitly a soft observed stop.
- Old development labels and student predictions are not inputs to the new teacher. Event groups never cross dataset roles.
- Compare untrained multilingual Laya and the new split model on the same development validation only. Do not describe teacher-reference agreement as independent performance.

**Review Focus:** malformed probabilities, incomplete material, quote integrity including table evidence, own-event attribution from background only, stale cache and config changes, partial retries, process restarts/budgets, event-group leakage, HTML injection, missing class support, truncation, unsupported deployment claims.

### Task 1: Split contract and shadow routing
**Interfaces:** Produces validated evidence labels and retained routing suggestions from explicit state and quality metadata; consumed by CLI and datasets.
- [ ] Write failing unittest cases for own vs industry evidence, pending materials, nondeletion, probabilities and HTML escaping.
- [ ] Implement separate questions, input identity and evidence validation, quality guards and shadow routes.
- [ ] Run `python3 -m unittest tests.test_news_processing -q`; expect all new tests to pass.
- [ ] Commit the contract and routing implementation.

### Task 2: Reproducible labeling and training tools
**Interfaces:** Imports explicit historical JSONL into new state-only inputs. Produces locked weak-reference records, fixed grouped train/validation/calibration files and optional two-question GPU checkpoints. Consumes Task 1 contract. Never loads prior independent test pairs.
- [ ] Write failing tests for input/prompt/model cache identity, persisted attempt limits, incomplete answers, reference agreement and role separation.
- [ ] Implement bounded dual-prompt teacher, import/label/assemble/replay CLI, review HTML, one-run frozen GPU trainer and paired base comparison.
- [ ] Run the complete new module tests; expect pass without network or Torch.
- [ ] Commit reusable tools and usage documentation.

### Task 3: Execute the historical pilot and review
**Interfaces:** Uses the new tools on 133 existing historical pairs, preserves all prior archives. Outputs new ignored runtime archive and checked-in result report, with actual counts and limitations.
- [ ] Declare exact old input hashes, group roles, API budget, model base and one-run training settings before teacher labels/student predictions.
- [ ] Run bounded labeling with existing server configuration; assemble only valid agreed weak references and retain every disagreement for human review.
- [ ] If all question/classes have support in every role, run one frozen GPU experiment. Otherwise record gate failure without forcing labels or changing roles after seeing predictions.
- [ ] Replay the pilot and export review queue; verify archive hashes and all previous freezes.
- [ ] Run repository backend unittest suite using original repo venv; get a fresh independent whole-branch review per executing-plans skill and fix important findings with RED→GREEN tests.
- [ ] Commit report and push the authorized development branch, leaving production unchanged.

Future corpus expansion, real human labels, Chinese reranker benchmark and a separately preregistered unseen-company evaluation follow this pilot; they cannot be replaced by the pilot's development agreement.
