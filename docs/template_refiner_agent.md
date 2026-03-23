# template_refiner_agent

Proposes targeted, surgical diffs to codegen template files based on accumulated playtesting knowledge. It reads the full knowledge base and the current template files, reasons about which patterns are strong enough to warrant a permanent prompt change, and emits proposed changes with confidence scores. It **never auto-applies** changes.

## Template Files

The agent is scoped to exactly four template files:

| File | Governs |
|---|---|
| `system_role.md` | Agent identity, technology stack, scope boundaries |
| `instructions.md` | Game structure, module map, ctx fields, network protocol, gameplay constants |
| `quality_defaults.md` | Visual quality, art style, performance standards, error handling, multiplayer correctness |
| `agent_rituals.md` | Decision-making rituals, pre/post-edit steps, debugging checklists, hygiene |

---

## Functions

### `template_refiner_agent`

```python
def template_refiner_agent(
    template_dir: str | Path,
    db_path: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> dict
```

Propose targeted diffs to codegen template files based on the knowledge base.

**Parameters**

| Name | Type | Description |
|---|---|---|
| `template_dir` | `str \| Path` | Directory containing `system_role.md`, `instructions.md`, `quality_defaults.md`, `agent_rituals.md` |
| `db_path` | `str \| Path \| None` | Path to `memory.db`; defaults to `memory.db` adjacent to the script |
| `output_dir` | `str \| Path \| None` | If provided, per-file diff JSONs are written to `output_dir/proposed_diffs/` and a human-readable entry is appended to `output_dir/REFINEMENT_LOG.md` |

**Returns** — `dict` with keys:

| Key | Type | Description |
|---|---|---|
| `proposed_changes` | `list[dict]` | Ordered list of proposed change objects (see below) |
| `conflicts` | `list[dict]` | Contradictory pattern pairs requiring human review |
| `changelog` | `str` | Markdown summary of what changed, in which file, and why |
| `meta` | `dict` | Run metadata: `generated_at`, `patterns_reviewed`, `templates_read`, `db_path`, `template_dir` |

**Structure of a proposed change object**

| Key | Type | Description |
|---|---|---|
| `id` | `str` | Unique identifier, e.g. `"change-001"` |
| `target_file` | `str` | One of the four template filenames |
| `type` | `str` | `"addition"` or `"edit"` |
| `anchor` | `str` | (additions only) Exact line after which to insert new text |
| `old_text` | `str` | (edits only) Verbatim text to replace; must appear in the current file |
| `new_text` | `str` | Text to insert or use as replacement |
| `rationale` | `str` | Evidence-backed explanation |
| `confidence` | `float` | 0.0–1.0 (see confidence scoring below) |
| `source_game_ids` | `list[str]` | Game IDs the finding originated from |
| `pattern_ids` | `list[int]` | KB row IDs backing this change |

**Confidence scoring**

| Range | Meaning |
|---|---|
| 0.9–1.0 | Pattern appears across multiple games with consistent direction |
| 0.6–0.89 | Single game, strong specific evidence |
| 0.3–0.59 | Plausible but speculative; flagged for human review |
| < 0.3 | Not proposed |

**Notable behaviour**

- Requires `GEMINI_API_KEY` in the environment. Raises `EnvironmentError` if absent.
- Returns early with empty `proposed_changes` and `conflicts` if the knowledge base contains no patterns, without making any Gemini API call.
- Template files that do not exist on disk are treated as empty strings; no error is raised.
- When two patterns imply contradictory changes to the same location, a conflict entry is emitted instead of silently choosing one.
- Raises `ValueError` if Gemini returns unparseable JSON.
- When `output_dir` is supplied, `proposed_diffs/` is created if it does not exist. Existing `REFINEMENT_LOG.md` is appended to, not overwritten.
