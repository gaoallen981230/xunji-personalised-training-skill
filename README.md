# Xunji Personalised Training Skill / 训记个性化训练 Skill

[中文说明](#中文说明) | [English documentation](#english-documentation)

Release candidate: `0.1.0`, full guarded-writeback edition. The repository owner has selected this capability scope and accepted the documented residual concurrency risk.

发布候选版本：`0.1.0`，包含完整的受保护写回功能。仓库所有者已选择该功能范围，并接受文档中说明的残余并发风险

## 中文说明

这是一个面向通用训练需求的 Codex Skill。用户可以自行定义训练方向、优先级、衡量指标、每周时间、可用器械、经验、偏好和限制；Skill 据此生成可以人工审核和调整的个性化训练计划，不预设任何人的训练方案或特定专项。

### 可以做什么

- 为减脂、增肌、力量、耐力、灵活性、通用体能、运动表现或其他自定义方向建立本地训练档案。
- 在不连接训记时，使用独立的通用 `programme` 格式离线生成并校验训练计划；该格式不含训记载荷，并强制关闭远端写入。
- 使用训记完整记录进行阶段回顾，同时保留用户的人工修正。
- 区分已记录事实、用户反馈、推导值、假设和待确认信息。
- 检查训练安排、目标覆盖、恢复限制、排除动作和单变量渐进规则。
- 在用户审核当前摘要并明确确认后，将计划写回训记，再通过完整数据回读核对结果。
- 对远端记录变化、重复标题、身份不完整、已开始训练、元数据丢失或不确定写入结果采取停止策略。

### 主要优点

- **高度个性化**：训练方向和成功指标由用户定义，而不是套用固定模板。
- **证据驱动**：计划变化必须与训记记录、用户反馈或明确标注的假设对应。
- **写入难以误触**：训练数据 Key 只提供技术访问，不代表写入授权；每次写回都需要当前摘要、绑定摘要值和后续明确确认。
- **可复核**：写入后强制重新读取完整数据，检查日期、标题、训练身份、动作顺序、组数和目标字段。

### 连接训记

首次导入或导出前，请在训记 App 中找到训练数据 Key，并通过用户控制的非回显方式，将其作为当前 Agent 进程的 `XUNJI_API_KEY` 注入。该 Key 应像密码一样保管，不得写入源代码、训练档案、计划文件、Git、命令历史、日志、聊天消息或错误报告。

持有 Key 只代表具备技术访问能力，不代表已经授权写入。每次写入仍需先展示当前变更摘要，再获得针对该版本的明确确认。

纯离线计划不能直接交给写入客户端。若之后决定导出到训记，Agent 必须另行生成并展示一份 `xunji_weekly_plan_v1` 草案，重新审核后再确认；不会把本地计划静默转换成远端写入。

### 已知写入限制

训记写入接口目前没有经过验证的 compare-and-swap、revision、ETag、幂等键或服务端标题唯一性保证。本地预检、锁、事务标记和完整回读能够降低风险，但不能消除预检与写入之间的并发窗口。

使用完整写回时，不要同时在训记 App 或另一台设备修改相同日期；遇到 `write_intent`、`verify_pending`、`ambiguous` 或 `drifted` 状态时，应先执行完整回读核对，禁止盲目重试。写入不能被描述为原子操作或“恰好一次”操作。

本项目是独立社区项目，不是训记官方产品。它不提供医疗诊断、治疗、康复或紧急评估。

## English documentation

This repository packages a Codex Skill for general-purpose personalised training design and guarded Xunji writeback. Each user defines their own direction, priorities, measures, and constraints; the package does not embed one person's programme as a default.

This is an independent community project. It is not affiliated with or endorsed by Xunji.

## What it does

- Builds a local profile from goals, availability, equipment, experience, preferences, symptoms, and progression rules.
- Produces offline training drafts with a dedicated general `programme` schema when Xunji access is not requested.
- Uses full Xunji evidence for weekly review and preserves user corrections.
- Separates recorded facts, user reports, derived values, assumptions, and unconfirmed items.
- Validates schedules, API limits, exclusions, goal coverage, and single-variable progression.
- Binds write confirmation to a canonical digest of the proposed payload, the complete profile, the optional baseline, and, for updates, the full imported original snapshot.
- Refuses changed payloads, remote drift after review, normalised-title collisions on creates and updates, incomplete update identity, silent metadata loss, and overwriting sessions with completion evidence.
- Records `write_intent` before dispatch, advances acknowledged writes to `verify_pending`, and records uncertain outcomes as `ambiguous`; none is complete until full-data reconciliation passes.
- Durably records transaction directories, locks, and marker transitions before an upsert may be dispatched; a directory-sync failure stops the write.
- Serialises cooperating local writers and verifiers for each training-data-Key namespace and date, and safely resumes a multi-date plan without resending dates already verified.

## Safety model

The workflow is deliberately asymmetric: draft generation is easy to revise, while remote writes are difficult to trigger accidentally.

```text
profiled -> evidence_read -> drafted -> user_reviewed
-> explicitly_confirmed -> write_intent -> verify_pending -> fully_verified
                                  \-> ambiguous -> full-data reconciliation
fully_verified -> reverify_pending -> fully_verified
                               \-> drifted -> fresh full-data reconciliation
```

A confirmation applies only to the current digest and date range. Editing the plan invalidates it. An approval given before the summary, a standing authorisation, or “looks good” is not write authority.

This Skill is not medical advice. New or worsening red-flag symptoms stop automatic progression and require appropriate professional assessment.

## Repository layout

```text
skill/xunji-personalised-training/   Installable Skill only
tests/                               Synthetic, offline tests
scripts/release_gate.py              Local release and privacy gate
.github/workflows/validate.yml       GitHub validation workflow
```

Real profiles, training records, caches, payloads, and manifests do not belong in this repository.

## Install after review

Inspect the staged repository and run its validation and security checks before copying the Skill into a live Codex Skill directory.

Requirements: Codex with local Skills support and Python 3.10 or newer. The runtime code uses only the Python standard library.

Current local snapshot: 116 synthetic offline tests and the 24-file whitelist release gate pass. These checks do not replace the required security scan and human review.

```bash
python3 -m unittest discover -s tests -v
python3 scripts/release_gate.py
```

Then copy only the installable directory:

```bash
cp -R skill/xunji-personalised-training "${CODEX_HOME:-$HOME/.codex}/skills/xunji-personalised-training"
```

Do not clone an unreviewed repository directly into a live Skill directory.

## Create a local profile

Copy the synthetic template to a private project path:

```bash
cp skill/xunji-personalised-training/assets/user-profile.example.json /local/private/path/training-profile.json
python3 skill/xunji-personalised-training/scripts/validate_profile.py /local/private/path/training-profile.json
```

Replace every synthetic value. Keep the real file outside this repository and out of Git.

For offline-only use, remove the optional `xunji` block from the copied profile and start from `skill/xunji-personalised-training/assets/offline-weekly-plan.example.json`. That plan uses `personalised_training_plan_v1`, stores exercises under `sessions[].programme`, contains no Xunji payload, and hard-disables remote writing.

Invoke the Skill explicitly:

```text
Use $xunji-personalised-training to build a three-day general-purpose plan for my own priorities. First validate my local profile and stop after the draft for review.
```

Implicit invocation is disabled because the Skill can prepare remote writes.

## Xunji access

General planning is offline. Xunji network access occurs only when the user asks to read or write Xunji data.

An offline plan cannot be passed to the write client. Export requires a separately reviewed `xunji_weekly_plan_v1` draft and a profile whose `xunji.write_policy` is `review_then_explicit_confirm`; this prevents a local programme from silently becoming a remote write.

For first-time connection, open the Xunji app and find the training data key (训练数据 Key). Inject it into the Agent's local process as `XUNJI_API_KEY` using a non-echoing local prompt or another user-controlled launch mechanism. Treat it like a password. The value must never be placed in source files, shell history, URLs, request bodies, manifests, logs, examples, messages, or bug reports. The Skill does not query host credential stores.

With the key, the Agent can import requested training records from Xunji for local review and export a reviewed plan back to Xunji. The key authenticates the API request; it does not authorise a write. Every export still requires the current summary, matching digest, and a later explicit confirmation.

Read, prepare, write, and verify are separate commands. `prepare-write` is offline. For an update, keep the exact full imported record in `original_xunji_payload` and make `xunji_payload` a full clone containing only reviewed value changes or additions. Normal API updates cannot remove original movements, sets, nested items, or metadata; use the app for a destructive restructure.

Use the same unchanged plan and profile files for all three stages. Every update uses its exact `original_xunji_payload` as a mandatory internal baseline: every actual dose change must be declared exactly, and every changed variable must already have comparable evidence in that imported snapshot. The comparison includes set count, load, repetitions, duration, distance, calories, intensity or heart rate, difficulty, rest or density, tempo, range of motion, nested metric paths at every depth, target positions, aliases, and load units. An imported legacy difficulty value may be preserved unchanged, but any new or changed difficulty must be exactly `easy`, `normal`, or `hard`. An unrecognised metrics leaf becomes its own JSON-Pointer-style `metric:/normalised/path` variable instead of being ignored; escaped path tokens distinguish literal separators from real nesting. An added set counts only as a set-count change when its complete target signature matches one comparable existing set; evidence is never assembled across different sets, and a novel target is counted as another variable. Adding another occurrence of an existing movement name is still `exercise_selection`, not a hidden set change. Newly created sessions, movements, sets, and nested items use known-field allowlists, while unknown or unrelated metadata additions, removals, or changes fail closed. An external `--baseline` is required when the plan's complete progression declaration is not fully and unambiguously covered by update snapshots, including whenever a declared movement name also appears in a create operation. Once supplied, it must comparably cover every declaration; empty, unrelated, or unchanged external baselines do not satisfy that rule. The same unchanged external baseline file must be used at every stage:

```bash
python3 skill/xunji-personalised-training/scripts/xunji_client.py prepare-write \
  --plan /local/private/path/weekly-plan.json \
  --manifest /local/private/path/write-manifest.json \
  --profile /local/private/path/training-profile.json \
  --baseline /local/private/path/comparable-baseline.json

python3 skill/xunji-personalised-training/scripts/xunji_client.py write \
  --plan /local/private/path/weekly-plan.json \
  --manifest /local/private/path/write-manifest.json \
  --profile /local/private/path/training-profile.json \
  --baseline /local/private/path/comparable-baseline.json \
  --expected-digest DIGEST_FROM_PREPARE \
  --write-confirmed

python3 skill/xunji-personalised-training/scripts/xunji_client.py verify \
  --plan /local/private/path/weekly-plan.json \
  --manifest /local/private/path/write-manifest.json \
  --profile /local/private/path/training-profile.json \
  --baseline /local/private/path/comparable-baseline.json
```

The manifest digest binds the complete profile, the optional baseline, and the reviewed Xunji records. Any bound file change invalidates the binding. Before dispatch, `write` durably stores a credential-namespace `write_intent`. An acknowledged response advances it to `verify_pending`; an uncertain response advances it to `ambiguous` when possible. `verify` reconciles any of those unresolved states with a new full-data read. A historical `fully_verified` marker first becomes blocking `reverify_pending`; timeout or interruption leaves it blocking, a mismatch becomes `drifted`, and only an exact fresh read-back restores that digest to `fully_verified`. A first-time mismatch keeps its original `write_intent`, `ambiguous`, or `verify_pending` evidence. The same drifted digest cannot be rewritten; a different digest may proceed only through a new manifest, explicit confirmation, and live full-data preflight. Never resend while an unresolved initial-write marker exists.

The client holds an exclusive local lock for each training-data-Key namespace and date during reads, writes, and verification. Transaction locks and markers use one fixed per-user root; `--cache-dir` and `XDG_CACHE_HOME` can relocate ordinary read caches but cannot relocate transaction state. Created directory entries, locks, and marker replacements are synchronised to their parent directories; failure to establish that durability stops dispatch. A lock already held by another process blocks the operation. A lock left after a crash also blocks by design; inspect the process and transaction marker rather than deleting it blindly.

Every version-3 ordinary read-cache entry is bound to the full training-data-Key fingerprint, exact date, full/light mode, and fixed transaction epoch. Cache roots, account directories, and entries must be owner-controlled private regular paths; symbolic links, unexpected owners, and group/other access fail closed. Once a date has any transaction marker, ordinary reads remain available but bypass all movable caches. This prevents a write-intent crash, ambiguous response, verified transition, or later remote change from being hidden by a previously cached response.

If a multi-date write stops after only some dates were dispatched, run `verify` again with the same unchanged full plan, profile, baseline, and manifest. It reconciles only dates with matching transaction markers and reports undispatched dates without contacting them. Then rerun the same `write` command: dates already `fully_verified` are skipped, while only undispatched dates may proceed. Any unresolved mismatch still blocks resending.

Every `verify` invocation performs a fresh full-data read for each dispatched date, including dates already marked `fully_verified`. Create and update candidates must each be the unique normalised-title match for the date; updates must also have the unique reviewed `localid`. The marker records a completed transaction; it is never a substitute for checking the current remote record.

See `skill/xunji-personalised-training/references/xunji-api-and-writeback.md` for the complete contract.

## Privacy

The code contains no telemetry and permits API traffic only to `https://trains.xunjiapp.cn`. A real read or write sends the selected Xunji record or plan to Xunji, so the user must understand that boundary before authorising it. No other service should receive training data. The release gate detects concrete macOS, Linux, and Windows home paths while allowing only explicit public placeholder account names.

See [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md).

## Current limitations

- Chinese Xunji movement names must be checked before real writeback; the synthetic examples are illustrative.
- The Skill does not provide nutrition treatment, diagnosis, rehabilitation, or emergency assessment.
- Public Xunji API behaviour and rate limits may change; verify them before release and treat unknown responses conservatively.
- The first release uses JSON templates and the Python standard library to keep local validation reproducible.
- Offline planning and reads are portable, but guarded write and verification transactions currently require a POSIX host so the fixed per-user state root cannot be redirected through mutable home-directory environment variables.
- Read caches and fixed-root transaction controls are partitioned by a fingerprint of the current training data Key because no stable account identifier is available in the reviewed contract. Finish or reconcile every pending transaction before replacing a Key; a new Key cannot be assumed to see the old namespace.
- SkillSpector 2.5.1 still reports the known `LP3` schema mismatch described at the top of this README. Treat the result as a disclosed non-zero finding, not a zero-finding scan.
- Xunji exposes no verified compare-and-swap, revision, ETag, idempotency, or server-side title-uniqueness precondition in the reviewed API contract. A fresh full-data preflight narrows but cannot remove the update race, and two concurrent create-only clients can both pass the duplicate-title check before creating duplicate sessions. The local lock serialises only cooperating processes on the same host and Key namespace. The repository owner has accepted publication of guarded writeback with this residual risk; that acceptance does not make updates atomic or creation exactly once.

## Licence

The draft uses the MIT Licence. Review the licence choice before publishing. No third-party movement catalogue or Xunji application code is bundled.
