# Game Intelligence Flywheel — Technical Reference

---

## What this code does (current state)

This repo is a **test harness** for two components of the flywheel: the memory query interface and the template refiner. It is not wired into the codegen pipeline. Its purpose is to validate that both components work correctly in isolation before integration.

**The five-stage test loop** (`run.py`):

```
Stage 1: playtester_agent  → play a live game via Playwright + Gemini VLM
                             output: screenshots, per-step observations, session summary
Stage 2: evaluator_agent   → score the session across 8 dimensions (0–100)
                             output: evaluation.json
Stage 3: memory_agent      → extract patterns/anti-patterns from the eval, embed them,
                             deduplicate (cosine ≥ 0.92), store in SQLite
                             output: rows in memory.db
Stage 4: query_memory      → [TEST ONLY] retrieve top-k patterns for this game spec
                             and print them with relevance reasons
                             output: patterns_extracted.json, stdout
Stage 5: template_refiner  → read all KB patterns, propose surgical diffs to
                             codegen template files (.md), write to proposed_diffs/
                             output: proposed_diffs/*.json, REFINEMENT_LOG.md
```

Stages 4 and 5 are independent. The query results from Stage 4 are not consumed by Stage 5. The refiner always reads the full KB directly.

---

## What it should do in production (features to add)

### 1. Memory query injected into codegen — not run after it

**Current:** `query_memory` runs post-hoc after a game is evaluated. Results are printed. Nothing reads them.

**Production:** When a user submits a game spec, `query_memory` is called before the codegen agent writes any code. The top-k patterns are injected into the system prompt as a memory context block:

```
[Base system prompt — instructions.md, quality_defaults.md, etc.]

[Injected memory context — from query_memory(game_spec, top_k=5)]
Relevant patterns from past playtests of similar games:

ANTI-PATTERN (playability, impact=-75): Third-person platformers where the player
character is completely unresponsive to keyboard input score ≤15 on playability.
Evidence: coin platformer session — identical screenshots, score stuck at 0 despite
20s of input.

PATTERN (objective_clarity, impact=+25): Games with a visible score counter and
clearly placed collectibles score 80+ on objective clarity without any tutorial text.
...
```

The agent uses this to reflect on past successes and failures and course-correct before generating. This is the core flywheel mechanic — the query must be interleaved in generation, not appended after evaluation.

**What needs to be built:** A hook in the codegen call site (wherever the agent receives a spec and begins generating) that calls `query_memory(spec)` and prepends the results to the system prompt.

---

### 2. Template refinement approval workflow

**Current:** The refiner proposes diffs to `proposed_diffs/*.json` and `REFINEMENT_LOG.md`. A human manually reads them and decides whether to edit the `.md` template files by hand. There is no tooling for applying, rejecting, or tracking the outcome of a proposed diff.

**Production needs:**
- **Apply tool:** A script that takes a `proposed_diffs/*.json` entry and applies the `addition` or `edit` to the target template file, with a git commit so it's reversible.
- **Outcome tracking:** After a diff is applied, tag subsequent game evaluations with the template version. Track whether mean scores on the affected dimension improve or regress.
- **Score-gated auto-apply:** For changes with confidence ≥ 0.9 and N≥10 contributing games, consider auto-applying after a human spot-check rather than requiring full review on every diff.
- **Rollback:** If a template change causes a regression (mean score drops >5 points on affected dimension over next 20 games), flag it automatically and revert.

---

### 3. Refinement cadence — gate on N games, not every run

**Current:** The refiner runs after every single game if `--template-dir` is passed. A single low-quality game can generate a low-confidence diff that clutters the log.

**Production needs:**
- Run the refiner every N games (e.g., N=10 or N=20) or when a pattern crosses a confidence threshold based on accumulated evidence count, not just LLM-judged confidence.
- Add a `times_seen` counter per pattern cluster so the refiner can distinguish "this failure happened once" from "this failure happened in 15 of the last 20 games."

---

### 4. Scale the KB storage and retrieval

**Current:** All embeddings are loaded into memory on every insert (for dedup) and every query (for similarity search). `memory_agent.py:201` does a full table scan. Works fine at <1K patterns; breaks at 10K+.

**Production needs:**
- Replace the in-memory scan with an ANN index (FAISS, or pgvector if moving to Postgres).
- Filter by `game_type` and `mechanic_type` before embedding comparison — most queries only care about a relevant subset.
- Move from SQLite to Postgres when running parallel game sessions (SQLite WAL handles concurrent reads fine but serializes writes).
- Add pattern clustering / consolidation: periodically merge semantically similar patterns into a canonical form so the KB doesn't accumulate 50 variations of "movement is broken."

---

### 5. Template refiner prompt scaling

**Current:** The refiner serializes every pattern in the KB into one prompt (`_format_patterns`, `template_refiner_agent.py:170`). At 57 patterns this is ~5K tokens. At 5K patterns it's ~500K tokens (fits in Gemini's 1M window but latency and coherence degrade). At 50K patterns it fails completely.

**Production needs:**
- Pre-filter patterns by dimension before passing to the refiner. Run separate refiner passes per template file, each seeing only patterns relevant to that file's domain (e.g., `quality_defaults.md` only sees `visual_coherence` and `feedback_loops` patterns).
- Alternatively, cluster patterns first and pass one representative per cluster, with a count of how many games contributed to that cluster.

---

## The 6-month picture

**Assuming ~10 games/day, production integration in place:**

**Months 1–2:** KB fills with ~2K patterns. Most are variations of the same early failures — broken movement, missing feedback, no win condition. The refiner proposes the same 3–5 template changes repeatedly. Once approved, the next games using updated templates should score higher on playability and feedback_loops. Signal that it's working: the unresponsive-controls anti-pattern stops appearing in new evaluations.

**Months 3–4:** Low-hanging failures are gone from templates. The KB accumulates higher-order patterns — difficulty curve issues, multiplayer sync bugs, mobile layout regressions. Refiner diffs get more surgical. Confidence scores drop because each new failure mode has fewer examples. Human review becomes more critical, not less.

**Months 5–6:** Two paths:

- *Scale infrastructure addressed* (ANN index, pattern clustering, per-dimension refiner passes): The KB functions as institutional memory. New game specs bootstrap from accumulated knowledge about similar games. Template changes compound — each approved diff raises the floor for the next thousand games.

- *Scale infrastructure not addressed*: The KB is a 20K-row table that takes 45 seconds per insert, the refiner prompt overflows, and the loop stalls. The query interface still works as a human-facing lookup tool, but the automated flywheel stops.

The core bet: the quality ceiling on codegen is not the model, it's the accumulated prompt knowledge. The flywheel is the right architecture for that. Production integration and the scale work are what turns this test harness into a compounding system.
