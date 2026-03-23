# Example usage:
# python3 query.py "a platformer where you collect coins"
# python3 query.py "top-down shooter with power-ups" --top-k 3
# python3 query.py "a platformer where you collect coins" --database /path/to/memory.db

import json
import sys
import argparse

import numpy as np
import google.generativeai as genai

from memory_agent import _get_db, _embed, _unpack_embedding, _cosine_sim, _configure_genai


def query_memory(game_spec: str, top_k: int = 5, db: str = None) -> list[dict]:
    """
    Retrieve the most relevant patterns for a game spec.

    Parameters
    ----------
    game_spec : Natural language description of the game being designed/generated.
    top_k     : Number of results to return.

    Returns
    -------
    List of pattern dicts ranked by relevance, each with:
      - type, text, dimension, score_impact, mechanic_type, evidence, source_game_id
      - similarity      : cosine similarity score (0–1)
      - relevance_reason: why this pattern applies to the given game spec
    Returns [] if the knowledge base is empty.
    """
    conn = _get_db(db)
    rows = conn.execute(
        "SELECT id, type, text, game_type, mechanic_type, dimension, "
        "score_impact, evidence, source_game_id, embedding FROM patterns"
    ).fetchall()
    conn.close()

    # Cold start: nothing stored yet
    if not rows:
        return []

    query_vec = np.array(_embed(game_spec), dtype=np.float32)

    # Rank all patterns by cosine similarity to the game spec
    candidates = []
    for row in rows:
        id_, type_, text, game_type, mechanic_type, dim, score_impact, evidence, src, emb_blob = row
        sim = _cosine_sim(query_vec, _unpack_embedding(emb_blob))
        candidates.append({
            "id":             id_,
            "type":           type_,
            "text":           text,
            "game_type":      game_type,
            "mechanic_type":  mechanic_type,
            "dimension":      dim,
            "score_impact":   score_impact,
            "evidence":       evidence,
            "source_game_id": src,
            "similarity":     round(sim, 4),
        })

    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    top = candidates[:top_k]

    # Ask Gemini to explain why each pattern is relevant to this specific game spec
    _configure_genai()
    model = genai.GenerativeModel("gemini-2.5-flash")
    patterns_text = "\n".join(
        f"{i+1}. [{p['type']}] (dim={p['dimension']}, impact={p['score_impact']:+d}) {p['text']}"
        for i, p in enumerate(top)
    )
    relevance_prompt = f"""\
Game spec: "{game_spec}"

The following patterns were retrieved from a knowledge base of past playtesting evaluations.
For each pattern, write one concise sentence explaining specifically why it is relevant to this game spec.
Be concrete — reference the game spec details.

Patterns:
{patterns_text}

Return ONLY valid JSON (no markdown):
{{
  "reasons": ["<reason for pattern 1>", "<reason for pattern 2>", ...]
}}"""

    try:
        response = model.generate_content(relevance_prompt)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip().rstrip("```").strip()
        start, end = raw.find("{"), raw.rfind("}") + 1
        reasons = json.loads(raw[start:end]).get("reasons", [])
    except Exception:
        reasons = []

    for i, p in enumerate(top):
        p["relevance_reason"] = reasons[i] if i < len(reasons) else ""

    return top


def main():
    parser = argparse.ArgumentParser(description="Query the game design pattern knowledge base.")
    parser.add_argument("game_spec", help="Natural language description of the game")
    parser.add_argument("--top-k", type=int, default=5, help="Number of results (default: 5)")
    parser.add_argument("--database", default=None, help="Path to knowledge base DB (default: memory.db)")
    args = parser.parse_args()

    results = query_memory(args.game_spec, top_k=args.top_k, db=args.database)

    if not results:
        print("Knowledge base is empty. Run memory_agent.py first to ingest eval reports.")
        sys.exit(0)

    print(f"\nTop {len(results)} patterns for: \"{args.game_spec}\"\n")
    print("=" * 70)

    for i, r in enumerate(results, 1):
        impact = f"{r['score_impact']:+d}" if r["score_impact"] is not None else "n/a"
        print(f"\n{i}. [{r['type']}]  sim={r['similarity']:.3f}  dim={r['dimension']}  impact={impact}")
        print(f"   {r['text']}")
        if r.get("relevance_reason"):
            print(f"   Why relevant : {r['relevance_reason']}")
        print(f"   Evidence     : {r['evidence']}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
