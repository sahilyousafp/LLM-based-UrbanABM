# StreetPLM — Street Perception Language Model Pipeline

VLM-based street-perception pipeline for the Barcelona Eixample study area.
Fetches Google Street View images, runs **Qwen2.5-VL-3B-Instruct** on each, and
produces a structured JSON record describing 10 perceptual categories per location —
grounded to five horizontal image zones — alongside nearby landmark data.

Designed to run as a [Lightning AI](https://lightning.ai) job on an L4 GPU (24 GB VRAM).
Results feed directly into the LLM-based Urban ABM as environmental perception data for
agent decision-making.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [File Structure](#file-structure)
3. [VLM Schema Design](#vlm-schema-design)
4. [Token Budget Analysis](#token-budget-analysis)
5. [Prompt Engineering](#prompt-engineering)
6. [Grammar-Constrained Generation](#grammar-constrained-generation)
7. [JSON Parsing Pipeline](#json-parsing-pipeline)
8. [Robustness Features](#robustness-features)
9. [Repeatability Evidence](#repeatability-evidence)
10. [Output Schema Reference](#output-schema-reference)

---

## Quick Start

### Environment variables (Lightning AI Secrets or `.env`)

```
GOOGLE_STREETVIEW_API_KEY=...   # Street View Static API
HF_TOKEN=...                    # HuggingFace token (Qwen model access)
GCP_PROJECT_ID=...              # GCP project for BigQuery (optional — grid fallback used if absent)
LANDMARK_DB_PATH=...            # Optional: path to eixample_osm.duckdb
```

### Install

```bash
pip install -r requirements.txt
# lm-format-enforcer auto-installs on first run if missing
```

### Test a single image

```bash
python test_single_image.py \
  --image /teamspace/studios/this_studio/StreetPLM/images/sv_41.396306_2.159444_h90.jpg \
  --raw
```

### Run the full pipeline

```bash
python street_plm_job.py --output-dir /teamspace/studios/this_studio/StreetPLM
```

### Trial run (one point, verbose output)

```bash
python street_plm_job.py --trial --trial-lat 41.3981 --trial-lon 2.1690
```

### Visualise results

Open `viewer.html` in a browser and select the output folder — Leaflet map with
per-point image strip, zone badges, and raw JSON tab.

---

## File Structure

| File | Purpose |
|---|---|
| `street_plm_job.py` | Main pipeline — sampling, Street View fetch, VLM inference, JSON output |
| `test_single_image.py` | Single-image test harness; imports all machinery from the job script |
| `viewer.html` | Self-contained browser viewer for pipeline results (Leaflet + `webkitdirectory`) |
| `requirements.txt` | Python dependencies |
| `.env` | Local secrets (not committed) |
| `Tests/Prompt consistency/` | Three reference runs proving output repeatability |

---

## VLM Schema Design

### Zone model

Every observation is anchored to one of five horizontal image zones:

```
far_left | left | center | right | far_right
```

This spatial grounding allows downstream LLM agents to reason about which side of
the street a feature is on — critical for pedestrian route preference and comfort
scoring. Without zones, the model outputs aggregate labels that cannot be used for
directional navigation decisions.

**Source:** [`_ZONE_ATTRS` L152](street_plm_job.py#L152)

### 10 perceptual categories

| Category | Captures | Key fields |
|---|---|---|
| `lighting` | Natural / artificial light quality per zone | `element`, `condition` (dark/dim/adequate/bright) |
| `spatial_character` | Geometry of the walkable envelope | `width`, `enclosure`, `passability`, `lane_type`, `crossing` |
| `crowdedness` | Pedestrian density | `density_level` (sparse/moderate/dense) |
| `greenery` | Vegetation type and coverage | `element`, `coverage` |
| `street_amenities` | All fixed street objects: seating, lamps, bins, bollards, fountains, hydrants, bike racks, info boards, bus shelters, kiosks, advertising panels | `element`, `presence` |
| `architecture` | Building style and type | `element`, `style` |
| `material` | Ground and facade surface | `surface` |
| `color` | Dominant tone | `tone` |
| `clarity` | Visual visibility / occlusion | `level` |
| `cleanliness` | Maintenance state and litter | `level`, `litter` |
| `visible_text` | Legible signs and labels | `text`, `zone`, `type` |

**Why `spatial_character` has no `element` field:** Early iterations included a
free-text `element` field in this category. The model consistently filled it with
greenery descriptions ("plane trees", "hedges"), bleeding vegetation data into a
geometry-only category. Removing `element` from `spatial_character` cleanly
separates walkability geometry from vegetation perception.

**Source:** [`StreetSceneAnalysis` L159](street_plm_job.py#L159)

### Pydantic schema and normalisation

```python
class StreetSceneAnalysis(BaseModel):
    scene            : str  = "unknown"
    lighting         : list = Field(default_factory=list)
    spatial_character: list = Field(default_factory=list)
    ...
    @field_validator("lighting", "spatial_character", ..., mode="before")
    def _coerce_to_list(cls, v):
        if isinstance(v, dict): return [v]   # bare dict → single-item list
        return v if isinstance(v, list) else []
```

The `_coerce_to_list` validator silently corrects the model returning a bare dict
instead of a list — a common failure mode for 3B models on nested schemas.

A `_KEY_ALIASES` dictionary (L175) maps 40+ model-hallucinated key names
(`"openness"`, `"vegetation"`, `"visual_clarity"`, etc.) to canonical field names,
so the schema is tolerant of natural model paraphrasing without requiring exact key matches.

**Source:** [`StreetSceneAnalysis` L159](street_plm_job.py#L159) · [`_KEY_ALIASES` L175](street_plm_job.py#L175)

---

## Token Budget Analysis

Token budget is the tightest constraint for a 3B VLM. Every number below is a
deliberate decision, not a default.

### Vision tokens

Qwen2.5-VL uses 28×28 pixel patches. At `MAX_PIXELS = 1280 × 28 × 28` (~1 M pixels),
a 640×640 Street View image uses approximately **1280 vision tokens**.

```
MAX_PIXELS = 1280 * 28 * 28   # ~1 M pixels — full resolution on 24 GB VRAM
```

VRAM tiers are set automatically at load time:

| GPU VRAM | MAX_PIXELS | Vision tokens | Setting |
|---|---|---|---|
| ≥ 24 GB (L4, A100) | 1 280 × 28² = ~1 M | ~1280 | Full quality |
| ≥ 16 GB | 784 × 28² = ~615 K | ~784 | Safe |
| < 16 GB | 512 × 28² = ~401 K | ~512 | Minimum |
| CPU | 256 × 28² = ~201 K | ~256 | Fallback |

**Source:** [`load_model` L499](street_plm_job.py#L499), VRAM tiers [L512–L517](street_plm_job.py#L512)

### Generation tokens

```
MAX_NEW_TOKENS = 640
# Derivation: 10 categories × 1.5 observations × ~35 tokens/obs ≈ 525 + margin
```

Per-observation token cost breakdown (approximate BPE tokens):

```
{"zone":"center","element":"natural daylight","condition":"adequate"}
= 1 + 8 + 5 + 10 + 5 + 3 + 7 ≈ 30–40 tokens
```

With 10 categories × 1.5 average observations × ~35 tokens = **525 tokens minimum**.
`MAX_NEW_TOKENS = 640` adds ~20% headroom for longer element descriptions and
visible_text entries without wasting GPU time on over-generation.

### Prompt token cost

The few-shot prompt (`_SCENE_PROMPT`) costs approximately **300–350 tokens** at
inference — the instructions (~80 tokens) plus the filled example (~220 tokens).
This is intentionally front-loaded: a concrete example is worth more than additional
generation budget for a 3B model.

**Source:** [`MAX_NEW_TOKENS` L138](street_plm_job.py#L138) · [`_SCENE_PROMPT` L345](street_plm_job.py#L345)

### Total context at inference (L4, full resolution)

| Component | Tokens |
|---|---|
| System / chat template | ~50 |
| Vision tokens (640×640 image) | ~1280 |
| Instruction + few-shot prompt | ~330 |
| JSON prime (`{`) | 1 |
| **Input total** | **~1661** |
| Generation budget | 640 |
| **Peak context** | **~2300** |

Well within Qwen2.5-VL-3B's 32 768 token context window.

---

## Prompt Engineering

### Why few-shot over template-only

Early versions used a compact schema template with placeholder strings
(`"<zone>"`, `"dark|dim|adequate|bright"`). The 3B model consistently echoed
these placeholder strings verbatim rather than filling them with image observations.
A filled example of a *different* image gives the model a concrete output to imitate,
which is far more reliable than instruction-following alone at this parameter scale.

### Prompt structure

```
[Urban analyst role] + [Zone definitions] + [Fill instruction] + [Format cue]
[Filled example for a different image — 2 observations per field]
[Now analyse the given image.]
[Output only JSON:]
```

The example shows two observations for most fields, making it clear that single-entry
arrays are the minimum, not the norm. This primes the model toward richer output.

**Source:** [`_SCENE_PROMPT` L345](street_plm_job.py#L345)

### JSON priming

```python
text += "{"   # appended to the chat-template output before tokenisation
```

The opening `{` is appended to the *input* (not generated). The model therefore starts
generating from `"scene":...` — it cannot produce any preamble text, code fences, or
explanation before the JSON. The decoded output is prepended with `{` to reconstruct
the full object.

**Source:** [`_infer_scene` L570](street_plm_job.py#L570)

### Format cue in instructions

```
"Fill EVERY list field with 1-2 real observations — arrays MUST start with [{ not []."
```

The phrase `[{ not []` makes the constraint syntactically explicit: at the token level,
`{` is named as the expected character after `[`, not `]`. This biases the model's
token probability distribution before any generation constraint takes over.

### Echo detection

`_ECHO_SENTINELS` (L376) is a frozenset of every pipe-separated enum string that
appears in the prompt (e.g. `"dark|dim|adequate|bright"`). If a parsed value matches
any sentinel, the model has echoed the template rather than filling it. The result is
discarded and the default empty schema is returned.

**Source:** [`_ECHO_SENTINELS` L376](street_plm_job.py#L376)

---

## Grammar-Constrained Generation

This is the primary mechanism ensuring populated output. Temperature and prompting
are probabilistic; grammar constraints are deterministic.

### The core problem

At `temperature=0.1` (near-greedy sampling), after the model emits `"lighting": [`,
the token `]` has slightly higher probability than `{` for images the model is
uncertain about. Near-greedy sampling picks `]` every time, collapsing the entire
array to `[]`. This is not a prompt problem — it is a consequence of the model's
learned probability distribution on sparse-evidence inputs.

### How lm-format-enforcer works

[lm-format-enforcer](https://github.com/noamgat/lm-format-enforcer) integrates with
`model.generate()` via `prefix_allowed_tokens_fn`. At every generation step, the
library:

1. Decodes the full token sequence so far
2. Advances an internal JSON schema state machine
3. Returns the set of token IDs that are **valid continuations** of the schema
4. All other tokens are masked to `-inf` before sampling

When the model has generated `"lighting": [` and the schema specifies `minItems: 1`,
the closing token `]` is **not in the valid set** — it cannot be chosen regardless of
its probability. The model is forced to generate `{`, then fill the object, before
`]` becomes valid again.

### Generation schema

```python
_GENERATION_SCHEMA = {
    "type": "object",
    "required": ["scene", "lighting", ..., "visible_text"],
    "properties": {
        "scene": {"type": "string"},
        # All 10 zone-attribute categories:
        "lighting": {"type": "array", "minItems": 1, "maxItems": 3,
                     "items": {"type": "object"}},
        ...
        # visible_text may genuinely be absent:
        "visible_text": {"type": "array", "minItems": 0, "maxItems": 4,
                         "items": {"type": "object"}},
    },
}
```

Items are typed as generic `{"type": "object"}` rather than with strict property
schemas. This lets the model freely choose field names from the few-shot example
without being blocked by enum constraints on object contents — which would cause the
library's state machine to reach dead ends on a 3B model.

**Source:** [`_GENERATION_SCHEMA` L252](street_plm_job.py#L252)

### Generation kwargs

```python
gen_kwargs = dict(
    max_new_tokens     = MAX_NEW_TOKENS,   # 640
    do_sample          = True,
    temperature        = 0.4,              # exploration without incoherence
    top_p              = 0.9,              # nucleus sampling
    repetition_penalty = 1.05,
)
if _LMFE_AVAILABLE:
    gen_kwargs["prefix_allowed_tokens_fn"] = _lmfe_build_prefix_fn(
        _processor.tokenizer, _LmfeSchemaParser(_GENERATION_SCHEMA)
    )
else:
    gen_kwargs["min_new_tokens"] = 200     # fallback: force past scene-only output
```

`temperature=0.4` is chosen as the fallback sampling rate: high enough to explore
`{` over `]` for uncertain images, low enough to keep output coherent for a 3B model.
`top_p=0.9` (nucleus sampling) restricts the candidate pool to avoid low-quality long-tail
tokens while still allowing `{` to compete with `]`.

**Source:** [`_infer_scene` L586–L613](street_plm_job.py#L586)

### Bootstrap

`lm-format-enforcer` is auto-installed at script start if not present:

```python
try:
    from lmformatenforcer import JsonSchemaParser as _LmfeSchemaParser
    ...
    _LMFE_AVAILABLE = True
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "lm-format-enforcer"])
    ...
```

**Source:** [L57–L76](street_plm_job.py#L57)

---

## JSON Parsing Pipeline

Even with grammar constraints, the raw model output may be truncated at
`MAX_NEW_TOKENS` or contain trailing whitespace/repeated braces. The parser applies
four strategies in order:

### Strategy 1 — Direct parse

```python
json.loads(text)          # exact match
json.loads(text + "}")    # single missing closing brace
```

Handles the common case where generation completed cleanly.

### Strategy 2 — Brace-counting extractor

```python
def _extract_first_json_obj(text: str) -> str | None:
    depth, in_str, escape = 0, False, False
    for i, ch in enumerate(text[start:], start):
        ...
        if ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
```

Walks the text character by character tracking brace depth and string context.
Returns the **first syntactically balanced** `{...}` substring. This is immune to
repeated `}}}` padding that greedy regex (`\{[\s\S]*\}`) mistakes for the object
boundary — greedy matching extends to the *last* `}`, which may be junk.

**Source:** [`_extract_first_json_obj` L411](street_plm_job.py#L411)

### Strategy 3 — Truncation recovery

Counts unmatched `{` vs `}` and appends the right number of closing braces, then
strips the last incomplete key if needed. Handles outputs truncated mid-field by
`MAX_NEW_TOKENS`.

### Strategy 4 — Key-value regex fallback

```python
re.finditer(r'"([^"]+)"\s*:\s*"([^"]*)"', text)
```

Extracts at minimum the `"scene"` string field even if the rest of the JSON is
malformed. Provides a non-empty result of last resort.

**Source:** [`_parse_scene_json` L442](street_plm_job.py#L442)

---

## Robustness Features

### Blank image detection

Google Street View serves grey placeholder frames (~5–50 KB) for areas with limited
panoramic coverage. These pass the existing file-size check but cause the VLM to
output near-nothing. Images are screened before inference:

```python
def _is_blank_image(img, std_threshold=18.0):
    arr = np.array(img.convert("L"), dtype=np.float32)
    return float(arr.std()) < std_threshold
```

Pixel luminance standard deviation below 18.0 indicates a near-uniform grey frame.
Flagged images are recorded with `"_blank": True` and zero latency — skipped without
consuming GPU time.

**Source:** [`_is_blank_image` L626](street_plm_job.py#L626) · [`analyze_image` L632](street_plm_job.py#L632)

### OCR noise filtering

`_filter_ocr_noise` (L299) removes three categories of spurious `visible_text` entries:

| Pattern | Regex | Example |
|---|---|---|
| Google watermark | `^(©\s*)?(20\d{2}\s+)?google(\s+maps)?` | `© 2024 Google Maps` |
| EU license plates | `^[A-Z]{1,4}[\s·-]?\d{2,4}...` | `VJL 360`, `1234 ABC` |
| Echoed enum string | `"\|" in type` | `sign\|label\|number\|other` |

The OCR instruction scans only the **upper 90% of the frame** (`visible_text: upper 90% of frame only` in the prompt) to avoid watermarks and vehicle plates in the bottom strip before they even reach the filter.

**Source:** [`_GOOGLE_WATERMARK_RE` L285](street_plm_job.py#L285) · [`_LICENSE_PLATE_RE` L292](street_plm_job.py#L292) · [`_filter_ocr_noise` L299](street_plm_job.py#L299)

### BigQuery fallback

If GCP credentials are unavailable (common on fresh Lightning AI studios), the
pipeline falls back automatically to a uniform 50 m grid across the study BBOX
rather than failing:

```python
try:
    bq_client = bigquery.Client(project=GCP_PROJECT_ID)
except Exception as exc:
    log.warning("BigQuery unavailable (%s) — falling back to BBOX grid sampler.", exc)
    return _grid_sample_bbox(output_root)
```

The grid generates ~160 sample points with headings 0° and 90° per location —
sufficient coverage for the current study area (`2.166667,41.396468 → 2.172096,41.399895`).

**Source:** [`_grid_sample_bbox` L804](street_plm_job.py#L804)

### Resume safety

The pipeline checks for an existing `*_analysis.json` before processing each point:

```python
pending = [pt for pt in sample_points
           if not _result_path(results_dir, pt["id"]).exists()]
```

Interrupted jobs resume from where they left off without re-fetching images or
re-running inference on completed points.

**Source:** [`run_pipeline` L1022](street_plm_job.py#L1022)

---

## Repeatability Evidence

Three independent runs on the same image (`sv_41.396306_2.159444_h90.jpg`) produced
identical output — same scene text, same field values, same token count:

| Run | latency_ms | approx_tokens | scene text |
|---|---|---|---|
| Output_1.json | 20 631.2 | 153 | "A wide city street lined with tall buildings..." |
| Output_2.json | 20 627.4 | 153 | "A wide city street lined with tall buildings..." |
| Output_3.json | 20 670.3 | 153 | "A wide city street lined with tall buildings..." |

All three runs: identical `scene`, `lighting`, `spatial_character`, `crowdedness`,
`greenery`, `street_furniture`, `architecture`, `material`, `color`, `clarity`,
`cleanliness`, and `visible_text` values.

**Source:** `Tests/Prompt consistency/Output_1.json` through `Output_3.json`

### Why output is stable

Three mechanisms work together:

1. **Grammar constraints** (`_GENERATION_SCHEMA`) force a deterministic schema path
   regardless of stochastic token sampling — the structure is invariant even when
   specific word choices vary slightly.

2. **Low temperature + nucleus sampling** (`temperature=0.4`, `top_p=0.9`) keeps
   token selection close to the mode of the distribution without fully collapsing to
   greedy decoding (which causes `[]` collapse on uncertain images).

3. **Pydantic normalisation** (`_coerce_to_list`, `_KEY_ALIASES`) smooths over
   surface variation in the raw model output before the record is written to disk —
   a bare dict becomes a one-item list, `"openness"` maps to `"spatial_character"`,
   etc.

---

## Output Schema Reference

Each completed location writes a single `{point_id}_analysis.json`:

```jsonc
{
  "metadata": {
    "timestamp"         : "20260523_152453",
    "latitude"          : 41.396306,
    "longitude"         : 2.159444,
    "heading"           : 90.0,
    "street_name"       : "",           // from BigQuery / empty for grid fallback
    "highway_type"      : "unknown",
    "edge_id"           : "grid",
    "dist_along_edge_m" : null,
    "source_image"      : "sv_41.396306_2.159444_h90.jpg",
    "model"             : "Qwen/Qwen2.5-VL-3B-Instruct",
    "device"            : "cuda",
    "latency_ms"        : 20631.2,
    "status"            : "ok"          // "ok" | "no_streetview"
  },
  "scene_analysis": {
    "scene": "A wide city street lined with tall buildings on either side...",
    "lighting": [
      {"zone": "center", "element": "natural daylight", "condition": "adequate"}
    ],
    "spatial_character": [
      {"zone": "center", "width": "wide", "enclosure": "open",
       "passability": "clear", "lane_type": "road", "crossing": "pedestrian crossing"}
    ],
    "crowdedness": [
      {"zone": "center", "density_level": "moderate"}
    ],
    "greenery": [
      {"zone": "left", "element": "trees", "coverage": "sparse"}
    ],
    "street_amenities": [
      {"zone": "right", "element": "waste bin", "presence": "few"}
    ],
    "architecture": [
      {"zone": "left", "element": "modern buildings", "style": "mixed"}
    ],
    "material": [
      {"zone": "center", "surface": "concrete"}
    ],
    "color": [
      {"zone": "left", "tone": "neutral"}
    ],
    "clarity": [
      {"zone": "center", "level": "good"}
    ],
    "cleanliness": [
      {"zone": "center", "level": "clean", "litter": "none"}
    ],
    "visible_text": [
      {"text": "Carrer de Provenca", "zone": "far_left", "type": "sign"}
    ]
  },
  "nearby_landmarks": [
    {"name": "Bar Calvet", "category": "bar", "distance_m": 42.1, "bearing": "NE"}
  ]
}
```

Points with no Street View coverage write a stub with `"status": "no_streetview"`
and `"scene_analysis": null`. Blank/placeholder images write `"_blank": true`
alongside a default empty analysis.
