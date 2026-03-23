# memory_agent

Ingests evaluation reports into a persistent pattern knowledge base backed by SQLite (structured metadata) and Gemini embeddings (`gemini-embedding-001`) for semantic similarity search. Cosine similarity is computed in-memory at query time.

**Default DB path:** `memory.db` in the same directory as the script.

---

## Functions

### `memory_agent`

```python
def memory_agent(report_path: str | Path) -> dict
```

Ingest an evaluation report into the knowledge base.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `report_path` | `str \| Path` | Path to a JSON file produced by `evaluator_agent` |

**Returns** — `dict` with keys:

| Key | Type | Description |
|---|---|---|
| `patterns_added` | `int` | Number of new patterns stored |
| `anti_patterns_added` | `int` | Number of new anti-patterns stored |
| `skipped_duplicates` | `int` | Entries skipped because cosine similarity ≥ 0.92 to an existing entry |
| `source_game_id` | `str` | `game_id` from the report, or the file stem if absent |
| `extracted` | `dict` | Raw extraction result from Gemini (`{"patterns": [...], "anti_patterns": [...]}`) |

**Notable behaviour**

- Requires `GEMINI_API_KEY` in the environment. Raises `EnvironmentError` if absent.
- Raises `FileNotFoundError` if `report_path` does not exist.
- Raises `ValueError` if Gemini returns unparseable JSON during pattern extraction.
- The SQLite table (`patterns`) is created automatically on first run.
- Duplicate detection uses cosine similarity against all existing embeddings loaded into memory before inserting the batch. Within-batch duplicates are also caught.
- Deduplication threshold: **0.92** cosine similarity.
- Prints each pattern/anti-pattern to stdout as it is processed, with status (`stored` or `skipped`).

**Schema of each stored pattern row**

| Column | Type | Description |
|---|---|---|
| `type` | `TEXT` | `"pattern"` or `"anti_pattern"` |
| `text` | `TEXT` | Actionable insight text |
| `game_type` | `TEXT` | e.g. `platformer`, `puzzle`, `shooter` |
| `mechanic_type` | `TEXT` | e.g. `scoring`, `movement`, `camera`, `ui` |
| `dimension` | `TEXT` | One of the 8 evaluation dimensions |
| `score_impact` | `INTEGER` | Estimated point change (positive = helps, negative = hurts) |
| `evidence` | `TEXT` | Direct quote or observation from the source report |
| `source_game_id` | `TEXT` | Originating game identifier |
| `embedding` | `BLOB` | 32-bit float packed vector |
