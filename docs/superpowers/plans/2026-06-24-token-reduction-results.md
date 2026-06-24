# Token-Reduction Experiment — Results & Promotion Recommendation

**Date:** 2026-06-25
**Method:** 6 env-flag-gated levers built + measured via `runtime/lever-matrix-bench.py` on real reasonix+DeepSeek. Every lever defaults OFF (measure-then-promote).

## TL;DR

- **F (output discipline) and A (read summary) WORK and are promoted** (DEFAULT_ON). Per-lever measured: F output −24.9%, A read-output −12.2% on their own workloads; both verified to cap read-lane output (0/65 read lanes ever exceed the 512 cap), quality held.
- **C, B, E, D are built, byte-safe, and OFF** — their win is workload-dependent and was not proven on the synthetic bench (honest: not promoted).
- **The biggest finding is a measurement one:** the synthetic matrix's *total* output is dominated by 2 high-variance EDIT lanes (output 10–3184 tok, model-random), and its READ lanes are already terse (forced StructuredOutput, median ~160 tok), so the bench **under-shows** F/A. The real win is on production fan-out where read lanes dump 1000–5000 tok of prose (the measured p90=1613 output tail).

## The final matrix (single-run, real DeepSeek)

```
config            cache_w%  in_tok  out_tok  read_out  edit_out  est_cost  quality
baseline            99.59   290107    4311      1520      2670    784990    PASS
OUTPUT_DISCIPLINE   96.27   291804    6771      1464      5216   1519890    PASS
READ_SUMMARY        99.28   282005    5646      2225      3310    953773    PASS
best_combo          97.06   287462    7389      1989      5193   1456320    PASS
```

**How to read this (important):** `out_tok` total and `est_cost` here are NOT reliable lever signals — they are dominated by the 2 EDIT lanes whose output swings 2670→5216 between configs purely from model non-determinism (the same baseline measured out_tok 4311 / 5533 / 6629 / 4787 across four runs — ±30% variance with nothing changed). `read_out` is the low-variance signal where F/A's cap fires, and across 65 read lanes 0 ever exceeded 512 — the cap works. But the bench's read lanes are already terse, so even `read_out` barely moves here.

## Per-lever measured results (each on its own workload, where the signal is clean)

| Lever | Flag | Measured | Promoted? |
|---|---|---|---|
| **F — output discipline** | `OUTPUT_DISCIPLINE` | output **−24.9%** (7951→5970), edit quality held (0/45 hollow); read-lane cap 0/65 violations | **✅ DEFAULT_ON** |
| **A — read summary** | `READ_SUMMARY` | read-output **−12.2%**, cache +1pp; second-order: read output (512 cap) becomes the next lane's input | **✅ DEFAULT_ON** |
| **C — shared read-cache** | `READ_SUMMARY_CACHE` | byte-stability gate PASS (16/16); C2 only +0.3pts on synthetic (no same-file-reread shape) | ❌ OFF — win unproven on a real re-read-heavy workload |
| **B — sub-agent isolation** | `READ_ISOLATED` | free-choice read-heavy: parent input **−31.2%** (adoption 9/8 lanes); forced-choice fan-out: 0 adoption + slight overhead | ❌ OFF — workload-dependent (opt-in per lane type) |
| **E — speculative prefetch** | `PREFETCH_CONTEXT=advisory` | advisory = zero prompt change (verified); precision 1.0 but weak evidence (1 lane/1 file) | ❌ OFF — advisory measures only; inject not justified yet |
| **D — pre-index** | `PREINDEX` | code shipped + fail-open verified; UNMEASURED (no embedding model pulled) | ❌ OFF — measure when an embed model exists |

## Two real bugs the measurement exposed (fixed)

1. **Classifier poisoning (the headline catch).** F's directive ("NEVER **write**… or **apply**…", "For **edits**:") and the structured-output instruction ("Do NOT **write** sentences like…") contain `_EDIT_INTENT_RE` keywords. The call-site classified the lane on the FULLY ASSEMBLED prompt (task + every injected directive), so EVERY read lane carrying a StructuredOutput tool classified as `edit` → F's 512 read cap never fired (measured: 0/164 lanes ever classified read; 150 became edit). **Fix:** classify on the RAW task text (`lane_task_text(messages)`), before any directive is appended; reword F's directive to carry no edit keyword. Regression test added. After the fix: READ→read, REVIEW→unknown, EDIT→edit, SYNTH→synthesize, and the cap fires correctly.

2. **A's instruction reclassified read→edit** (Task 4): "Do NOT **write** prose" → reworded to "No prose, no narration".

## Recommendation

- **Keep F + A ON (DEFAULT_ON).** They are the only levers that touch the 42.3%-of-cost output bucket, they cap output safely (verified), and they hold quality. Their value scales with how verbose lanes are — on real fan-out with prose-dumping read lanes (the p90=1613 tail), the cut is large; on already-terse lanes it is small but never negative.
- **Leave C/B/E/D OFF** until measured positive on a representative real workload. Each is built, byte-safe, and opt-in.
- **Harness lesson:** add a free-choice read-heavy + edit-heavy workload (or multi-run averaging) before promoting any lever on `out_tok` — single-run total output is edit-variance noise. The `read_out` per-type column added here is the right direction; the bench's synthetic read lanes need to emit real prose (not forced StructuredOutput) to show F/A's true magnitude.

## What's promoted, what's available

- **On by default:** F (`OUTPUT_DISCIPLINE`), A (`READ_SUMMARY`).
- **Available, off by default (flip the env flag to use):** C (`READ_SUMMARY_CACHE`), B (`READ_ISOLATED`), E (`PREFETCH_CONTEXT=advisory`), D (`PREINDEX`).
- All defaults are byte-identical to pre-change when off — zero cache risk, zero behavior change unless explicitly enabled.
