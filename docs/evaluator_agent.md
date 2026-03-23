# evaluator_agent

Evaluates a playtester session result against an 8-dimension scoring schema using Gemini, optionally with screenshots attached for multimodal analysis.

## Constants

`DIMENSIONS` — ordered list of the 8 evaluation dimensions:
`playability`, `objective_clarity`, `feedback_loops`, `difficulty_curve`, `mobile_suitability`, `visual_coherence`, `completion_state`, `overall`.

---

## Functions

### `evaluator_agent`

```python
def evaluator_agent(
    result: dict | str | Path,
    prompt: str,
    game_id: Optional[str] = None,
) -> dict
```

Evaluate a playtester session result against the evaluation schema.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `result` | `dict \| str \| Path` | Either a playtester result dict (as produced by `_format_result` in `run.py`) or a path to a result JSON file on disk |
| `prompt` | `str` | The original game prompt / description used during playtesting |
| `game_id` | `str \| None` | Optional identifier for the game; defaults to `"session"` when `result` is a dict, or to the file stem when `result` is a path |

**Returns** — `dict` with the following keys:

| Key | Type | Description |
|---|---|---|
| `game_id` | `str` | Game identifier |
| `prompt` | `str` | Original game prompt |
| `evaluated_at` | `str` | UTC ISO-8601 timestamp of evaluation |
| `dimensions` | `dict` | Per-dimension `{"score": int, "reasoning": str}` for all 8 dimensions |
| `highlights` | `list[str]` | Specific things that worked well |
| `failure_modes` | `list[str]` | Specific things that are broken or missing |
| `observation_log` | `list[dict]` | Per-step log: `timestamp`, `game_state`, `observation`, `actions` |

**Score bands**

| Range | Meaning |
|---|---|
| 0–20 | Completely broken or absent |
| 21–40 | Present but severely flawed |
| 41–60 | Functional with significant issues |
| 61–80 | Works well with minor issues |
| 81–100 | Excellent |

**Notable behaviour**

- Requires `GEMINI_API_KEY` in the environment. Raises `EnvironmentError` if absent.
- When `result` is a file path and the file does not exist, raises `FileNotFoundError`.
- Screenshots referenced in the result's step data are loaded from disk and attached to the Gemini request as images when available. If no screenshot files exist, evaluation falls back to text-only.
- If a dimension is missing from the Gemini response, it is back-filled with `score: 0` and a note rather than raising.
- Raises `ValueError` if Gemini returns unparseable JSON.
- Calls `asyncio.run` internally; cannot be called from an already-running event loop.
