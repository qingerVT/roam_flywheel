# run

CLI entry point that orchestrates the full four-stage flywheel pipeline: playtesting → evaluation → memory ingestion → knowledge base query → template refinement.

This module is intended to be run as a script. It exposes no importable public API beyond the `main` function, but the private `_format_result` serialiser is called internally between stages.

---

## CLI Usage

```
python3 run.py \
    --url <game_url> \
    --spec "<natural language goal>" \
    [--output-dir ./run_output] \
    [--rules /path/to/rules.md] \
    [--interval 5] \
    [--steps 4] \
    [--template-dir /path/to/template/dir]
```

**Arguments**

| Argument | Required | Default | Description |
|---|---|---|---|
| `--url` | Yes | — | URL of the Three.js game to test |
| `--spec` | Yes | — | Natural-language goal passed to the playtester and evaluator |
| `--output-dir` | No | `.` (current dir) | Directory for all output artifacts |
| `--rules` | No | `""` | Path to a game rules file passed to the playtester |
| `--interval` | No | `5` | Seconds between scheduled screenshots |
| `--steps` | No | `4` | Number of intervals; enforced minimum of `4` |
| `--template-dir` | No | `None` | Path to codegen template directory; Stage 4 is skipped if omitted |

---

## Pipeline Stages

### Stage 1 — Playtester + Evaluator

1. Calls `playtester_agent` to run the game session and collect `SessionResult`.
2. Serialises the result via `_format_result` into a timestamp-keyed dict.
3. Calls `evaluator_agent` on the serialised result.
4. Saves the evaluation report to `<output-dir>/evaluation.json`.
5. Prints per-dimension scores, highlights, and failure modes to stdout.

### Stage 2 — Memory Agent

1. Calls `memory_agent` on `<output-dir>/evaluation.json`.
2. Saves the raw extracted patterns to `<output-dir>/patterns_extracted.json`.
3. Prints counts of patterns added, anti-patterns added, and duplicates skipped.

### Stage 3 — Query Interface

1. Calls `query_memory(spec, top_k=5)` against the now-updated knowledge base.
2. Prints the top-5 patterns with similarity scores and relevance reasons.

### Stage 4 — Template Refiner (optional)

Skipped unless `--template-dir` is provided.

1. Calls `template_refiner_agent` with `template_dir` and `output_dir`.
2. Writes per-file diff JSONs to `<output-dir>/proposed_diffs/`.
3. Appends a human-readable summary to `<output-dir>/REFINEMENT_LOG.md`.
4. Prints proposed changes and any conflicts requiring human review.

---

## Output Artifacts

| Path | Description |
|---|---|
| `<output-dir>/snapshot.png` | Final screenshot from the playtester |
| `<output-dir>/evaluation.json` | Full evaluation report from `evaluator_agent` |
| `<output-dir>/patterns_extracted.json` | Raw patterns/anti-patterns extracted by `memory_agent` |
| `<output-dir>/proposed_diffs/<file>.json` | Per-template proposed diffs (Stage 4 only) |
| `<output-dir>/REFINEMENT_LOG.md` | Appended refinement run log (Stage 4 only) |

---

## Functions

### `main`

```python
def main() -> None
```

Parse CLI arguments and execute all pipeline stages in sequence. All output is written to `--output-dir`. Prints structured progress and results to stdout. No return value.
