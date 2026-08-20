# Programme design policy

## Contents

1. First-principles model
2. Goal-specific emphasis
3. Schedule and exercise selection
4. Progression
5. Recovery and deloading
6. Draft quality checks
7. Evidence anchors

## First-principles model

A programme is an allocation of limited time and recovery capacity towards ranked outcomes. Do not begin with a fashionable split or template. Begin with the user's task, success measure, constraints, recent evidence, and minimum effective work.

For every programme decision, state:

- what outcome it serves;
- what evidence supports it;
- what assumption remains;
- how success or failure will be measured;
- what will trigger progression, maintenance, substitution, or regression.

Use goal weights to resolve trade-offs. Do not divide time equally across goals unless their weights and recovery cost justify it.

## Goal-specific emphasis

### Fat loss

Treat exercise as one part of body-mass management, not proof of an energy deficit.

- Preserve resistance training to support strength and fat-free mass.
- Add sustainable aerobic or mixed conditioning within the user's recovery and adherence limits.
- Progress weekly activity conservatively; do not increase conditioning volume and lower-body resistance stress together when recovery evidence is weak.
- Track adherence, activity dose, performance retention, recovery, and the user's chosen body-composition trend.
- Do not promise a specific weight-loss rate or infer causality from one week of training records.

### Hypertrophy

- Allocate recoverable weekly sets by target muscle group, movement stability, experience, and proximity to failure.
- Start at the lowest volume likely to produce progress, especially for new or returning users.
- Use multiple sets and sufficient weekly exposure, but do not impose a universal high-volume target.
- Prefer stable movements when the goal is local muscular stimulus; complexity is not a benefit by itself.
- Use repetitions, set count, load, range, and exercise selection as separate variables.
- Do not use soreness as proof of an effective session.

### Strength

- Define the lifts or movement patterns that matter.
- Place the highest-priority strength work early enough to preserve quality.
- Use heavier loading only when experience, technique, equipment, symptoms, and recovery support it.
- Track completed work, load, repetitions, RPE or RIR, and technique constraints.
- Avoid turning a strength goal into repeated maximal testing.

### Endurance

- Define the event, duration, distance, terrain, or physiological demand.
- Establish consistent low- to moderate-intensity volume before adding frequent hard intervals.
- Progress duration, distance, frequency, density, or intensity as distinct variables.
- Match impact and modality to the user's history, equipment, and symptoms.

### Functional fitness

“Functional” is not an exercise category. Translate it into tasks such as carrying, climbing, hiking, getting from the floor, repeated work bouts, or balance.

- Map each task to strength, power, aerobic capacity, muscular endurance, mobility, balance, and skill.
- Select the smallest set of exercises that transfers plausibly to those capacities.
- Use a task-specific assessment rather than circuit novelty as the success measure.

### General health

- Prioritise repeatable mixed activity, confidence, and adherence.
- Use population guidelines as a direction of travel, not a compulsory first-week dose.
- Scale volume and intensity to current capacity and increase gradually.

### Custom or mixed goals

Use this route for any direction outside the general goal types. Decompose the outcome into measurable components. Give each component a weight, a minimum dose, a success measure, and a fatigue cost. Protect the primary outcome; maintain secondary outcomes with the smallest useful dose.

## Schedule and exercise selection

### By weekly availability

- One day: full-body or task-specific essentials; explain what cannot be developed well.
- Two days: normally use two full-body sessions or two task-specific mixed sessions.
- Three days: use full-body, upper/lower/full-body, or goal-specific mixed sessions.
- Four or more days: split only when it improves quality, schedule fit, or recovery.

Do not use a body-part split that leaves a goal-dependent movement exposed only once when the user frequently misses that day.

### By session duration

When time is short:

1. Keep the highest-priority task or compound movement.
2. Keep the minimum warm-up and safety preparation needed for the session.
3. Pair non-competing movements when technique and equipment allow.
4. Remove low-priority accessories before reducing the primary goal dose.

### By equipment

Substitute by purpose:

- movement pattern and joint action;
- target muscle or performance task;
- stability demand;
- range and loading options;
- symptom and skill constraints.

Do not substitute solely because two exercises look similar.

## Progression

Use this decision sequence for each movement or training purpose:

1. Confirm the planned work was actually completed.
2. Check structured RPE or RIR, technique, symptoms, and recovery.
3. Check that the same movement or purpose is comparable across weeks.
4. Select at most the configured number of variables.
5. Record the reason and a regression gate.

Common ladders are configurable:

- Strength or hypertrophy: repetitions, then sets, then load, with range controlled separately.
- Aerobic endurance: consistent duration or distance, then frequency or intensity.
- Power: execution quality, then small volume or complexity changes; never chase fatigue.
- Mobility: control, then usable range, then hold duration.
- Skill: stable success rate, then context complexity or speed.

Maintain or regress when completion falls, RPE rises materially at the same work, recovery declines, technique becomes unstable, or symptoms appear. Missing RPE is not evidence that a session was easy.

## Recovery and deloading

Use recent evidence rather than a calendar alone. Review a deload or regression when several signals align, such as:

- repeated performance decline;
- elevated RPE at unchanged work;
- incomplete sessions;
- persistent soreness or fatigue;
- worsening sleep or recovery;
- new pain or neurological symptoms;
- a planned event or unusually dense activity week.

A deload may reduce volume, intensity, complexity, impact, or session count. Change the minimum necessary variable and state why.

## Draft quality checks

Before showing a plan, confirm that:

- every session serves a declared goal;
- the weekly schedule fits the available days and time;
- required equipment exists at the planned location;
- excluded movements and hard constraints are absent;
- high-fatigue sessions do not collide without a stated reason;
- new exercises have a learning rationale and conservative dose;
- progression variables do not exceed the profile limit;
- set count, load, repetitions, duration, distance, calories, intensity, density or rest, tempo, range, movement difficulty, and exercise selection are treated as separate progression variables; previously unseen metrics leaves use collision-free JSON-Pointer-style `metric:/normalised/path` variables;
- an added set is only a set-count progression when its complete target signature matches one comparable existing set; never assemble evidence across different sets, and count novel targets separately;
- another occurrence of an existing movement name remains an exercise-selection change, while dose comparison stays scoped to occurrences present in the baseline;
- facts, user reports, assumptions, and unconfirmed items are separate;
- a regression gate is present for each progression;
- Xunji payload limits are respected.

## Evidence anchors

The defaults are deliberately broad because individual response, adherence, experience, and recovery modify the prescription.

- The World Health Organization publishes population-level physical-activity and sedentary-behaviour guidance for adults and relevant subpopulations. Use it as a health anchor, not a mandatory starting dose: https://www.who.int/publications/i/item/9789240015128
- The 2026 American College of Sports Medicine position stand synthesised 137 reviews and found that progressive resistance training improves strength, hypertrophy, power, endurance, and physical function; prescription variables should be matched to the target outcome: https://pubmed.ncbi.nlm.nih.gov/41843416/
- A network meta-analysis of healthy adults found that many resistance prescriptions improve strength and hypertrophy, with heavier loads favouring strength and multiple-set prescriptions ranking highly for hypertrophy: https://pubmed.ncbi.nlm.nih.gov/37414459/
- A 2025 systematic review found different trade-offs between aerobic, resistance, and concurrent training for fat mass and fat-free mass. Use this to support mixed programming without promising individual fat loss: https://pubmed.ncbi.nlm.nih.gov/40405489/

Treat these as general evidence for healthy adults. Do not apply population findings mechanically to a person with symptoms, a medical condition, pregnancy, or a rehabilitation need.
