# Game Intelligence Pipeline: Playtest-to-Memory Flywheel

> **Type:** Research prototype
**Role:** AI Engineer @ Roam
**Constraint:** Standalone system, no modifications to existing codegen pipeline
**In one sentence:** generated game URL → VLM playtester agent → structured evaluation → memory agent extracts patterns → queryable knowledge base → automatic template refinement → codegen gets smarter over time.
> 

```
INPUT:  a live Three.js game URL + optional game spec/prompt
OUTPUT: {
  evaluation:        structured JSON report scoring the game across 8 dimensions
  patterns:          extracted design patterns + anti-patterns stored in knowledge base
  query_result:      given a new game spec, return relevant patterns from past evals
  template_diffs:    proposed surgical edits to codegen template files based on findings
}
```

---

## Background

Roam builds mobile games using AI-powered coding agents. The core codegen system (Three.js + FastAPI + Claude) can already generate, validate, and auto-fix games. Every generation produces rich artifacts: `trace.json`, `conversation.json`, `features.json`, `plan.md`, and full module code.

**The gap:** the system knows if a game *compiles*. It has no idea if it's *good*. And every generation currently starts from zero — none of the signal from past games is reused. The codegen agent is entirely prompt-driven via a set of template `.md` files (`system_role.md`, `instructions.md`, `quality_defaults.md`, `agent_rituals.md` etc.) — but nothing ever improves those templates based on what players actually experience.

Your task is to close all of these gaps by building a **self-improving game evaluation flywheel.**

---

## Core Principle

**Don't just check if the game runs. Check if it's good. Then use that signal to make the next one better — permanently.**

```
NAIVE (WHAT EXISTS):
  generate game -> playwright catches runtime errors -> done
  next game starts from zero, no memory of what worked

FLYWHEEL (WHAT YOU'RE BUILDING):
  generate game -> agent plays it -> structured critique
               -> memory layer ingests critique -> extracts patterns
               -> query interface informs next generation at prompt-time
               -> template refiner proposes permanent improvements to codegen templates
               -> better codegen -> better game -> better critique -> richer memory
```

---

## Suggested Architecture

```
                    [1] GAME URL + SPEC
                            |
                            v
                   +------------------+
                   |  PLAYTESTER      |  Playwright loads game, takes screenshots
                   |  AGENT           |  at t=0,5,15,30s, simulates inputs,
                   |                  |  feeds screenshots to VLM iteratively
                   +--------+---------+
                            |
                            v
                   +------------------+
                   | EVALUATION       |  structured JSON, 8 scored dimensions
                   | REPORT           |  + reasoning string per dimension
                   +--------+---------+
                            |
                            v
                   +------------------+
                   |  MEMORY AGENT    |  ingests report, extracts patterns,
                   |                  |  tags by game type + mechanic + dimension
                   +--------+---------+
                            |
                            v
                   +------------------+
                   | KNOWLEDGE BASE   |  queryable store of patterns
                   |                  |  + anti-patterns across all past evals
                   +--------+---------+
                            |
                   +--------+---------+
                            |
                   +--------v---------+
                   | QUERY INTERFACE  |  spec in → relevant patterns out
                   +--------+---------+  (this plugs into future codegen prompts)
                            |
                            v
                   +------------------+
                   | TEMPLATE REFINER |  reads current codegen templates
                   | AGENT            |  + knowledge base findings
                   |                  |  → proposes diffs to .md template files
                   +--------+---------+
                            |
                            v
                   +------------------+
                   | REFINED TEMPLATES|  updated system_role.md,
                   |                  |  instructions.md, quality_defaults.md,
                   |                  |  agent_rituals.md etc.
                   +------------------+
```

---

## Pipeline Stages

### Stage 1: Playtester Agent

**Input:** live Three.js game URL
**Output:** raw observation log (screenshots + VLM reasoning at each timestep)

The agent loads the game in a headless browser via Playwright and attempts to play it autonomously:

- Takes screenshots at t=0s, t=5s, t=15s, t=30s (and on significant visual change)
- Simulates plausible inputs between screenshots — arrow keys, mouse clicks, WASD — based on what the VLM observes on screen
- At each step, the VLM reasons about game state: what is happening, what the player is supposed to do, whether the game appears stuck or broken
- The agent should detect and handle edge cases: game that never starts, game stuck in a loop, game that ends immediately, game with no visible feedback

The playtester does NOT produce scores. It produces a timestamped observation log that the evaluator consumes.

### Stage 2: Evaluator

**Input:** observation log from Stage 1 + original game spec/prompt (if available)
**Output:** evaluation report JSON conforming to the schema below

A second LLM pass over the full observation log produces a structured evaluation. This is a separate step from observation so the two concerns don't collapse into each other.

**Evaluation schema:**

```json
{
  "game_id": "string",
  "prompt": "string | null",
  "evaluated_at": "ISO timestamp",
  "dimensions": {
    "playability":        { "score": 0-100, "reasoning": "string" },
    "objective_clarity":  { "score": 0-100, "reasoning": "string" },
    "feedback_loops":     { "score": 0-100, "reasoning": "string" },
    "difficulty_curve":   { "score": 0-100, "reasoning": "string" },
    "mobile_suitability": { "score": 0-100, "reasoning": "string" },
    "visual_coherence":   { "score": 0-100, "reasoning": "string" },
    "completion_state":   { "score": 0-100, "reasoning": "string" },
    "overall":            { "score": 0-100, "reasoning": "string" }
  },
  "highlights": ["string"],
  "failure_modes": ["string"],
  "observation_log": [...]
}
```

Every field is required. Scores must be opinionated — a game that compiles but has no discernible objective should score below 30 on `objective_clarity`, not 50. Vague middle-ground scores are a failure of the evaluator.

### Stage 3: Memory Agent

**Input:** evaluation report from Stage 2
**Output:** updated knowledge base

The memory agent ingests the report and does two things:

- **Pattern extraction:** identifies specific design decisions that correlated with high or low scores, tags them with metadata (game type, mechanic type, dimension affected, score impact)
- **Anti-pattern extraction:** identifies specific failure modes worth remembering — things the codegen agent did that hurt playability, clarity, or coherence

Patterns must be stored with enough context to be retrievable and actionable. A pattern like "player has fun" is useless. A pattern like "games with a visible score counter score 20+ points higher on feedback_loops" is useful.

Storage choice is intentionally open — vector DB, SQLite + embeddings, JSON + semantic search. The choice and the reasoning behind it are part of the signal.

### Stage 4: Query Interface

**Input:** a new game spec or prompt string
**Output:** top N relevant patterns from the knowledge base

Given a description of a game about to be generated, the query interface returns the most relevant patterns from past evaluations — things that worked, things that failed, and in what context. This is the output that would be injected into a future generation prompt.

The interface must handle a cold start gracefully — zero prior evaluations should return an empty result, not crash.

### Stage 5: Template Refiner Agent

**Input:** current codegen template files + full knowledge base
**Output:** proposed diffs to one or more template files

The existing codegen system is entirely prompt-driven — `system_role.md`, `instructions.md`, `quality_defaults.md`, `agent_rituals.md` and others are what shape every game the agent generates. This stage closes the loop by translating accumulated gameplay findings directly into prompt improvements.

The refiner agent reads the full knowledge base and the current template files, reasons about which patterns are strong enough and consistent enough to warrant a permanent prompt change, and proposes specific targeted edits. It must:

- Target the right template for each finding — a finding about visual quality belongs in `quality_defaults.md`, a finding about game structure belongs in `instructions.md`, a finding about decision-making belongs in `agent_rituals.md`
- Propose minimal, surgical diffs — not rewrites. One finding = one addition or edit, clearly attributed
- Attach a confidence score and the source game IDs to every proposed change so a human can audit before applying
- Never propose contradictory edits — if two findings conflict, flag the conflict rather than silently picking one
- Produce a human-readable changelog explaining what changed and why

The agent should NOT auto-apply changes. It proposes. A human reviews and merges. The output is structured diffs + reasoning, not overwritten files.

---

## Deliverables

### D1: Playtester + Evaluator Module

A Python module that takes a game URL and returns a structured evaluation report.

|  | Spec |
| --- | --- |
| **Input** | `str` — live Three.js game URL, optional prompt string |
| **Output** | `dict` — evaluation report conforming to schema above |
| **Browser automation** | Playwright, headless |
| **Screenshots** | minimum 4 per run (t=0, t=5, t=15, t=30), more on visual change |
| **Input simulation** | must attempt at least keyboard + click inputs |
| **VLM** | Claude or GPT-4V |
| **Validation** | output must pass JSON schema validator included in module |

**Pass/fail:**

- PASS: given any live Three.js game URL, produces a schema-valid evaluation report with all 8 dimensions scored and reasoned. Scores are opinionated (not all clustered around 50). Report correctly identifies at least one failure mode if one exists.
- FAIL: crashes on valid input, produces schema-invalid JSON, all scores between 40–60 regardless of game quality, failure modes empty on a clearly broken game

**5 test games (provided by Roam):** at minimum, one game should be clearly broken, one should be clearly polished, and the evaluator's scores should reflect the difference.

---

### D2: Memory Agent Module

A Python module that ingests evaluation reports and maintains a knowledge base of patterns.

|  | Spec |
| --- | --- |
| **Input** | evaluation report dict from D1 |
| **Output** | updated knowledge base (side effect) + extracted patterns for this report |
| **Pattern schema** | `{ pattern_id, type: "positive"\|"negative", description, game_type_tags, mechanic_tags, dimension, score_impact_estimate, source_game_ids }` |
| **Storage** | contractor's choice — must be justified in README |
| **Cold start** | must handle 0 prior evaluations without error |
| **Deduplication** | semantically similar patterns from different games should merge, not duplicate |

**Pass/fail:**

- PASS: after ingesting 3+ evaluation reports, knowledge base contains distinct positive and negative patterns with metadata. No duplicate patterns for the same observation. Cold start works.
- FAIL: crashes on first ingest, all patterns generic/vague, no deduplication, cold start errors

---

### D3: Query Interface

A Python module that takes a game spec and returns relevant patterns from the knowledge base.

|  | Spec |
| --- | --- |
| **Input** | `str` — game spec or prompt describing a game about to be generated |
| **Output** | `list[pattern]` — top N most relevant patterns, ranked by relevance |
| **Cold start** | returns empty list (not error) if knowledge base is empty |
| **Ranking** | must explain why each pattern was returned (relevance reasoning) |
| **CLI** | `python query.py "a platformer where you collect coins"` prints results |

**Pass/fail:**

- PASS: given a game spec, returns patterns that are meaningfully relevant to that spec (not random). Relevance reasoning is coherent. Cold start returns empty list without error.
- FAIL: returns random or irrelevant patterns, crashes on cold start, no relevance reasoning

---

### D4: Template Refiner Module

A Python module that reads the knowledge base and current templates and proposes refinements.

|  | Spec |
| --- | --- |
| **Input** | path to templates directory + knowledge base |
| **Output** | `list[proposed_diff]` — each with `target_file`, `diff`, `reasoning`, `confidence`, `source_pattern_ids`, `source_game_ids` |
| **Targeting** | must correctly route findings to the right template file — validated in README |
| **Conflict detection** | if two patterns suggest contradictory changes to the same template, output a `conflict` entry instead of a diff |
| **Changelog** | human-readable `REFINEMENT_LOG.md` generated alongside diffs |
| **Apply script** | optional `apply.py` that writes diffs to disk after human confirmation prompt |

**Pass/fail:**

- PASS: after ingesting a knowledge base with 3+ evaluations, produces at least 2 proposed diffs targeting different template files. Each diff is surgical (not a full rewrite). Reasoning is specific and references source patterns. Conflict detection fires correctly when given two contradictory patterns (include a synthetic test case for this).
- FAIL: proposes full template rewrites, reasoning is generic, crashes on empty knowledge base, no conflict detection

**Acceptance demo:** after running D5 on at least 3 games (broken, polished, mixed), run the refiner. It should produce:

- At least 1 diff targeting `quality_defaults.md` or `agent_rituals.md`
- At least 1 diff with a confidence score below 0.7 (the system should know what it doesn't know yet)
- A `REFINEMENT_LOG.md` that a non-technical person could read and understand why each change was proposed

---

### D5: End-to-End Pipeline

A single Python entry point that chains D1 → D2 → D3 → D4.

|  | Spec |
| --- | --- |
| **Input** | game URL + optional spec string |
| **Output** | folder containing `evaluation.json`, `patterns_extracted.json`, updated knowledge base, `proposed_diffs/`, `REFINEMENT_LOG.md` |
| **CLI** | `python run.py --url <game_url> --spec "a platformer with coins"` |
| **Status** | prints pipeline stage to stdout as it runs |
| **Timing** | logs elapsed time per stage |

**Pass/fail for the 3 acceptance demos:**

| # | Input | Must produce |
| --- | --- | --- |
| 1 | A clearly broken game (provided) | Evaluation scores below 40 on at least 3 dimensions, failure modes populated, patterns include at least 2 negative entries |
| 2 | A polished game (provided) | Evaluation scores above 70 on at least 5 dimensions, highlights populated, patterns include at least 2 positive entries |
| 3 | Query + refine after both are ingested | Query `"a game where you shoot enemies and collect points"` returns ≥2 relevant patterns with reasoning. Refiner produces ≥1 diff per game evaluated, at least 2 targeting different template files. |

---

### D6: Integration Design Doc

A short doc or Loom (~10–15 min) answering:

- Where exactly in the existing codegen flow does the memory query happen — before enrichment, after enrichment, as a tool the main agent can call?
- What does the injected context look like — show a concrete before/after example of a system prompt with and without memory context
- How does the template refinement cadence work — after every game? Every N games? Above a confidence threshold?
- What's the review process for proposed template diffs — who approves, how do bad diffs get caught before they degrade generation quality?
- What breaks at scale — specific failure modes at 1K games, 100K games
- What does this flywheel look like in 6 months if it runs continuously

This is not an essay. Concrete and specific beats thorough and vague.

---

## Risk Matrix

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| VLM produces shallow/generic evaluations | High | High | force structured schema + few-shot examples of opinionated scoring in system prompt |
| Scores cluster around 50 regardless of quality | Medium | High | include explicit calibration examples in evaluator prompt (broken game = what scores, polished game = what scores) |
| Memory patterns too vague to be useful | Medium | High | pattern schema enforces `score_impact_estimate` + `mechanic_tags` — vague patterns fail validation |
| Template refiner proposes rewrites instead of diffs | Medium | High | system prompt must explicitly constrain to surgical edits; test with a synthetic "rewrite attempt" case |
| Contradictory patterns corrupt templates | Low | High | conflict detection is a hard requirement in D4, not a stretch goal |
| Cold start query returns noise | Low | Medium | empty list is valid return, query interface must handle gracefully |
| Playwright fails on complex Three.js games | Medium | Medium | fallback to static screenshot-only mode if input simulation causes crash |
| Pattern deduplication collapses distinct signals | Low | Medium | merge only when cosine similarity > threshold, keep `source_game_ids` for lineage |

---

## What We're Evaluating

| Signal | What to look for |
| --- | --- |
| **Agent design instincts** | Clean tool design, well-defined interfaces, sensible failure handling — not just prompt wrangling |
| **VLM reasoning quality** | Does she get meaningful signal out of screenshots, or is the evaluation shallow? |
| **Memory architecture taste** | Does her storage/retrieval design actually scale? Did she think about cold start and deduplication? |
| **Template refiner judgment** | Does the refiner know the difference between a strong signal and noise? Does it target the right files? |
| **Systems thinking** | Does the integration sketch show she understands the existing architecture deeply? |
| **Speed + pragmatism** | Did she ship something real and demonstrable in the time given? |
| **Vision** | Does she see where this flywheel goes at scale, and what breaks first? |

---

## Setup Notes

- Provide **3–5 sample generated games** (live URLs) — at minimum one clearly broken, one clearly polished, one mixed
- Provide the **full codegen template directory** (`backend/engine/templates/`) so the refiner has real files to work with
- Provide the **existing agent architecture doc** so the integration design is grounded in reality
- Keep infra lightweight — everything should run locally
- No single right answer — the storage choice, the pattern schema, the refiner's targeting logic — all of these are design decisions, and the reasoning behind them is as important as the outcome

---

## The Flywheel

```
GENERATE → PLAYTEST → EVALUATE → STORE → EXTRACT PATTERNS
                                              |
                        REFINE TEMPLATES ←---+
                              |
                        BETTER CODEGEN → GENERATE → ...
```

In 6 months, if this works, the codegen system should be measurably generating better games because it's learning from every game it has ever made — both at inference time via the query interface, and permanently via template refinement. Qing's trial output is the first brick in that wall.
