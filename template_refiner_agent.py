"""
template_refiner_agent.py — propose targeted diffs to codegen template files
based on accumulated playtesting knowledge.

The agent reads the full knowledge base and the current template files, reasons
about which patterns are strong enough to warrant a permanent prompt change, and
proposes specific, minimal edits. It NEVER auto-applies changes.

Importable API
--------------
    from template_refiner_agent import template_refiner_agent

    report = template_refiner_agent(
        template_dir="output/88886666/",
        db_path="memory.db",          # optional, defaults to memory.db
        output_path="refinements.json" # optional
    )

CLI
---
    python3 template_refiner_agent.py output/88886666/
    python3 template_refiner_agent.py output/88886666/ --db memory.db --output refinements.json
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Template files the refiner is allowed to propose changes to, and what each governs
TEMPLATE_FILES = {
    "system_role.md":      "High-level agent identity, technology stack, scope boundaries.",
    "instructions.md":     "Game structure, module map, ctx fields, network protocol, gameplay constants.",
    "quality_defaults.md": "Visual quality, art style, performance standards, error handling, multiplayer correctness.",
    "agent_rituals.md":    "Decision-making rituals, pre/post-edit steps, debugging checklists, hygiene.",
}

ROUTING_GUIDE = """
Use this guide to route each finding to the correct template file:

- system_role.md     → Changes to the agent's identity, role boundaries, or technology stack.
                        Rarely changes. Only route here if the finding is about what the agent IS.
- instructions.md    → Changes to game structure rules, module responsibilities, ctx field ownership,
                        network message names, gameplay mechanics, or constants (speeds, radii, etc.).
- quality_defaults.md → Changes to visual standards, art style rules, performance requirements,
                        multiplayer correctness invariants, or error handling conventions.
- agent_rituals.md   → Changes to the sequence of steps the agent performs (before/after edits,
                        debugging checklists, when to check contracts, hygiene tasks).
"""

REFINER_PROMPT = """\
You are a senior game engineer reviewing playtesting findings and proposing minimal, surgical improvements \
to the codegen agent's template prompt files.

{routing_guide}

## Current Template Files

{template_contents}

## Knowledge Base — All Accumulated Patterns

{patterns_text}

## Task

Analyse the patterns and propose targeted changes to the template files. Follow these rules strictly:

**Routing:** Each proposed change targets exactly one template file based on the routing guide above.

**Surgical diffs only:** Propose additions or targeted edits — never full rewrites.
- An `addition` inserts new text after a specific anchor line that already exists in the file.
- An `edit` replaces an exact verbatim excerpt from the file with improved text. The `old_text` \
  must appear verbatim in the current file content.

**Confidence scoring:**
- 0.9–1.0 : Pattern appears across multiple games with consistent direction.
- 0.6–0.89: Appears in one game but with strong, specific evidence.
- 0.3–0.59: Plausible but speculative — flag for human review.
- Below 0.3: Do not propose; discard.

**Conflict detection:** If two patterns imply contradictory changes to the same location, do NOT \
silently pick one. Emit a conflict entry instead.

**Attribution:** Every proposed change must list the source_game_ids and pattern_ids from the KB.

**No redundancy:** If the template already contains equivalent guidance, skip the pattern.

Return ONLY valid JSON (no markdown) matching this schema exactly:
{{
  "proposed_changes": [
    {{
      "id": "change-001",
      "target_file": "<filename>",
      "type": "addition" | "edit",
      "anchor": "<exact line after which to insert — for additions only>",
      "old_text": "<verbatim text to replace — for edits only>",
      "new_text": "<text to insert or replacement text>",
      "rationale": "<why this change improves the template, citing evidence>",
      "confidence": <float 0.0–1.0>,
      "source_game_ids": ["<game_id>"],
      "pattern_ids": [<int>]
    }}
  ],
  "conflicts": [
    {{
      "pattern_ids": [<int>, <int>],
      "description": "<what the conflict is>",
      "recommendation": "<what a human reviewer should decide>"
    }}
  ],
  "changelog": "<markdown summary: what changed, in which file, and why>"
}}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_gemini():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-2.5-flash")


def _load_kb(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, type, text, game_type, mechanic_type, dimension, "
        "score_impact, evidence, source_game_id FROM patterns"
    ).fetchall()
    conn.close()
    return [
        {
            "id":             row[0],
            "type":           row[1],
            "text":           row[2],
            "game_type":      row[3],
            "mechanic_type":  row[4],
            "dimension":      row[5],
            "score_impact":   row[6],
            "evidence":       row[7],
            "source_game_id": row[8],
        }
        for row in rows
    ]


def _load_templates(template_dir: Path) -> dict[str, str]:
    contents = {}
    for filename in TEMPLATE_FILES:
        path = template_dir / filename
        if path.exists():
            contents[filename] = path.read_text(encoding="utf-8")
        else:
            contents[filename] = ""
    return contents


def _format_patterns(patterns: list[dict]) -> str:
    lines = []
    for p in patterns:
        impact = f"{p['score_impact']:+d}" if p["score_impact"] is not None else "n/a"
        lines.append(
            f"[ID={p['id']} | {p['type']} | dim={p['dimension']} | impact={impact} | "
            f"mechanic={p['mechanic_type']} | game={p['source_game_id']}]\n"
            f"  Pattern : {p['text']}\n"
            f"  Evidence: {p['evidence']}\n"
        )
    return "\n".join(lines)


def _format_templates(contents: dict[str, str]) -> str:
    parts = []
    for filename, purpose in TEMPLATE_FILES.items():
        body = contents.get(filename, "(file not found)")
        parts.append(f"### {filename}\n_Purpose: {purpose}_\n\n```\n{body}\n```")
    return "\n\n---\n\n".join(parts)


def _parse_response(raw: str) -> dict:
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip().rstrip("```").strip()
    start, end = raw.find("{"), raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini returned invalid JSON: {exc}\nRaw: {raw[:500]}")


def _save_diffs(report: dict, output_dir: "str | Path") -> None:
    """
    Save proposed changes grouped by target file under output_dir/proposed_diffs/,
    and append a human-readable entry to output_dir/REFINEMENT_LOG.md.
    """
    output_dir = Path(output_dir)
    diffs_dir  = output_dir / "proposed_diffs"
    diffs_dir.mkdir(parents=True, exist_ok=True)

    changes   = report.get("proposed_changes", [])
    conflicts = report.get("conflicts", [])
    meta      = report.get("meta", {})
    changelog = report.get("changelog", "")

    # Group changes by target file
    by_file: dict = {}
    for c in changes:
        tf = c.get("target_file", "unknown")
        by_file.setdefault(tf, []).append(c)

    # Write per-file JSON
    for filename, file_changes in by_file.items():
        safe_stem = Path(filename).stem   # e.g. "instructions" from "instructions.md"
        out = diffs_dir / f"{safe_stem}.json"
        out.write_text(json.dumps({"target_file": filename, "changes": file_changes}, indent=2), encoding="utf-8")

    # Append to REFINEMENT_LOG.md
    log_path = output_dir / "REFINEMENT_LOG.md"
    generated_at = meta.get("generated_at", datetime.now(timezone.utc).isoformat())
    lines = [
        f"\n## Refinement run — {generated_at}\n",
        f"- Patterns reviewed : {meta.get('patterns_reviewed', '?')}",
        f"- Changes proposed  : {len(changes)}",
        f"- Conflicts flagged : {len(conflicts)}",
        f"- Templates read    : {', '.join(meta.get('templates_read', []))}",
    ]
    if by_file:
        lines.append("\n### Files touched")
        for fname, fc in by_file.items():
            lines.append(f"- `{fname}` — {len(fc)} change(s)")
    if conflicts:
        lines.append("\n### Conflicts requiring human review")
        for cf in conflicts:
            lines.append(f"- Patterns {cf['pattern_ids']}: {cf['description']}")
    lines.append(f"\n### Changelog\n{changelog}\n")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def template_refiner_agent(
    template_dir: "str | Path",
    db_path: "str | Path | None" = None,
    output_dir: "str | Path | None" = None,
) -> dict:
    """
    Propose targeted diffs to codegen template files based on the knowledge base.

    Parameters
    ----------
    template_dir : Directory containing system_role.md, instructions.md, etc.
    db_path      : Path to memory.db (defaults to memory.db next to this script).
    output_dir   : If provided, write per-file diffs under output_dir/proposed_diffs/
                   and append to output_dir/REFINEMENT_LOG.md.

    Returns
    -------
    dict with keys: proposed_changes, conflicts, changelog, meta
    """
    template_dir = Path(template_dir)
    db_path      = Path(db_path) if db_path else Path(__file__).parent / "memory.db"

    patterns  = _load_kb(db_path)
    templates = _load_templates(template_dir)

    if not patterns:
        return {
            "proposed_changes": [],
            "conflicts":        [],
            "changelog":        "No patterns in knowledge base yet. Run memory_agent.py first.",
            "meta": {"patterns_reviewed": 0, "templates_read": list(templates.keys())},
        }

    model = _init_gemini()

    prompt = REFINER_PROMPT.format(
        routing_guide=ROUTING_GUIDE,
        template_contents=_format_templates(templates),
        patterns_text=_format_patterns(patterns),
    )

    response = model.generate_content(prompt)
    report   = _parse_response(response.text.strip())

    report["meta"] = {
        "generated_at":      datetime.now(timezone.utc).isoformat(),
        "patterns_reviewed": len(patterns),
        "templates_read":    [f for f in TEMPLATE_FILES if templates.get(f)],
        "db_path":           str(db_path),
        "template_dir":      str(template_dir),
    }

    if output_dir:
        _save_diffs(report, output_dir)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Propose diffs to codegen template files from KB patterns.")
    parser.add_argument("template_dir",               help="Directory containing template .md files")
    parser.add_argument("--db",         default=None, help="Path to memory.db (default: ./memory.db)")
    parser.add_argument("--output-dir", default=None, help="Directory for proposed_diffs/ and REFINEMENT_LOG.md")
    args = parser.parse_args()

    report = template_refiner_agent(args.template_dir, db_path=args.db, output_dir=args.output_dir)

    changes   = report.get("proposed_changes", [])
    conflicts = report.get("conflicts", [])

    print(f"\n{'='*70}")
    print(f"TEMPLATE REFINEMENT REPORT")
    print(f"{'='*70}")
    print(f"  Patterns reviewed : {report['meta']['patterns_reviewed']}")
    print(f"  Changes proposed  : {len(changes)}")
    print(f"  Conflicts flagged : {len(conflicts)}")

    if changes:
        print(f"\n{'─'*70}")
        print(f"PROPOSED CHANGES")
        print(f"{'─'*70}")
        for c in changes:
            conf = c.get("confidence", 0)
            flag = " ⚠ low confidence" if conf < 0.6 else ""
            print(f"\n  [{c['id']}] → {c['target_file']}  ({c['type']})  confidence={conf:.2f}{flag}")
            print(f"  Rationale: {c['rationale']}")
            print(f"  Sources  : games={c.get('source_game_ids')}  patterns={c.get('pattern_ids')}")
            if c["type"] == "addition":
                print(f"  After    : \"{c.get('anchor', '')}\"")
            elif c["type"] == "edit":
                print(f"  Replace  : \"{str(c.get('old_text', ''))[:80]}...\"")
            print(f"  New text :\n    {c.get('new_text', '').strip()}")

    if conflicts:
        print(f"\n{'─'*70}")
        print(f"CONFLICTS — HUMAN REVIEW REQUIRED")
        print(f"{'─'*70}")
        for cf in conflicts:
            print(f"\n  Patterns {cf['pattern_ids']}: {cf['description']}")
            print(f"  Recommendation: {cf['recommendation']}")

    print(f"\n{'─'*70}")
    print(f"CHANGELOG")
    print(f"{'─'*70}")
    print(report.get("changelog", ""))

    if args.output_dir:
        print(f"\n  Diffs saved → {args.output_dir}/proposed_diffs/")
        print(f"  Log appended → {args.output_dir}/REFINEMENT_LOG.md")


if __name__ == "__main__":
    main()
