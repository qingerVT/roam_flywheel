# Example usage:
# python3 run.py --url "http://192.168.12.164:8083" --spec "third-person platformer, collect coins on floating islands" \
#     --rules /Users/qinsun/Documents/roam/output/88886666/rules.md \
#     --steps 3 --output-dir ./run_output --template-dir /Users/qinsun/Documents/roam/output/88886666/

import argparse
import dataclasses
import json
import time
from pathlib import Path
from playtester_agent import playtester_agent
from evaluator_agent import evaluator_agent
from memory_agent import memory_agent
from query import query_memory
from template_refiner_agent import template_refiner_agent


def _format_result(result) -> dict:
    """Serialise SessionResult into timestamp-keyed dicts for steps and summary."""
    # Steps: keyed by timestamp string
    steps_dict = {}
    for s in result.steps:
        key = f"{s.timestamp:.1f}"
        steps_dict[key] = {
            "trigger":          s.trigger,
            "screenshot_path":  s.screenshot_path,
            "game_state":       s.game_state,
            "observation":      s.observation,
            "reasoning":        s.reasoning,
            "actions_planned":  s.actions_planned,
            "actions_executed": s.actions_executed,
        }

    # Summary: per-interval entries + overall
    summary_dict = {}
    for ts_str, iv in result.summary.intervals.items():
        summary_dict[ts_str] = dataclasses.asdict(iv)
    summary_dict["overall"] = {
        "overall_status":    result.summary.overall_status,
        "health_assessment": result.summary.health_assessment,
        "narrative":         result.summary.narrative,
        "recommendations":   result.summary.recommendations,
    }

    return {"steps": steps_dict, "summary": summary_dict}



def main():
    parser = argparse.ArgumentParser(description="Gemini playtester for Three.js games.")
    parser.add_argument("--url",            required=True, help="Game URL")
    parser.add_argument("--spec",           required=True, help="Natural language goal for the agent")
    parser.add_argument("--output-dir",   default=".",   help="Directory to write all artifacts (default: current dir)")
    parser.add_argument("--rules",    default="",             help="Path to a game rules file")
    parser.add_argument("--interval", type=int, default=5,    help="Seconds between screenshots (default: 5)")
    parser.add_argument("--steps",       type=int, default=4,    help="Number of intervals, total = steps*interval (default: 4 → 20s, minimum: 4)")
    parser.add_argument("--template-dir", default=None,          help="Path to codegen template directory for Stage 4 refinement")
    args = parser.parse_args()
    args.steps = max(4, args.steps)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'─'*60}")
    print(f"  STAGE 1 — PLAYTESTER + EVALUATOR")
    print(f"{'─'*60}")
    t1 = time.time()

    result = playtester_agent(
        url=args.url,
        prompt=args.spec,
        rules_path=args.rules or None,
        output_path=str(out_dir / "snapshot.png"),
        interval=args.interval,
        steps=args.steps,
    )

    # ── Print steps ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"SESSION COMPLETE — {len(result.steps)} analysis steps")
    print(f"{'='*60}")
    for s in result.steps:
        print(f"  t={s.timestamp:5.1f}s [{s.trigger:14s}] state={s.game_state:12s} "
              f"actions={len(s.actions_executed):2d}  {s.observation[:55]}...")

    # ── Print per-interval summaries ───────────────────────────────────────
    sm = result.summary
    print(f"\n{'─'*60}")
    print(f"INTERVAL SUMMARIES")
    print(f"{'─'*60}")
    for ts, iv in sm.intervals.items():
        print(f"\n  [{iv.interval}]  state: {iv.game_state}")
        print(f"    {iv.what_happened}")
        for ec in iv.edge_cases:
            print(f"    • edge case : {ec}")
        for a in iv.agent_adaptations:
            print(f"    • adaptation: {a}")
        for e in iv.key_events:
            print(f"    • event     : {e}")

    # ── Print overall summary ──────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"OVERALL SUMMARY")
    print(f"{'─'*60}")
    print(f"  Status  : {sm.overall_status}")
    print(f"  Health  : {sm.health_assessment}")
    print(f"  Narrative: {sm.narrative}")
    if sm.recommendations:
        print(f"\n  Recommendations:")
        for r in sm.recommendations:
            print(f"    → {r}")

    eval_base = str(out_dir / "evaluation.json")
    report = evaluator_agent(result=_format_result(result), prompt=args.spec)
    with open(eval_base, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    dims = report["dimensions"]
    print(f"\n  Overall score : {dims['overall']['score']}/100")
    print(f"  {dims['overall']['reasoning'][:120]}...")
    print(f"\n  Dimension scores:")
    for dim, val in dims.items():
        print(f"    {dim:20s} {val['score']:3d}/100")
    if report.get("highlights"):
        print(f"\n  Highlights:")
        for h in report["highlights"]:
            print(f"    ✓ {h}")
    if report.get("failure_modes"):
        print(f"\n  Failure modes:")
        for fm in report["failure_modes"]:
            print(f"    ✗ {fm}")
    print(f"\n  Evaluation report saved → {eval_base}")
    print(f"  Stage 1 elapsed    : {time.time() - t1:.1f}s")

    print(f"\n{'─'*60}")
    print(f"  STAGE 2 — MEMORY AGENT")
    print(f"{'─'*60}")
    t2 = time.time()
    mem_summary = memory_agent(eval_base)
    patterns_extracted_path = str(out_dir / "patterns_extracted.json")
    with open(patterns_extracted_path, "w", encoding="utf-8") as f:
        json.dump(mem_summary["extracted"], f, indent=2)
    print(f"  Patterns added     : {mem_summary['patterns_added']}")
    print(f"  Anti-patterns added: {mem_summary['anti_patterns_added']}")
    print(f"  Skipped (duplicate): {mem_summary['skipped_duplicates']}")
    print(f"  Patterns file      : {patterns_extracted_path}")
    print(f"  Stage 2 elapsed    : {time.time() - t2:.1f}s")

    print(f"\n{'─'*60}")
    print(f"  STAGE 3 — QUERY INTERFACE")
    print(f"{'─'*60}")
    t3 = time.time()
    patterns = query_memory(args.spec, top_k=5)
    if not patterns:
        print("  (knowledge base is empty)")
    for i, p in enumerate(patterns, 1):
        impact = f"{p['score_impact']:+d}" if p["score_impact"] is not None else "n/a"
        print(f"\n  {i}. [{p['type']}]  sim={p['similarity']:.3f}  dim={p['dimension']}  impact={impact}")
        print(f"     {p['text']}")
        if p.get("relevance_reason"):
            print(f"     Why: {p['relevance_reason']}")
    print(f"  Stage 3 elapsed    : {time.time() - t3:.1f}s")

    print(f"\n{'─'*60}")
    print(f"  STAGE 4 — TEMPLATE REFINER")
    print(f"{'─'*60}")
    t4 = time.time()
    if args.template_dir:
        refiner_out_dir = str(out_dir)
        ref_report = template_refiner_agent(
            template_dir=args.template_dir,
            output_dir=refiner_out_dir,
        )
        changes   = ref_report.get("proposed_changes", [])
        conflicts = ref_report.get("conflicts", [])
        print(f"\n  Patterns reviewed : {ref_report['meta']['patterns_reviewed']}")
        print(f"  Changes proposed  : {len(changes)}")
        print(f"  Conflicts flagged : {len(conflicts)}")
        for c in changes:
            conf = c.get("confidence", 0)
            flag = "  ⚠ low confidence" if conf < 0.6 else ""
            print(f"\n  [{c['id']}] → {c['target_file']}  ({c['type']})  confidence={conf:.2f}{flag}")
            print(f"     {c['rationale'][:120]}")
        if conflicts:
            print(f"\n  Conflicts requiring human review:")
            for cf in conflicts:
                print(f"    ⚡ patterns {cf['pattern_ids']}: {cf['description'][:100]}")
        print(f"\n  Diffs saved       → {refiner_out_dir}/proposed_diffs/")
        print(f"  Log appended      → {refiner_out_dir}/REFINEMENT_LOG.md")
        print(f"  Stage 4 elapsed    : {time.time() - t4:.1f}s")
    else:
        print(f"\n  Skipped — pass --template-dir to enable")


if __name__ == "__main__":
    main()
