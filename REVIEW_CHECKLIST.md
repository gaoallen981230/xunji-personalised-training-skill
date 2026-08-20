# Manual review checklist for version 0.1.0

This file is for the pre-publication review and may be removed before the first tag.

## Release verification snapshot — 2026-08-20

- Official Codex Skill validation: pass with the bundled official validator and PyYAML 6.0.3 supplied from the local uv cache.
- Synthetic offline test suite: 116 of 116 passed.
- Local whitelist release gate: pass, 24 files.
- Xunji example plan and the dedicated offline example plan: 0 errors, 0 warnings after resolving the synthetic profile's deliberate provenance marker.
- SkillSpector 2.5.1 `--no-llm`: every enabled analyser completed and 13/13 Skill files were fully inspected; semantic LLM analysers were disabled by configuration. One `LP3` issue remains with issue severity MEDIUM and confidence 0.7; the overall assessment is risk score 7, severity LOW, recommendation SAFE. The scanner requests a `permissions` frontmatter field that the official Codex Skill format does not accept. This is a disclosed non-zero, no-LLM result.
- On 2026-08-20, the repository owner explicitly accepted the disclosed `LP3` result and instructed publication of this reviewed 24-file package.

## Product decisions

- [x] Approve Skill name: `xunji-personalised-training`.
- [x] Approve proposed repository name: `xunji-personalised-training-skill`.
- [x] Approve explicit-only invocation (`allow_implicit_invocation: false`).
- [x] Approve the general-purpose model in which each user defines their own direction, priorities, and measures.
- [x] Approve the minimum onboarding fields and one-message intake style.
- [x] Approve JSON as the first-release local configuration format.
- [x] Approve the separate `personalised_training_plan_v1` offline format, which contains no Xunji payload and hard-disables remote writes.
- [x] Approve the draft -> digest -> explicit confirmation -> `write_intent` -> `verify_pending` or `ambiguous` -> full-data reconciliation state machine, plus `fully_verified` -> `reverify_pending` -> `fully_verified` or `drifted` for historical checks.

## Training policy

- [x] Confirm that every user-defined direction must have a real task or outcome and a success measure.
- [x] Confirm the default single-variable progression guard.
- [x] Confirm that missing RPE or completion data prevents precise load progression.
- [x] Confirm that red-flag symptoms stop automatic progression.
- [x] Confirm that the Skill makes no guaranteed outcome or implicit treatment prescription.

## Xunji policy

- [x] Re-check the current endpoint shapes, limits, response fields, and rate limits.
- [x] Confirm that updates preserve `localid`, `start`, `end`, notes, and unrelated metadata.
- [x] Confirm that each update binds the reviewed full-data original snapshot and refuses remote drift.
- [x] Confirm that `prepare-write`, `write`, and `verify` require the same unchanged profile and plan, and the same unchanged baseline whenever `--baseline` is used.
- [x] Confirm that every update uses its bound original snapshot as an internal baseline and that an external baseline is required for any declared progression not fully covered by update snapshots.
- [x] Confirm that a declared movement name appearing in any create operation requires an external baseline rather than borrowing a same-name update snapshot.
- [x] Confirm that every actual update dose change is declared exactly, so an empty declaration cannot bypass progression limits or red-flag stops.
- [x] Confirm that progression comparison preserves set positions, recursively checks nested `items[].set` targets, and retains every target alias, load unit, metric path, and movement difficulty.
- [x] Confirm that the manifest digest binds the complete profile and optional baseline, not only the Xunji payload.
- [x] Confirm that typed progression covers distance, calories, intensity, density or rest, tempo, range, difficulty, exercise selection, and unknown JSON-Pointer-style `metric:/normalised/path` leaves without literal-key or nesting collisions.
- [x] Confirm that an unchanged imported legacy difficulty is preserved, while every new or changed difficulty is restricted to `easy`, `normal`, or `hard`.
- [x] Confirm that nested dose and completion fields are traversed recursively at every supported `items[].set` depth.
- [x] Confirm that an added set is only a set-count change when its complete target signature matches one comparable existing set; evidence cannot be assembled across different sets, and novel targets count separately.
- [x] Confirm that adding another occurrence of an existing movement name is declared as `exercise_selection` and does not disappear into the existing occurrence's set count.
- [x] Confirm that normal API updates cannot delete original movements, sets, or nested items, and that unknown or unrelated metadata additions, removals, or changes fail closed.
- [x] Confirm that every newly created session, movement, set, and nested item rejects fields outside the reviewed API allowlist, and that planned sets cannot carry completion evidence.
- [x] Confirm that started or completed sessions cannot be overwritten by the normal workflow.
- [x] Confirm that uncertain Chinese movement names block writeback.
- [x] Confirm that a marker is stored as `write_intent` before dispatch, an acknowledged write becomes `verify_pending`, and an uncertain outcome becomes `ambiguous` when possible.
- [x] Confirm that transaction-directory creation, lock creation/removal, and every marker replacement are parent-directory-synchronised, and that a sync failure before upsert prevents dispatch.
- [x] Confirm that verification requires a matching credential-namespace unresolved transaction marker and performs full-data reconciliation.
- [x] Confirm that `write_intent`, `ambiguous`, and `verify_pending` block blind resend until reconciliation finishes.
- [x] Confirm that historical verification durably enters `reverify_pending` before its full read, that timeout remains blocking, and that mismatch becomes `drifted`; a first-time mismatch must retain its original unresolved state.
- [x] Confirm that the same drifted digest cannot be rewritten, while a different digest can proceed only through a new manifest, explicit confirmation, and live full-data preflight.
- [x] Confirm that create and update read-back each have exactly one normalised-title match, and that the update's unique `localid` selects that same record.
- [x] Confirm that the exclusive Key-namespace/date lock blocks concurrent readers, writers, and verifiers, and that stale locks fail closed for inspection.
- [x] Confirm that transaction locks and markers use one fixed per-user root and cannot be relocated with `--cache-dir` or `XDG_CACHE_HOME`.
- [x] Confirm that version-3 movable read caches bind the full Key fingerprint, exact date, full/light mode, and transaction epoch, reject symbolic links, unexpected owners, and group/other permissions, and are bypassed for every date with a transaction marker.
- [x] Confirm that partial multi-date dispatch can be reconciled with the unchanged complete input and resumed without resending `fully_verified` dates.
- [x] Confirm that repeated verification performs a fresh full-data read even for a historical `fully_verified` marker.
- [x] Confirm that replacing a training data Key creates a new local cache namespace and must wait until every pending transaction is reconciled.
- [x] Confirm that no compare-and-swap, revision, or ETag precondition has been verified for Xunji updates and that a fresh preflight does not eliminate the race.
- [x] Confirm that the local lock does not prevent another device from passing the same-title preflight and creating a duplicate session.

## Publication and legal

- [x] Approve the MIT Licence and copyright line.
- [x] Confirm that no third-party movement list, Xunji code, logo, or proprietary asset is bundled.
- [x] Choose GitHub owner `gaoallen981230` and public visibility.
- [x] Approve the bilingual repository description: `通用个性化训练 Codex Skill：支持任意训练方向、训记数据 Key 导入及明确确认后的写回与回读校验；服务端不保证原子/恰好一次写入。 General personalised training Skill with custom goals, Xunji Key import, explicit-confirmation writeback and read-back verification; atomic/exactly-once writes are not guaranteed.`
- [x] Choose GitHub topics: `codex-skill`, `xunji`, `personalised-training`, `workout-planning`, `fitness`, and `python`.
- [x] Run the local release gate and all unit tests.
- [x] Run SkillSpector 2.5.1 with `--no-llm`; record the exact non-zero `LP3` result and complete execution ledger.
- [x] Confirm that the privacy gate detects concrete macOS, Linux, and Windows home paths and exempts only explicit public account placeholders.
- [x] Accept the documented SkillSpector `LP3` schema mismatch for publication as a disclosed non-zero finding; do not call it a zero-finding scan.
- [x] Repository owner chose the full guarded-writeback release on 2026-08-17 and accepted the documented residual concurrency race; this does not make writes atomic or exactly once.
- [x] Review the final file manifest and secret/PII scan.
- [x] Give an explicit upload instruction only after all items pass.
