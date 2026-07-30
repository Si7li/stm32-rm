# Semantic (context-aware) table extraction — design

Goal: stop emitting one flat headers/rows shape for every table. Instead **classify
each table into a type** and extract it into a **structure that carries meaning** for
that type. This mirrors the instructor's datasheet example, which already used typed
`table_content` shapes (`parameters`, `pins`, `mappings`, `specifications`,
`dimensions`, `variants`+`features`, generic).

Two-step pipeline:
1. **Classify** the table (by header signature first, caption/section as tiebreakers).
2. **Extract** with the type's schema. Always keep raw `headers`/`rows` too (fallback).

## Evidence — type distribution across RM0490 + RM0008 (409 tables)

GENERIC 301 · REGISTER_MAP 58 · ALT_FUNCTION/PIN 23 · INTERRUPT_VECTOR 8 ·
MEMORY_MAP 7 · FEATURE_MATRIX 7 · PARAMETER 5. (~26% typed; the GENERIC bucket hides
more feature-matrices.) Reference manuals skew register/memory; datasheets skew
parameter/pin/dimension — the taxonomy below is the **union** so one classifier serves
both document families.

## The taxonomy (types + detection signal + semantic output)

Check MOST SPECIFIC first; fall through to GENERIC. Detection is deterministic on the
normalized header row (+ caption/section as tiebreakers). Be conservative — a wrong type
is worse than GENERIC.

### 1. REGISTER_MAP
- Detect: headers contain a descending bit-number run (≥8 numeric cols incl. `31` and
  `0`), or caption matches `register map`.
- Semantic output: `{ "type":"register_map", "registers":[ {"offset":"0x00",
  "name":"CRC_DR", "fields":[{"bits":"31:0","name":"..."}], "reset_value":"0x..."} ] }`
  (pair each register's field-label row with its reset-value row).

### 2. ALT_FUNCTION / PIN
- Detect: header has `AFx` columns or contains `alternate function`; caption has
  `alternate function`/`remap`/`pin (definition|assignment)`.
- Output: `{ "type":"alternate_function", "pins":[ {"pin":"PA0",
  "functions":{"AF0":"...","AF1":"..."}} ] }` (for remap tables:
  `{"function":"CAN_RX","configs":{"00":"PA11","10":"PB8",...}}`).

### 3. INTERRUPT_VECTOR
- Detect: headers ⊇ {`position`,`priority`,`acronym`|`description`,`address`}, or caption
  `vector table`.
- Output: `{ "type":"interrupt_vector", "entries":[ {"position":0,"priority":"-3",
  "acronym":"NMI","description":"...","address":"0x..."} ] }`.

### 4. MEMORY_MAP / BOUNDARY
- Detect: headers ⊇ {`boundary address`|`base address`, `size`|`bus`} or {`area`,`size`};
  caption `boundary addresses`/`memory map`/`register boundary`.
- Output: `{ "type":"memory_map", "regions":[ {"type":"APB1","boundary":"0x4000
  0000-0x4000 03FF","size":"1 KB","area":"TIM2","register_desc":"..."} ] }`.

### 5. PARAMETER / ELECTRICAL
- Detect: headers ⊇ {`min`|`max`|`typ`} and/or {`symbol`,`unit`}.
- Output: `{ "type":"parameter", "parameters":[ {"symbol":"...","parameter":"...",
  "conditions":"...","min":..,"typ":..,"max":..,"unit":".."} ] }` (units pulled from a
  Unit column or header parentheses).

### 6. FEATURE_MATRIX / CAPABILITY
- Detect: first column ∈ {`feature`,`peripheral or function`,`peripheral`} AND remaining
  columns are variant/device names (`STM32...`, instance names) with cell values in
  {`•`,`X`,`-`,`yes`,`no`,number}.
- Output: `{ "type":"feature_matrix", "variants":["STM32C011xx",...],
  "features":[ {"feature":"ADC","values":{"STM32C011xx":"1","STM32C031xx":"1",...}} ] }`.

### 7. GENERIC (fallback)
- Anything unmatched → current `{headers, rows, units, notes, legend}`. No change.

## Design principles (important)

- **Header signature is the primary key**; caption keyword + section are tiebreakers.
  Cells' value vocabulary (bullets, hex, min/typ/max) is a strong secondary signal.
- **Additive, never lossy.** Always keep raw `headers`/`rows` alongside the typed block,
  and set a `type` discriminator. Consumers that only understand generic still work; the
  typed block is the semantic upgrade. (This also makes classification mistakes safe.)
- **Conservative classification.** Only assign a type when its signature clearly matches;
  otherwise GENERIC. Log the chosen type + why (which signal fired).
- **Merged-cell fill must run first** — typed extractors assume every row is complete
  (that's why the earlier merged-cell fix matters here).
- **One classifier, union taxonomy** → works on both reference manuals and datasheets;
  the type mix just shifts by document family.
- **Roll out by value/frequency**, GENERIC always available: REGISTER_MAP → ALT_FUNCTION
  → MEMORY_MAP → INTERRUPT_VECTOR → FEATURE_MATRIX → PARAMETER. Ship one type at a time,
  each behind its own tests, without destabilizing the rest.

## Validation per type

- A classified table must satisfy its schema (e.g. INTERRUPT_VECTOR entries all have an
  address; PARAMETER rows have a unit; FEATURE_MATRIX variants match column count).
- Report a per-type count and a sample; flag tables that *almost* match a type (one
  signal short) for manual review — that's where the taxonomy grows.
- Keep a golden set: a few tables of each type with expected typed output.

## Decisions (made — optimized for selective RAG)

These are final; no external sign-off needed. Each is chosen so the output stays a
safe, filterable, non-lossy corpus.

1. **Type list/names — the union, stable snake_case:** `register_map`, `memory_map`,
   `interrupt_vector`, `alternate_function`, `parameter`, `feature_matrix`, `generic`.
   Names align with the datasheet example where they overlap. Rationale: in a selective
   RAG, `semantic_type` becomes a first-class **filter key** ("search only parameter
   tables"), so the labels must be stable and unambiguous.

2. **Additive, typed block ALONGSIDE generic (inside `table_content`).** Keep raw
   `headers/rows/units/notes/legend` untouched; add `semantic_type` + `semantic`.
   Rationale for selective RAG: `semantic_type` is a pre-retrieval **selector** (filter
   before the semantic search); the typed `semantic` block gives clean, self-describing
   content that embeds/answers better; the raw rows remain as fidelity + fallback so a
   misclassification never loses data. Best of all three at once.

3. **Register maps stay a table type** (`semantic_type: register_map`), not routed into
   the separate per-field register pipeline. Rationale: one filterable corpus, no
   dependency on the half-built register track; register-map questions are retrievable by
   the type filter. (The per-field register KB can remain a complementary future output.)

Selective-RAG note: the retriever should filter on `table_content.semantic_type` (and
`section`) before ranking. `semantic_type` is deterministic and low-cardinality, which is
exactly what makes selective retrieval fast and precise. Existing fields are never
modified, so this stays fully backward-compatible.
