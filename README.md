# Game Intelligence Flywheel

**generated game URL → VLM playtester → structured evaluation → memory agent extracts patterns → queryable knowledge base → automatic template refinement → codegen gets smarter over time**

> Research prototype. Standalone system — no modifications to the existing codegen pipeline required.

---

## What it does

The existing codegen system knows if a game *compiles*. It has no idea if it's *good*. And every generation starts from zero — none of the signal from past games is reused.

This pipeline closes those gaps:

```
NAIVE (what exists):
  generate game → playwright catches runtime errors → done
  next game starts from zero, no memory of what worked

FLYWHEEL (what this builds):
  generate game → agent plays it → structured critique
               → memory layer ingests critique → extracts patterns
               → query interface informs next generation at prompt-time
               → template refiner proposes permanent improvements to codegen templates
               → better codegen → better game → better critique → richer memory
```

---

## Pipeline stages

```
Stage 1: playtester_agent  → Playwright loads game, VLM takes screenshots at t=0,5,15,30s
                             simulates keyboard/click inputs, records observations per step
Stage 2: evaluator_agent   → second LLM pass scores the session across 8 dimensions (0–100)
                             output: evaluation.json
Stage 3: memory_agent      → extracts patterns + anti-patterns from eval, embeds them,
                             deduplicates (cosine ≥ 0.92), stores in SQLite
                             output: rows in memory.db
Stage 4: query_memory      → given a game spec, retrieves top-k relevant patterns from KB
                             with relevance reasoning per result
Stage 5: template_refiner  → reads all KB patterns + current codegen templates,
                             proposes surgical diffs with confidence scores and attribution
                             output: proposed_diffs/*.json, REFINEMENT_LOG.md
```

---

## Modules

| File | Description |
|---|---|
| `playtester_agent.py` | Playwright + Gemini VLM game playtester |
| `evaluator_agent.py` | Structured 8-dimension evaluator |
| `memory_agent.py` | Pattern extractor + SQLite knowledge base |
| `query.py` | Semantic query interface over KB |
| `template_refiner_agent.py` | Proposes diffs to codegen template `.md` files |
| `run.py` | End-to-end pipeline entry point |
| `FLYWHEEL.md` | Integration design doc |

---

## Setup

```bash
pip install google-generativeai playwright pillow numpy python-dotenv
playwright install chromium
```

Create `.env` in the project root:

```
GEMINI_API_KEY=your_key_here
```

---

## Run

```bash
python3 run.py --url "http://192.168.12.164:8083" \
               --spec "third-person platformer, collect coins on floating islands" \
               --rules /Users/qinsun/Documents/roam/output/88886666/rules.md \
               --template-dir /Users/qinsun/Documents/roam/output/88886666/ \
               --output-dir coin_outputs 2>&1 | tee run.log
```

| Flag | Required | Description |
|---|---|---|
| `--url` | yes | Live Three.js game URL |
| `--spec` | yes | Natural language game description |
| `--rules` | no | Path to game rules `.md` file |
| `--template-dir` | no | Path to codegen templates directory (enables Stage 5) |
| `--output-dir` | no | Where to write all artifacts (default: current dir) |
| `--steps` | no | Number of intervals; total duration = steps × interval (default: 4, min: 4) |
| `--interval` | no | Seconds between scheduled screenshots (default: 5) |

**Output artifacts:**

```
output-dir/
  evaluation.json          # structured scores across 8 dimensions
  patterns_extracted.json  # patterns/anti-patterns from this session
  snapshot.png             # final screenshot
  proposed_diffs/
    instructions.json      # proposed edits to instructions.md
    quality_defaults.json  # proposed edits to quality_defaults.md
  REFINEMENT_LOG.md        # human-readable audit trail of all refinement runs
```

---

## Query the knowledge base directly

```bash
python3 query.py "a platformer where you collect coins"
python3 query.py "top-down shooter with power-ups" --top-k 3
```

Returns `[]` on cold start (empty KB) — no crash.

---

## Storage design

**SQLite + Gemini embeddings (`gemini-embedding-001`).**

Each pattern row stores structured metadata (`game_type`, `mechanic_type`, `dimension`, `score_impact`, `source_game_id`) alongside a binary-packed embedding blob. Cosine similarity is computed in-memory at query time using numpy.

Reasons for this choice:
- **Zero infrastructure** — runs locally, no external services, no docker, consistent with "keep infra lightweight"
- **Full-table scan is fast enough** at the target scale (<100K patterns): numpy cosine over 100K × 768-float vectors completes in ~200ms
- **Structured metadata** enables pre-filtering by `game_type` or `dimension` before similarity search, which both improves precision and reduces scan size
- **Dedup threshold at 0.92 cosine** — tight enough to block near-duplicates, loose enough to preserve distinct observations from different games about the same mechanic

Scale limitations and migration path are documented in `FLYWHEEL.md`.

---

## Template refiner — file routing

The refiner routes each finding to exactly one template file based on what the finding is about:

| Template | Routes findings about |
|---|---|
| `system_role.md` | Agent identity, technology stack, scope boundaries |
| `instructions.md` | Game structure, module rules, ctx field ownership, network protocol, gameplay mechanics, constants |
| `quality_defaults.md` | Visual standards, art style rules, performance requirements, multiplayer correctness, error handling |
| `agent_rituals.md` | Step sequences, debugging checklists, pre/post-edit hygiene, when to check contracts |

The refiner proposes additions and targeted edits — never full rewrites. Every proposed change includes:
- `confidence` (0.0–1.0): 0.9+ = consistent signal across multiple games; <0.6 = flagged for human review; <0.3 = discarded
- `source_game_ids` and `pattern_ids` for full traceability
- Conflict detection: contradictory patterns targeting the same location emit a `conflicts` entry instead of a diff

**Changes are never auto-applied.** The refiner proposes. A human reviews `proposed_diffs/` and merges.

---

## Evaluation dimensions

| Dimension | Definition |
|---|---|
| `playability` | Can a player make meaningful progress? Are controls responsive? |
| `objective_clarity` | Is it immediately obvious what the player must do? |
| `feedback_loops` | Does the game signal the result of actions (score, sound, animation)? |
| `difficulty_curve` | Is challenge appropriate, progressive, and fair from the start? |
| `mobile_suitability` | Would touch controls and a small screen work? Is the UI legible? |
| `visual_coherence` | Do art style, colors, layout, and assets feel consistent? |
| `completion_state` | Is there a clear win/lose/end condition the player can reach? |
| `overall` | Holistic quality score weighing all dimensions |

Score bands: 0–20 broken/absent · 21–40 severely flawed · 41–60 functional with issues · 61–80 works well · 81–100 excellent. Scores must be opinionated — not clustered around 50.
