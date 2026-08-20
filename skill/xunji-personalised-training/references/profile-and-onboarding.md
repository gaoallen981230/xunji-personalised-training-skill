# Profile and onboarding

## Contents

1. Purpose
2. Minimum onboarding
3. Evidence and inference rules
4. Profile fields
5. Goal types
6. Conflict handling
7. Updating a profile

## Purpose

Use the profile for stable personal constraints and preferences. Keep transient sleep, soreness, schedule changes, symptoms, and weekly performance in a separate weekly check-in. This prevents one difficult week from becoming a permanent programme rule.

The profile is local personal data. Store the real file in the user's chosen project, never in the installed Skill or its Git repository.

## Minimum onboarding

Ask these six groups together. Reuse confirmed answers and ask only for material gaps.

1. What should change over the next 4–12 weeks? Ask for a primary goal, any secondary goal, and a success measure.
2. Which days, durations, locations, and equipment are available?
3. What training has the user done, and how consistent were the last four weeks?
4. Are there current symptoms, clinician restrictions, pregnancy-related considerations, or movement patterns that must be avoided?
5. Which movements or training styles are preferred, required, disliked, or excluded?
6. Will the user log RPE? If, and only if, they request Xunji import or export, ask whether the Skill may read the relevant history, whether they have found the training data key (训练数据 Key) in the Xunji app, and whether access is read-only or permits a separately confirmed write for each reviewed draft. Do not ask an offline-only user for a Key.

Do not turn onboarding into an interrogation. Infer locale, units, and timezone from reliable local context when available, show the inference, and let the user correct it.

## Evidence and inference rules

| Field | May infer from Xunji | Must ask or confirm |
|---|---:|---:|
| Recent session frequency | Yes | If records are incomplete |
| Common movements and recent loads | Yes | If a movement name or unit is ambiguous |
| Completion and structured RPE | Yes | If the user reports a logging correction |
| Training age or technical skill | No | Yes |
| Current goal and priority | No | Yes |
| Pain, diagnosis, pregnancy, or medical clearance | No | Yes |
| Available equipment outside recorded sessions | No | Yes |
| Preferences and disliked movements | No | Yes |

Label each planning input as `recorded`, `user_reported`, `derived`, `assumed`, or `unconfirmed`. Do not silently promote an assumption into a fact.

## Profile fields

Use `schema_version: "xunji_training_profile_v1"` for backwards compatibility. The top-level `xunji` block is optional. Omit it for offline-only planning; if present, `write_policy` must remain `review_then_explicit_confirm`. Never store the training data Key in the profile.

### Identity and presentation

- `profile_id`: A local non-sensitive identifier. Do not use an email address, student number, or API account.
- `locale`: Output language and regional formatting.
- `timezone`: IANA timezone used to interpret dates.
- `units`: `metric` or `imperial`.

### Goals

`goals` is a list. Each entry contains:

- `type`: one supported goal type;
- `priority`: a decimal from 0 to 1; all priorities must sum to 1;
- `outcome`: a concrete result written in the user's terms;
- `time_horizon_weeks`: optional review horizon;
- `success_metrics`: one or more observable measures.

Use `functional_targets` for functional fitness or custom outcomes. Each target should specify a real task, relevant capacity, and an assessment, for example carrying loads comfortably, climbing stairs, completing a hike, or maintaining balance during a chosen task.

### Availability

- `sessions_per_week`: The realistic planned count, not an aspirational maximum.
- `available_days`: Lower-case weekday names that form the hard scheduling boundary.
- `preferred_days`: A preferred subset of `available_days`; using another available day should produce a visible warning rather than an error.
- `fixed_days`: Sessions that cannot move.
- `session_duration_minutes.default`: Normal planning budget.
- `session_duration_minutes.maximum`: Hard ceiling.
- `minimum_rest_days_per_week`: A schedule constraint, not a recovery guarantee.

If the requested sessions do not fit the days or time budget, report the conflict and ask which goal loses priority.

### Training background

Record overall level and modality-specific exposure separately. A user may be advanced in strength training and new to running, or the reverse.

Useful fields include:

- `overall_level`: `novice`, `intermediate`, or `advanced`;
- `resistance_years` and `endurance_years`;
- `recent_consistency_weeks`;
- `current_weekly_sessions`;
- movement- or task-specific notes when they affect exercise selection.

Do not infer technical competence from years alone.

### Equipment and environments

List `environments`, `available`, and `unavailable` equipment. Resolve substitutions by movement purpose and constraints, not by matching a similar-looking exercise.

### Preferences

Keep distinct lists for:

- `liked_movements`;
- `required_movements`;
- `excluded_movements`;
- `avoid_patterns`;
- `session_style`;
- `exercise_order_rules`.

An exclusion is a hard constraint. A dislike is a preference that may be discussed.

### Health and safety

- `medical_clearance`: `not_required`, `cleared_with_constraints`, `required_not_cleared`, or `unknown`.
- `clinician_constraints`: User-provided instructions; do not reinterpret them.
- `red_flags`: Current user-reported stop signals. Any non-empty value blocks automated progression.
- `current_symptoms`: Describe location, severity, behaviour, and change without diagnosing.
- `stop_pain_score`: A conservative 0–5 threshold. Use a lower clinician-specified threshold when present.
- `stop_rules`: Non-overridable symptom rules.

`required_not_cleared` blocks programming beyond low-risk information and referral back to the relevant clinician.

### Progression

- `variable_order`: User-selected order such as repetitions, sets, then load.
- `max_variables_per_movement_per_week`: Normally 1; never use a higher value merely to accelerate results.
- `maximum_sets_per_movement`: A planning ceiling, not a target.
- `deload.mode`: `readiness_triggered`, `scheduled`, or `hybrid`.
- `regression_triggers`: Completion, RPE, recovery, pain, or technique signals that hold or reduce work.

### Xunji

- `read_records`: Whether the user authorises Xunji reads for this workflow.
- `write_policy`: Must remain `review_then_explicit_confirm`.
- `use_full_data_for_weekly_review`: Must remain true.
- `use_full_data_for_verification`: Must remain true.
- `omit_planned_session_duration`: User preference for Xunji payloads.

No profile setting may enable automatic writeback.

## Goal types

- `fat_loss`: Support sustainable activity and lean-mass retention; do not promise a rate of weight loss or prescribe nutrition without a separate qualified scope.
- `hypertrophy`: Prioritise recoverable resistance-training volume and movement quality.
- `strength`: Prioritise force production in selected movement patterns or lifts.
- `endurance`: Prioritise task-specific aerobic or muscular endurance.
- `functional_fitness`: Define the real-life tasks and capacities first.
- `general_health`: Use sustainable mixed activity and adherence as primary constraints.
- `custom`: Define any user-selected direction by decomposing it into measurable strength, size, endurance, power, mobility, balance, or skill components.

Hybrid programmes use multiple weighted entries rather than a vague `hybrid` label.

## Conflict handling

Use this order when constraints conflict:

1. Stop rules and clinician constraints.
2. Current symptoms and recovery.
3. Time, equipment, and fixed schedule.
4. Higher-priority goal.
5. Lower-priority goal.
6. Preferences.

Return unresolved conflicts as `unconfirmed`; do not create false precision.

## Updating a profile

Change stable profile fields only after the user confirms the change. Record weekly deviations in the check-in. Revalidate the profile after any goal, availability, equipment, safety, or progression-policy change.
