"""
playtester_agent.py — time-based Gemini game playtester.

Screenshots are taken at t=0s, t=5s, t=15s, t=30s and on significant
visual change. Between screenshots the VLM's planned actions are executed
in sequence and recorded against the preceding GameStep. At the end a
second VLM pass synthesises the entire journey into a SessionSummary.

Importable API
--------------
    from playtester_agent import playtester_agent, SessionResult, GameStep

    result = playtester_agent(
        url="http://...",
        prompt="collect coins",
        rules_path="rules.md",   # optional
        output_path="out.png",   # optional
    )
    for step in result.steps:
        print(step.timestamp, step.game_state, step.actions_executed)
    print(result.summary.narrative)
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageChops, ImageStat
from playwright.async_api import async_playwright
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Timing / thresholds
# ---------------------------------------------------------------------------

DEFAULT_INTERVAL = 5   # seconds between screenshots
DEFAULT_STEPS    = 6   # number of intervals  →  schedule covers 0..steps*interval
CHANGE_CHECK_INTERVAL = 0.5   # seconds between pixel-diff checks
CHANGE_THRESHOLD      = 0.04  # mean normalised diff → triggers extra analysis
STUCK_THRESHOLD       = 0.005 # diff below this between two analyses → stuck
ACTION_INTERVAL       = 0.15  # seconds between consecutive actions


# ---------------------------------------------------------------------------
# Public data type
# ---------------------------------------------------------------------------

@dataclass
class GameStep:
    timestamp: float            # seconds since session start (screenshot taken)
    trigger: str                # "schedule" | "visual_change"
    screenshot_path: str        # PNG captured at start of this window
    observation: str            # VLM description of what it sees
    game_state: str             # playing | not_started | stuck | ended | no_feedback
    reasoning: str              # VLM reasoning for the action plan
    actions_planned: List[dict] # full action list returned by VLM
    actions_executed: List[str] = field(default_factory=list)  # recorded as they run


@dataclass
class IntervalSummary:
    interval: str               # e.g. "0.0s - 5.4s"
    game_state: str             # dominant state during this interval
    what_happened: str          # narrative of this interval
    edge_cases: List[str]       # edge cases observed in this interval
    agent_adaptations: List[str]  # how agent responded in this interval
    key_events: List[str]       # notable moments in this interval


@dataclass
class SessionSummary:
    # Per-interval entries keyed by timestamp string e.g. "0.0"
    intervals: dict             # {timestamp_str: IntervalSummary}
    # Overall session fields
    overall_status: str         # completed | stuck | never_started | ended_early | no_feedback
    health_assessment: str      # one-line verdict: is the game functional?
    narrative: str              # 2-3 sentence summary of the entire journey
    recommendations: List[str]  # what to investigate in future analysis


@dataclass
class SessionResult:
    steps: List[GameStep]
    summary: SessionSummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_logger() -> logging.Logger:
    name = f"playtester_{datetime.now().strftime('%H%M%S%f')}"
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    h = logging.StreamHandler()
    h.setFormatter(fmt)
    logger.addHandler(h)
    return logger


def _init_gemini() -> genai.GenerativeModel:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-2.5-flash-lite")


async def _screenshot(page, path: str) -> str:
    await page.locator("canvas").first.screenshot(path=path)
    return path


def _pixel_diff(path_a: str, path_b: str) -> float:
    """Mean normalised pixel difference: 0.0 = identical, 1.0 = fully different."""
    try:
        a = Image.open(path_a).convert("RGB")
        b = Image.open(path_b).convert("RGB")
        stat = ImageStat.Stat(ImageChops.difference(a, b))
        return sum(stat.mean) / (3 * 255)
    except Exception:
        return 0.0


async def _ask_gemini(
    model: genai.GenerativeModel,
    goal: str,
    screenshot_path: str,
    action_history: List[str],
    rules: str,
    time_to_next: float,
    consecutive_stuck: int,
) -> dict:
    """
    Ask Gemini to analyse the screenshot and return a structured JSON plan.
    Returns dict with keys: observation, game_state, reasoning, actions.
    """
    history_text = ""
    if action_history:
        history_text = "\nRecent actions executed:\n" + "\n".join(
            f"  - {a}" for a in action_history[-20:]
        )

    rules_text = f"\nGame rules:\n{rules}\n" if rules else ""
    stuck_note = (
        f"\nNOTE: The screen has looked the same for {consecutive_stuck} consecutive "
        "analyses — the game may be stuck. Try different inputs.\n"
        if consecutive_stuck >= 2 else ""
    )

    prompt = f"""You are an automated game playtester controlling a Three.js browser game.
Goal: {goal}
{rules_text}{stuck_note}{history_text}

You have approximately {time_to_next:.0f} seconds until the next screenshot analysis.

Analyse the screenshot and return ONLY a JSON object with this exact structure:
{{
  "observation": "What you see: UI elements, player position, score, any text, visual feedback",
  "game_state": "playing | not_started | stuck | ended | no_feedback",
  "reasoning": "Why you are choosing these actions",
  "actions": [
    // Provide enough actions to fill ~{time_to_next:.0f} seconds of gameplay.
    // Available actions:
    {{"action": "key",        "key": "ArrowUp"}},
    {{"action": "key",        "key": "ArrowDown"}},
    {{"action": "key",        "key": "ArrowLeft"}},
    {{"action": "key",        "key": "ArrowRight"}},
    {{"action": "key",        "key": "w"}},
    {{"action": "key",        "key": "a"}},
    {{"action": "key",        "key": "s"}},
    {{"action": "key",        "key": "d"}},
    {{"action": "key",        "key": "Space"}},
    {{"action": "key",        "key": "Return"}},
    {{"action": "click",      "x": 640, "y": 360}},
    {{"action": "mouse_move", "x": 640, "y": 360}},
    {{"action": "wait",       "ms": 300}}
    // Canvas is 1280×720. Repeat or vary keys to simulate sustained movement.
  ]
}}

game_state guide:
  playing      — game is running and responding to input
  not_started  — at a menu / loading screen / waiting for input to begin
  stuck        — game is running but frozen, looping, or unresponsive
  ended        — game over / victory screen / session complete
  no_feedback  — screen is blank, black, or shows no recognisable game content

Return ONLY the JSON. No markdown, no extra text."""

    image = Image.open(screenshot_path)
    response = await asyncio.to_thread(model.generate_content, [prompt, image])
    raw = response.text.strip()

    # Strip markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()

    # Extract outermost JSON object
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: return a safe default so the loop continues
        return {
            "observation": raw[:200],
            "game_state": "playing",
            "reasoning": "JSON parse failed — using fallback",
            "actions": [{"action": "key", "key": "ArrowUp"}],
        }


async def _execute_action(page, action: dict) -> str:
    kind = action.get("action")
    if kind == "key":
        key = action["key"]
        await page.keyboard.press(key)
        return f"key:{key}"
    elif kind == "click":
        x, y = action["x"], action["y"]
        box = await page.locator("canvas").first.bounding_box()
        if box:
            await page.mouse.click(box["x"] + x, box["y"] + y)
        return f"click:({x},{y})"
    elif kind == "mouse_move":
        x, y = action["x"], action["y"]
        box = await page.locator("canvas").first.bounding_box()
        if box:
            await page.mouse.move(box["x"] + x, box["y"] + y)
        return f"mouse_move:({x},{y})"
    elif kind == "wait":
        ms = action.get("ms", 300)
        await asyncio.sleep(ms / 1000)
        return f"wait:{ms}ms"
    return f"unknown:{kind}"


async def _generate_summary(
    model: genai.GenerativeModel,
    goal: str,
    steps: List[GameStep],
    total_duration: float,
) -> SessionSummary:
    """Text-only VLM call: produces per-interval summaries + overall session summary."""

    # Build interval boundaries: step[i].timestamp → step[i+1].timestamp (or end)
    intervals_text = ""
    for i, s in enumerate(steps):
        end_t = steps[i + 1].timestamp if i + 1 < len(steps) else total_duration
        actions_str = ", ".join(s.actions_executed) if s.actions_executed else "none"
        intervals_text += (
            f"\nInterval {s.timestamp:.1f}s - {end_t:.1f}s  [trigger: {s.trigger}]\n"
            f"  game_state : {s.game_state}\n"
            f"  observation: {s.observation}\n"
            f"  reasoning  : {s.reasoning}\n"
            f"  actions    : {actions_str}\n"
        )

    # Build the per-interval keys for the JSON template
    interval_keys = {
        f"{s.timestamp:.1f}": (
            f"{s.timestamp:.1f}s - "
            f"{(steps[i+1].timestamp if i+1 < len(steps) else total_duration):.1f}s"
        )
        for i, s in enumerate(steps)
    }
    interval_template = json.dumps(
        {k: {
            "interval": v,
            "game_state": "playing | not_started | stuck | ended | no_feedback",
            "what_happened": "narrative of this interval",
            "edge_cases": ["list edge cases in this interval"],
            "agent_adaptations": ["what agent tried in this interval"],
            "key_events": ["notable moments in this interval"],
        } for k, v in interval_keys.items()},
        indent=2
    )

    prompt = f"""You are summarising an automated game playtesting session.

Goal: {goal}
Session duration: {total_duration:.1f}s
Total actions executed: {sum(len(s.actions_executed) for s in steps)}

Interval-by-interval log:
{intervals_text}

Return ONLY a JSON object with this exact structure:
{{
  "intervals": {interval_template},
  "overall_status": "completed | stuck | never_started | ended_early | no_feedback",
  "health_assessment": "One sentence — is the game functional and playable?",
  "narrative": "2-3 sentence narrative of the entire session.",
  "recommendations": ["what to investigate in future playtesting runs"]
}}
No markdown, no extra text."""

    response = await asyncio.to_thread(model.generate_content, prompt)
    raw = response.text.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    s = raw.find("{")
    e = raw.rfind("}") + 1
    if s != -1 and e > s:
        raw = raw[s:e]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}

    intervals = {}
    for ts_str, iv in data.get("intervals", {}).items():
        intervals[ts_str] = IntervalSummary(
            interval          = iv.get("interval", ""),
            game_state        = iv.get("game_state", ""),
            what_happened     = iv.get("what_happened", ""),
            edge_cases        = iv.get("edge_cases", []),
            agent_adaptations = iv.get("agent_adaptations", []),
            key_events        = iv.get("key_events", []),
        )

    return SessionSummary(
        intervals         = intervals,
        overall_status    = data.get("overall_status", "unknown"),
        health_assessment = data.get("health_assessment", ""),
        narrative         = data.get("narrative", ""),
        recommendations   = data.get("recommendations", []),
    )


# ---------------------------------------------------------------------------
# Core async loop
# ---------------------------------------------------------------------------

async def _play(
    url: str,
    goal: str,
    rules: str,
    output_path: str,
    log: logging.Logger,
    schedule: List[int],
) -> SessionResult:
    model = _init_gemini()
    steps: List[GameStep] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await (
            await browser.new_context(viewport={"width": 1280, "height": 720})
        ).new_page()

        log.info(f"Navigating to: {url}")
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_selector("canvas", timeout=15000)
        log.info("Canvas detected.")

        await page.locator("canvas").first.click()
        await asyncio.sleep(0.5)

        loop = asyncio.get_event_loop()
        start = loop.time()
        def elapsed() -> float:
            return loop.time() - start

        sched_idx        = 0
        last_analysis_path: Optional[str] = None
        last_change_t    = 0.0
        pending_actions: List[dict] = []
        action_history:  List[str]  = []
        consecutive_stuck = 0
        total_seconds    = schedule[-1]

        while elapsed() <= total_seconds + 0.5:
            t = elapsed()

            # ── Determine whether to analyse now ──────────────────────────
            should_analyze = False
            trigger        = "schedule"
            reuse_path: Optional[str] = None

            # Priority 1 — scheduled timestamp
            if sched_idx < len(schedule) and t >= schedule[sched_idx]:
                should_analyze = True
                trigger = "schedule"
                # Advance past any timestamps we've already passed
                while sched_idx < len(schedule) and elapsed() >= schedule[sched_idx]:
                    sched_idx += 1

            # Priority 2 — significant visual change
            elif last_analysis_path and (t - last_change_t) >= CHANGE_CHECK_INTERVAL:
                chk = f"/tmp/chk_{int(t * 100):07d}.png"
                await _screenshot(page, chk)
                last_change_t = elapsed()
                diff = _pixel_diff(last_analysis_path, chk)
                if diff > CHANGE_THRESHOLD:
                    should_analyze = True
                    trigger = "visual_change"
                    reuse_path = chk
                    log.info(f"[t={t:.1f}s] Visual change detected (diff={diff:.3f})")

            # ── Analysis ──────────────────────────────────────────────────
            if should_analyze:
                frame_dir = Path(output_path).parent
                ss_path = reuse_path or str(frame_dir / f"frame_{len(steps):03d}_{int(t)}s_{trigger}.png")
                if reuse_path is None:
                    await _screenshot(page, ss_path)

                # Stuck detection: compare current screenshot to last analysis
                if last_analysis_path:
                    inter_diff = _pixel_diff(last_analysis_path, ss_path)
                    if inter_diff < STUCK_THRESHOLD:
                        consecutive_stuck += 1
                        log.info(f"[t={t:.1f}s] Screen unchanged (diff={inter_diff:.4f}), consecutive_stuck={consecutive_stuck}")
                    else:
                        consecutive_stuck = 0

                # Time budget until next scheduled screenshot
                next_sched_t = (
                    schedule[sched_idx]
                    if sched_idx < len(schedule)
                    else total_seconds
                )
                time_to_next = max(2.0, next_sched_t - elapsed())

                log.info(f"[t={t:.1f}s | {trigger}] Asking Gemini (next in ~{time_to_next:.0f}s)...")
                try:
                    result = await _ask_gemini(
                        model, goal, ss_path, action_history, rules,
                        time_to_next, consecutive_stuck,
                    )
                except Exception as e:
                    log.error(f"[t={t:.1f}s] Gemini error: {e}")
                    last_change_t = elapsed()
                    await asyncio.sleep(0.5)
                    continue

                step = GameStep(
                    timestamp       = t,
                    trigger         = trigger,
                    screenshot_path = ss_path,
                    observation     = result.get("observation", ""),
                    game_state      = result.get("game_state", "unknown"),
                    reasoning       = result.get("reasoning", ""),
                    actions_planned = result.get("actions", []),
                )
                steps.append(step)

                log.info(f"[t={t:.1f}s] state      : {step.game_state}")
                log.info(f"[t={t:.1f}s] observation: {step.observation}")
                log.info(f"[t={t:.1f}s] reasoning  : {step.reasoning}")
                log.info(f"[t={t:.1f}s] planned    : {len(step.actions_planned)} actions")

                last_analysis_path = ss_path
                last_change_t      = elapsed()
                pending_actions    = list(result.get("actions", []))

                if step.game_state == "ended":
                    log.info(f"[t={t:.1f}s] Game ended — stopping early.")
                    break

            # ── Execute next queued action ─────────────────────────────────
            elif pending_actions:
                action = pending_actions.pop(0)
                try:
                    result_str = await _execute_action(page, action)
                except Exception as e:
                    result_str = f"error:{e}"
                action_history.append(result_str)
                if steps:
                    steps[-1].actions_executed.append(result_str)
                log.info(f"[t={elapsed():.1f}s] → {result_str}")
                await asyncio.sleep(ACTION_INTERVAL)

            else:
                await asyncio.sleep(0.05)

        # ── Final snapshot ─────────────────────────────────────────────────
        await _screenshot(page, output_path)
        total_duration = elapsed()
        log.info(f"Final snapshot → {output_path}")
        log.info(f"Session complete: {len(steps)} analysis steps, "
                 f"{sum(len(s.actions_executed) for s in steps)} actions executed.")

        # ── Session summary ────────────────────────────────────────────────
        log.info("Generating session summary...")
        try:
            summary = await _generate_summary(model, goal, steps, total_duration)
        except Exception as e:
            log.error(f"Summary generation failed: {e}")
            summary = SessionSummary(
                intervals={}, overall_status="unknown",
                health_assessment="", narrative="Summary unavailable.", recommendations=[],
            )

        log.info(f"[SUMMARY] overall_status  : {summary.overall_status}")
        log.info(f"[SUMMARY] health          : {summary.health_assessment}")
        log.info(f"[SUMMARY] narrative       : {summary.narrative}")
        for ts, iv in summary.intervals.items():
            log.info(f"[SUMMARY] t={ts}s  {iv.interval}  state={iv.game_state}  {iv.what_happened[:80]}")
        for rec in summary.recommendations:
            log.info(f"[SUMMARY] recommendation : {rec}")

        await browser.close()

    return SessionResult(steps=steps, summary=summary)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def playtester_agent(
    url: str,
    prompt: str,
    rules: str = "",
    rules_path: Optional[str] = None,
    output_path: str = "snapshot.png",
    interval: int = DEFAULT_INTERVAL,
    steps: int = DEFAULT_STEPS,
) -> SessionResult:
    """
    Run a timed playtesting session on a Three.js browser game.

    Screenshots are taken on a fixed schedule and on significant visual
    change. Between screenshots the VLM's planned actions are executed and
    recorded. At the end a second VLM pass synthesises the full journey
    into a SessionSummary.

    Parameters
    ----------
    url         : Game URL.
    prompt      : Natural-language goal for the agent.
    rules       : Raw rules string (takes priority over rules_path).
    rules_path  : Path to a rules file.
    output_path : Path for the final screenshot.
    interval    : Seconds between scheduled screenshots (default 5).
    steps       : Number of intervals; total duration = steps * interval.

    Returns
    -------
    SessionResult  (.steps: List[GameStep], .summary: SessionSummary)
    """
    if not rules and rules_path:
        rules = Path(rules_path).read_text(encoding="utf-8")

    schedule = [i * interval for i in range(steps + 1)]  # e.g. steps=6,interval=5 → [0,5,10,15,20,25,30]

    log = _setup_logger()
    log.info(f"Goal       : {prompt}")
    log.info(f"Schedule   : {schedule}s")
    if rules_path:
        log.info(f"Rules file : {rules_path}")

    return asyncio.run(_play(url, prompt, rules, output_path, log, schedule))
