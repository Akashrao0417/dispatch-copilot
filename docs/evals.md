# Evals

30 questions with known correct answers ([`evals/questions.json`](../evals/questions.json)):
15 SOP questions (expected section + required facts), 7 that must get **"I don't know"**, and
8 job questions whose correct job IDs are computed independently with pandas from the tables.
Plus 10 **held-out** questions ([`evals/questions_holdout.json`](../evals/questions_holdout.json))
written after the fixes and run once, never tuned on.

```bash
python -m evals.run_evals --label baseline
python -m evals.run_evals --label holdout --questions questions_holdout.json
```

Data: deterministic dataset as of 2026-11-19 14:00, loaded into its own `dispatch_eval` database.
Mode: offline (no `ANTHROPIC_API_KEY`), so answers are extractive and cost is $0. With a key set,
the same runner reports Claude tokens and cost (set `PRICE_IN_PER_MTOK` / `PRICE_OUT_PER_MTOK`).

## Results

| Run | Qs | Accuracy | SOP | I don't know | Jobs | Citation rate | SOP citation hit | Latency p50 / p95 | Cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Baseline | 30 | **70%** | 60% | 71% | 88% | 96% | 60% | 0 / 3 ms | $0.00 |
| After fixes | 30 | **100%** | 100% | 100% | 100% | 96% | 100% | 0 / 3 ms | $0.00 |
| Held-out (new questions) | 10 | **90%** | 100% | 50% | 100% | 100% | 100% | 0 / 3 ms | $0.00 |

*Citation rate* = answered questions (not "I don't know") that cite at least one SOP section.
The one uncited answer is a correct "no jobs are late" reply, which has nothing to cite.

## Baseline failures (9) and the worst 5

Ranked by harm in production — a confident wrong answer is worse than a refusal:

| # | Question | What went wrong | Root cause | Fix |
|---|---|---|---|---|
| 1 | idk-02 "How do I reset my email password?" | Answered with an unrelated SOP section | Synonym expansion ("email" → "update") let one word pass the evidence gate | Gate now counts how many of the **user's own words** the section supports (≥ 2) |
| 2 | idk-03 "What's the overtime pay rate for technicians?" | Same | Same, plus "what's" split into "what" + "s", and "s" matched "technician's" | Same gate; contraction fragments ignored |
| 3 | job-06 "Which Gold customers have late jobs today?" | Listed all 15 late jobs, not the 3 Gold ones | No tier filter | Copilot filters by Gold / Silver / Bronze when named |
| 4 | sop-04 "Which delayed jobs should I handle first?" | Sent to the database instead of the SOP | Router treated any question with "delayed" + "jobs" as a lookup | Router sends procedure questions ("should I", "how often", "when does", "deadlines") to the SOPs and requires a data cue (today, a city, "my jobs") for lookups |
| 5 | sop-01 "When does a job count as delayed?" | Same | Same | Same |

The router fix also fixed sop-02, sop-03 and sop-06. The last one, sop-11 (cited §6 instead of §3), was
a stemming miss ("choose" vs "choosing") and was fixed too. **After fixes: 30/30.**

## Held-out: honest number

On 10 new questions: **90%**. The miss:
"How many sick days do drivers get per year?" returned SOP-RA §2, which really contains
"sick" and "technician". Keyword retrieval can't tell that the question is about HR policy.
Left unfixed on purpose (tuning on the held-out set would make it meaningless).
Next step: semantic retrieval or Claude mode, where the model is told to answer "I don't know"
unless the section actually answers the question — then re-run both sets.

## Limits

- 30 + 10 questions is a small set; one question is 3 points of accuracy.
- Offline latency (~0-3 ms) and $0 cost are for extractive mode. Claude mode adds a model call per SOP answer.
- Job answers are graded on job IDs only; the "why" text comes straight from tickets.
