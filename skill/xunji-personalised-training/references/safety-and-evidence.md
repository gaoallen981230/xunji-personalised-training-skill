# Safety and evidence boundaries

## Scope

This Skill supports training organisation and record handling. It does not diagnose, treat, rehabilitate, or replace an appropriately qualified clinician or coach.

## Non-overridable stop rules

Stop automatic progression and high-intensity programming when the user reports a new or worsening red flag, including:

- chest pain, fainting, or severe unexplained breathlessness;
- new neurological weakness, numbness, loss of coordination, or radiating symptoms;
- an acute injury with major swelling, deformity, or inability to bear normal load;
- symptoms that a clinician has told the user require assessment;
- any emergency symptom or rapidly worsening condition.

Advise the user to seek appropriate medical assessment. For a possible emergency, advise use of local emergency services. Do not provide a diagnostic explanation.

No profile option may disable these stop rules.

## Conditions and return to training

- Preserve clinician restrictions exactly as user-reported.
- If clearance is required but not obtained, do not create a progressive programme.
- After illness, injury, surgery, pregnancy-related change, or a long lay-off, begin with a conservative re-entry based on current capacity rather than old performance.
- Do not infer that an exercise is safe because it appeared in historical records.
- Use pain and symptom behaviour as constraints, not as a diagnosis.

## Evidence classes

Keep these categories separate in every review:

- `recorded`: Direct API field or local source value.
- `user_reported`: A correction, symptom, recovery score, or completion statement from the user.
- `derived`: A transparent calculation from recorded values.
- `assumed`: A provisional planning choice made because evidence is missing.
- `unconfirmed`: A material unknown that requires the user.

Do not merge comment-parsed RPE with structured RPE in longitudinal statistics. Do not interpret `done=false` as non-completion when a completion-specific signal or user correction exists. Do not treat target time or distance as observed work.

## Training-load interpretation

- A single session cannot establish adaptation or causality.
- Completion is not proof that the dose was appropriate.
- Low RPE without structured recording may be useful qualitative evidence, but it is not a precise longitudinal measure.
- Soreness is neither required nor sufficient evidence of hypertrophy.
- Weight change cannot be attributed to a programme without appropriate dietary, measurement, and time context.
- A performance test is specific to its protocol and conditions.

## Privacy

- Store real profiles, check-ins, plans, manifests, caches, and API results locally.
- Never commit them to the Skill repository.
- Never include credentials in commands, URLs, request bodies, logs, screenshots, examples, or bug reports.
- Use synthetic or thoroughly anonymised fixtures for tests.
- Tell the user before a Xunji operation that the selected record or plan will be sent to the Xunji service.
- Do not send training data to movement databases, language models, analytics, telemetry, or error-reporting services.

## Public evidence used by this Skill

- WHO Guidelines on physical activity and sedentary behaviour: https://www.who.int/publications/i/item/9789240015128
- ACSM resistance-training position stand for healthy adults: https://pubmed.ncbi.nlm.nih.gov/41843416/
- Resistance-training prescription network meta-analysis: https://pubmed.ncbi.nlm.nih.gov/37414459/
- Aerobic, resistance, and concurrent training body-fat review: https://pubmed.ncbi.nlm.nih.gov/40405489/

These sources inform general defaults. They do not establish an individual medical recommendation.
