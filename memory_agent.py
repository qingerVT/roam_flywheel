"""
memory_agent.py — ingest evaluation reports into a persistent pattern knowledge base.

Storage: SQLite + Gemini embeddings (text-embedding-004)
  - SQLite for structured metadata (dimension, score_impact, game_type, mechanic_type)
  - Gemini text-embedding-004 for semantic similarity search
  - Cosine similarity computed in-memory at query time (fast for < 100k patterns)

Importable API
--------------
    from memory_agent import memory_agent, query_memory

    summary  = memory_agent("eval_report.json")
    patterns = query_memory("how to improve feedback loops", dimension="feedback_loops", top_k=5)

CLI
---
    python3 memory_agent.py eval_report.json
    python3 memory_agent.py eval_report.json --query "visible score counter" --top-k 5
"""

import json
import os
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_PATH = Path(__file__).parent / "memory.db"
EMBEDDING_MODEL = "models/gemini-embedding-001"
DIMENSIONS = [
    "playability", "objective_clarity", "feedback_loops", "difficulty_curve",
    "mobile_suitability", "visual_coherence", "completion_state", "overall",
]

EXTRACTION_PROMPT = """\
You are a game design analyst reviewing a playtesting evaluation report.

Extract specific, actionable patterns and anti-patterns.

Rules for good patterns:
- BAD:  "player has fun"
- GOOD: "games with a visible score counter score 20+ points higher on feedback_loops"
- BAD:  "movement was broken"
- GOOD: "third-person platformers where keyboard inputs are not captured after page load score below 15 on playability"
- Every pattern must cite specific evidence from THIS report.
- score_impact: estimated point change on the stated dimension (positive = helps, negative = hurts)
- game_type: e.g. platformer | puzzle | shooter | runner | idle
- mechanic_type: e.g. scoring | movement | camera | ui | collision | spawning | progression | controls
- dimension: one of [playability, objective_clarity, feedback_loops, difficulty_curve,
                     mobile_suitability, visual_coherence, completion_state, overall]

Return ONLY valid JSON (no markdown):
{
  "patterns": [
    {
      "text": "<specific actionable insight that generalises beyond this game>",
      "game_type": "<type>",
      "mechanic_type": "<type>",
      "dimension": "<dimension>",
      "score_impact": <int>,
      "evidence": "<direct quote or observation from the report>"
    }
  ],
  "anti_patterns": [
    {
      "text": "<specific failure mode that a codegen agent should avoid>",
      "game_type": "<type>",
      "mechanic_type": "<type>",
      "dimension": "<dimension>",
      "score_impact": <int>,
      "evidence": "<direct quote or observation from the report>"
    }
  ]
}"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_db(path: "str | Path | None" = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patterns (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            type          TEXT NOT NULL,
            text          TEXT NOT NULL,
            game_type     TEXT,
            mechanic_type TEXT,
            dimension     TEXT,
            score_impact  INTEGER,
            evidence      TEXT,
            source_game_id TEXT,
            created_at    TEXT NOT NULL,
            embedding     BLOB NOT NULL
        )
    """)
    conn.commit()
    return conn


def _pack_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_embedding(blob: bytes) -> np.ndarray:
    n = len(blob) // 4
    return np.array(struct.unpack(f"{n}f", blob), dtype=np.float32)


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------

def _init_gemini():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-2.5-flash")


def _configure_genai():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")
    genai.configure(api_key=api_key)


def _embed(text: str) -> list[float]:
    _configure_genai()
    result = genai.embed_content(model=EMBEDDING_MODEL, content=text)
    return result["embedding"]


def _extract_patterns(model, report: dict) -> dict:
    """Ask Gemini to extract patterns and anti-patterns from the eval report."""
    report_text = json.dumps(report, indent=2)
    prompt = f"{EXTRACTION_PROMPT}\n\n=== EVALUATION REPORT ===\n{report_text}"
    response = model.generate_content(prompt)
    raw = response.text.strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned invalid JSON: {exc}\nRaw: {raw[:500]}")

    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def memory_agent(report_path: "str | Path") -> dict:
    """
    Ingest an evaluation report into the knowledge base.

    Parameters
    ----------
    report_path : Path to evaluator_agent output JSON.

    Returns
    -------
    dict with keys: patterns_added, anti_patterns_added, source_game_id
    """
    report_path = Path(report_path)
    if not report_path.exists():
        raise FileNotFoundError(f"Eval report not found: {report_path}")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    source_game_id = report.get("game_id", report_path.stem)

    model = _init_gemini()
    extracted = _extract_patterns(model, report)

    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    counts = {"patterns_added": 0, "anti_patterns_added": 0, "skipped_duplicates": 0}

    # Load existing embeddings once for duplicate detection
    existing = conn.execute("SELECT embedding FROM patterns").fetchall()
    existing_vecs = [_unpack_embedding(row[0]) for row in existing]

    DEDUP_THRESHOLD = 0.92  # cosine similarity above this → treat as duplicate

    for kind, key in [("pattern", "patterns"), ("anti_pattern", "anti_patterns")]:
        for item in extracted.get(key, []):
            text = item.get("text", "").strip()
            if not text:
                continue

            label = "pattern" if kind == "pattern" else "anti-pattern"
            print(f"\n  [{label}] {text}")
            print(f"           dim={item.get('dimension')}  impact={item.get('score_impact', 0):+d}  mechanic={item.get('mechanic_type')}")

            embedding = _embed(text)
            vec = np.array(embedding, dtype=np.float32)

            # Duplicate check against all existing embeddings
            duplicate = False
            for ev in existing_vecs:
                if _cosine_sim(vec, ev) >= DEDUP_THRESHOLD:
                    duplicate = True
                    break

            if duplicate:
                print(f"           → skipped (too similar to existing pattern)")
                counts["skipped_duplicates"] += 1
                continue

            conn.execute(
                """INSERT INTO patterns
                   (type, text, game_type, mechanic_type, dimension, score_impact,
                    evidence, source_game_id, created_at, embedding)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    kind,
                    text,
                    item.get("game_type"),
                    item.get("mechanic_type"),
                    item.get("dimension"),
                    item.get("score_impact"),
                    item.get("evidence"),
                    source_game_id,
                    now,
                    _pack_embedding(embedding),
                ),
            )
            existing_vecs.append(vec)  # guard against duplicates within this batch
            count_key = "patterns_added" if kind == "pattern" else "anti_patterns_added"
            counts[count_key] += 1
            print(f"           → stored")

    conn.commit()
    conn.close()

    return {**counts, "source_game_id": source_game_id, "extracted": extracted}



# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Memory agent for game design patterns.")
    parser.add_argument("report", help="Path to eval report JSON")
    args = parser.parse_args()

    summary = memory_agent(args.report)
    print(f"\nIngested from: {summary['source_game_id']}")
    print(f"  Patterns added     : {summary['patterns_added']}")
    print(f"  Anti-patterns added: {summary['anti_patterns_added']}")
    print(f"  Skipped (duplicate): {summary['skipped_duplicates']}")
    print(f"  KB stored at       : {DB_PATH}")


if __name__ == "__main__":
    main()
