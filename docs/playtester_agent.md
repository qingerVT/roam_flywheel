# playtester_agent

Time-based Gemini game playtester for Three.js browser games. Screenshots are taken at scheduled intervals and on significant visual change. Between screenshots the VLM's planned actions are executed in sequence and recorded. At the end a second VLM pass synthesises the full session into a `SessionSummary`.

## Data Classes

### `GameStep`

Represents one analysis window (one screenshot + the actions that followed it).

| Field | Type | Description |
|---|---|---|
| `timestamp` | `float` | Seconds since session start when the screenshot was taken |
| `trigger` | `str` | `"schedule"` or `"visual_change"` |
| `screenshot_path` | `str` | Path to the PNG captured at the start of this window |
| `observation` | `str` | VLM description of what it sees |
| `game_state` | `str` | One of: `playing`, `not_started`, `stuck`, `ended`, `no_feedback` |
| `reasoning` | `str` | VLM reasoning for the action plan |
| `actions_planned` | `List[dict]` | Full action list returned by the VLM |
| `actions_executed` | `List[str]` | Actions recorded as they were executed |

### `IntervalSummary`

Per-interval narrative entry inside a `SessionSummary`.

| Field | Type | Description |
|---|---|---|
| `interval` | `str` | Human-readable time range, e.g. `"0.0s - 5.4s"` |
| `game_state` | `str` | Dominant game state during this interval |
| `what_happened` | `str` | Narrative of this interval |
| `edge_cases` | `List[str]` | Edge cases observed |
| `agent_adaptations` | `List[str]` | How the agent responded |
| `key_events` | `List[str]` | Notable moments |

### `SessionSummary`

Overall session summary produced by a second VLM pass after play ends.

| Field | Type | Description |
|---|---|---|
| `intervals` | `dict` | `{timestamp_str: IntervalSummary}` keyed by e.g. `"0.0"` |
| `overall_status` | `str` | One of: `completed`, `stuck`, `never_started`, `ended_early`, `no_feedback` |
| `health_assessment` | `str` | One-line verdict on whether the game is functional |
| `narrative` | `str` | 2–3 sentence summary of the entire session |
| `recommendations` | `List[str]` | Items to investigate in future playtesting runs |

### `SessionResult`

Top-level return type of `playtester_agent`.

| Field | Type | Description |
|---|---|---|
| `steps` | `List[GameStep]` | All analysis steps in chronological order |
| `summary` | `SessionSummary` | End-of-session synthesised summary |

---

## Functions

### `playtester_agent`

```python
def playtester_agent(
    url: str,
    prompt: str,
    rules: str = "",
    rules_path: Optional[str] = None,
    output_path: str = "snapshot.png",
    interval: int = 5,
    steps: int = 6,
) -> SessionResult
```

Run a timed playtesting session on a Three.js browser game.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `url` | `str` | URL of the game to test |
| `prompt` | `str` | Natural-language goal for the agent (e.g. `"collect coins"`) |
| `rules` | `str` | Raw rules string; takes priority over `rules_path` if both supplied |
| `rules_path` | `str \| None` | Path to a rules file; loaded if `rules` is empty |
| `output_path` | `str` | Destination path for the final end-of-session screenshot |
| `interval` | `int` | Seconds between scheduled screenshots (default: `5`) |
| `steps` | `int` | Number of intervals; total session duration = `steps × interval` (default: `6` → 30 s) |

**Returns** — `SessionResult` containing all `GameStep` entries and a `SessionSummary`.

**Notable behaviour**

- Requires `GEMINI_API_KEY` in the environment (reads from `.env` in the script directory). Raises `EnvironmentError` if absent.
- Launches a non-headless Chromium browser via Playwright and waits for a `<canvas>` element before starting.
- Screenshots are additionally triggered when pixel-level diff between the last analysis frame and the current frame exceeds `CHANGE_THRESHOLD` (0.04 normalised mean diff).
- If the screen is unchanged across two consecutive analyses (`STUCK_THRESHOLD` = 0.005), a stuck counter is incremented and passed to the VLM so it can try different inputs.
- Early termination occurs when the VLM reports `game_state == "ended"`.
- If JSON parsing of the VLM response fails, a safe fallback response is used so the session continues rather than crashing.
- Calls `asyncio.run` internally; cannot be called from an already-running event loop.
