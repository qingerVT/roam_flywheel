"""
evaluator_agent.py — evaluate playtester output against the evaluation schema.

Importable API
--------------
    from evaluator_agent import evaluator_agent

    report = evaluator_agent(
        result_path="snapshot_result.json",
        prompt="collect coins on floating islands",
        game_id="my_game_001",  # optional
    )
    # report is a dict conforming to the evaluation schema

CLI
---
    python3 evaluator_agent.py result.json "game prompt" [--game-id ID] [--output report.json]
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DIMENSIONS = [
    "playability",
    "objective_clarity",
    "feedback_loops",
    "difficulty_curve",
    "mobile_suitability",
    "visual_coherence",
    "completion_state",
    "overall",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_gemini() -> genai.GenerativeModel:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-2.5-flash")


def _load_screenshots(steps: dict) -> list:
    """Return list of (timestamp_str, PIL.Image) for available screenshots."""
    images = []
    for ts, step in steps.items():
        path = step.get("screenshot_path", "")
        if path and Path(path).exists():
            images.append((ts, Image.open(path)))
    return images


def _build_session_text(result: dict) -> str:
    steps   = result.get("steps", {})
    summary = result.get("summary", {})
    overall = summary.get("overall", {})

    lines = [
        f"Overall status : {overall.get('overall_status', 'unknown')}",
        f"Health         : {overall.get('health_assessment', '')}",
        f"Narrative      : {overall.get('narrative', '')}",
        "",
        "Per-timestep data:",
    ]
    for ts, step in steps.items():
        actions = ", ".join(step.get("actions_executed", [])) or "none"
        lines += [
            f"\n  [t={ts}s]",
            f"  game_state : {step['game_state']}",
            f"  observation: {step['observation']}",
            f"  reasoning  : {step['reasoning']}",
            f"  actions    : {actions}",
        ]
        iv = summary.get(ts, {})
        if iv.get("what_happened"):
            lines.append(f"  summary    : {iv['what_happened']}")
        for ec in iv.get("edge_cases", []):
            lines.append(f"  edge_case  : {ec}")

    if overall.get("recommendations"):
        lines += ["", "Playtester recommendations:"]
        for r in overall["recommendations"]:
            lines.append(f"  - {r}")

    return "\n".join(lines)


def _build_prompt(game_prompt: str, session_text: str, images_available: bool) -> str:
    images_note = (
        "Screenshots from each timestep are attached."
        if images_available else
        "No screenshots available — evaluate from text observations only."
    )

    return f"""You are an expert game evaluator reviewing an automated playtesting session.

Game prompt: "{game_prompt}"
{images_note}

=== PLAYTESTER SESSION LOG ===
{session_text}

=== EVALUATION INSTRUCTIONS ===
Evaluate the game across all 8 dimensions. Be opinionated and evidence-based:

Score bands:
  0 - 20   : completely broken or absent
  21 - 40  : present but severely flawed
  41 - 60  : functional with significant issues
  61 - 80  : works well with minor issues
  81 - 100 : excellent

Do NOT default to 50. Every score must cite specific evidence from the session log or screenshots.

Dimension definitions:
  playability        — Can a player make meaningful progress? Are controls responsive?
  objective_clarity  — Is it immediately obvious what the player must do?
  feedback_loops     — Does the game signal the result of actions (score, sound, animation, visual change)?
  difficulty_curve   — Is challenge appropriate, progressive, and fair from the start?
  mobile_suitability — Would touch controls and a small screen work? Is the UI legible?
  visual_coherence   — Do art style, colors, layout, and assets feel consistent and intentional?
  completion_state   — Is there a clear win/lose/end condition that the player can reach?
  overall            — Holistic quality score weighing all dimensions.

Return ONLY valid JSON matching this schema exactly (no markdown, no extra keys):
{{
  "dimensions": {{
    "playability":        {{"score": <int 0-100>, "reasoning": "<cite specific evidence>"}},
    "objective_clarity":  {{"score": <int 0-100>, "reasoning": "<cite specific evidence>"}},
    "feedback_loops":     {{"score": <int 0-100>, "reasoning": "<cite specific evidence>"}},
    "difficulty_curve":   {{"score": <int 0-100>, "reasoning": "<cite specific evidence>"}},
    "mobile_suitability": {{"score": <int 0-100>, "reasoning": "<cite specific evidence>"}},
    "visual_coherence":   {{"score": <int 0-100>, "reasoning": "<cite specific evidence>"}},
    "completion_state":   {{"score": <int 0-100>, "reasoning": "<cite specific evidence>"}},
    "overall":            {{"score": <int 0-100>, "reasoning": "<cite specific evidence>"}}
  }},
  "highlights": ["<specific things that worked well>"],
  "failure_modes": ["<specific things that are broken or missing>"]
}}"""


async def _evaluate(
    model: genai.GenerativeModel,
    game_prompt: str,
    result: dict,
    game_id: str,
) -> dict:
    steps            = result.get("steps", {})
    screenshots      = _load_screenshots(steps)
    session_text     = _build_session_text(result)
    text_prompt      = _build_prompt(game_prompt, session_text, bool(screenshots))

    # Build content list: text prompt + images interleaved with labels
    content = [text_prompt]
    for ts, img in screenshots:
        content.append(f"\n[Screenshot at t={ts}s]")
        content.append(img)

    response = await asyncio.to_thread(model.generate_content, content)
    raw = response.text.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned invalid JSON: {exc}\nRaw: {raw[:500]}")

    # Validate all dimensions present
    dims = data.get("dimensions", {})
    for dim in DIMENSIONS:
        if dim not in dims:
            dims[dim] = {"score": 0, "reasoning": "Missing from evaluation response."}

    # Build observation_log from steps
    observation_log = []
    for ts, step in steps.items():
        observation_log.append({
            "timestamp":   ts,
            "game_state":  step.get("game_state", ""),
            "observation": step.get("observation", ""),
            "actions":     step.get("actions_executed", []),
        })

    return {
        "game_id":      game_id,
        "prompt":       game_prompt,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "dimensions":   dims,
        "highlights":   data.get("highlights", []),
        "failure_modes": data.get("failure_modes", []),
        "observation_log": observation_log,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluator_agent(
    result: "dict | str | Path",
    prompt: str,
    game_id: Optional[str] = None,
) -> dict:
    """
    Evaluate a playtester session result against the evaluation schema.

    Parameters
    ----------
    result  : Playtester result dict, or path to a result JSON file.
    prompt  : The original game prompt / description.
    game_id : Optional identifier; defaults to "session" when result is a dict.

    Returns
    -------
    dict conforming to the evaluation schema.
    """
    if isinstance(result, dict):
        data    = result
        game_id = game_id or "session"
    else:
        result_path = Path(result)
        if not result_path.exists():
            raise FileNotFoundError(f"Result file not found: {result_path}")
        game_id = game_id or result_path.stem
        data    = json.loads(result_path.read_text(encoding="utf-8"))

    model = _init_gemini()
    return asyncio.run(_evaluate(model, prompt, data, game_id))

