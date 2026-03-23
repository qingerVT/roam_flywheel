# query

Retrieves the most relevant patterns from the knowledge base for a given game specification, ranked by cosine similarity to the query embedding, with a Gemini-generated relevance explanation for each result.

Depends on helpers imported from `memory_agent`: `_get_db`, `_embed`, `_unpack_embedding`, `_cosine_sim`, `_configure_genai`.

---

## Functions

### `query_memory`

```python
def query_memory(game_spec: str, top_k: int = 5, db: str = None) -> list[dict]
```

Retrieve the most relevant patterns for a game spec.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `game_spec` | `str` | Natural-language description of the game being designed or generated |
| `top_k` | `int` | Number of results to return (default: `5`) |
| `db` | `str \| None` | Path to the knowledge-base SQLite file; defaults to `memory.db` via `_get_db` |

**Returns** — `list[dict]`, ordered by descending cosine similarity. Each dict contains:

| Key | Type | Description |
|---|---|---|
| `id` | `int` | Row ID in the DB |
| `type` | `str` | `"pattern"` or `"anti_pattern"` |
| `text` | `str` | Actionable insight |
| `game_type` | `str` | Game genre classification |
| `mechanic_type` | `str` | Mechanic category |
| `dimension` | `str` | Evaluation dimension |
| `score_impact` | `int` | Estimated score delta |
| `evidence` | `str` | Supporting evidence from source report |
| `source_game_id` | `str` | Originating game |
| `similarity` | `float` | Cosine similarity to `game_spec` (rounded to 4 dp) |
| `relevance_reason` | `str` | One-sentence Gemini explanation of why this pattern applies |

**Notable behaviour**

- Returns `[]` immediately (no API calls) if the knowledge base contains no rows.
- Embeds the full pattern corpus in-memory at query time; suitable for up to ~100 k patterns.
- The `relevance_reason` field is populated via a separate Gemini call. If that call fails for any reason, `relevance_reason` is set to `""` for all results rather than raising.
- Requires `GEMINI_API_KEY` in the environment (configured via `_configure_genai`).
