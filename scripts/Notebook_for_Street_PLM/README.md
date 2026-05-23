# StreetPLM — Street Perception Language Model Pipeline

VLM-based street-perception pipeline for the Barcelona Eixample study area.
Fetches Google Street View images (official outdoor imagery only), runs
**Qwen2.5-VL-3B-Instruct** on each, and produces a structured JSON record
describing **5 perceptual categories** per location — grounded to five horizontal
image zones — alongside nearby landmark data.

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
6. [Retry-Based Reliability](#retry-based-reliability)
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
| `Tests/Prompt consistency/5 Fields/` | Reference runs proving 5-field output repeatability |

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

**Source:** [`_ZONE_ATTRS`](street_plm_job.py)

### 5 perceptual categories

The schema was deliberately reduced from 10 to 5 categories. The removed categories
(`architecture`, `material`, `color`, `clarity`, `cleanliness`) caused consistent
attention dilution: a 3B model generating 10 arrays loses image attention after the
first ~2 fields, leaving all subsequent arrays empty. Five categories fit within the
model's effective attention budget, yielding fully-populated output.

| Category | Captures | Key fields |
|---|---|---|
| `lighting` | Natural / artificial light quality per zone | `element` (specific source), `condition` (dark/dim/adequate/bright) |
| `spatial_character` | Geometry of the walkable envelope | `width`, `enclosure`, `passability`, `lane_type`, `crossing` |
| `crowdedness` | Pedestrian density | `density_level` (sparse/moderate/dense) |
| `greenery` | Vegetation type and coverage | `element` (species + description), `coverage` |
| `street_amenities` | All fixed street objects: seating, lamps, bins, bollards, fountains, hydrants, bike racks, info boards, bus shelters, kiosks, advertising panels | `element` (material/style), `presence` |
| `visible_text` | Legible signs and labels (upper 90% of frame) | `text`, `zone`, `type` |

**Why `spatial_character` has no `element` field:** Early iterations included a
free-text `element` field in this category. The model consistently filled it with
greenery descriptions ("plane trees", "hedges"), bleeding vegetation data into a
geometry-only category. Removing `element` from `spatial_character` cleanly
separates walkability geometry from vegetation perception.

### Element precision

Every `element` value must describe the specific visual appearance of the object —
material, style, colour, or scale — not its generic type. The prompt enforces this:

```
element: be specific — include material, style, colour, or scale
(e.g. 'mature plane trees with mottled bark',
      'cast-iron double-arm street lamp',
      'grey cylindrical municipal waste bin',
      'direct overhead sunlight')
```

This yields richer data for downstream agent comfort scoring without increasing
token count, because specificity is injected into the `element` string value rather
than adding new fields.

**Source:** [`_SCENE_PROMPT`](street_plm_job.py)

### Pydantic schema and normalisation

```python
class StreetSceneAnalysis(BaseModel):
    scene            : str  = "unknown"
    lighting         : list = Field(default_factory=list)
    spatial_character: list = Field(default_factory=list)
    crowdedness      : list = Field(default_factory=list)
    greenery         : list = Field(default_factory=list)
    street_amenities : list = Field(default_factory=list)
    visible_text     : list = Field(default_factory=list)

    @field_validator("lighting", "spatial_character", ..., mode="before")
    def _coerce_to_list(cls, v):
        if isinstance(v, dict): return [v]   # bare dict → single-item list
        return v if isinstance(v, list) else []
```

The `_coerce_to_list` validator silently corrects the model returning a bare dict
instead of a list — a common failure mode for 3B models on nested schemas.

A `_KEY_ALIASES` dictionary maps 40+ model-hallucinated key names
(`"openness"`, `"vegetation"`, `"visual_clarity"`, `"street_furniture"`, etc.) to
canonical field names, so the schema tolerates natural model paraphrasing without
requiring exact key matches.

**Source:** [`StreetSceneAnalysis`](street_plm_job.py) · [`_KEY_ALIASES`](street_plm_job.py)

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

**Source:** [`load_model`](street_plm_job.py)

### Generation tokens

```
MAX_NEW_TOKENS = 640
# Derivation: 5 categories × 2 observations × ~45 tokens/obs ≈ 450
#             + visible_text (0-2 entries × ~20 tok) + scene (~25 tok) ≈ 515
#             640 adds ~25% headroom for verbose element descriptions
```

Per-observation token cost (approximate BPE tokens) with specific elements:

```
{"zone":"left","element":"cast-iron double-arm street lamp","presence":"few"}
= ~45–55 tokens   (longer than generic "street lamp" by ~10–15 tokens)
```

With 5 categories × 2 average observations × ~45 tokens = **~450 tokens minimum**.
`MAX_NEW_TOKENS = 640` absorbs verbose element strings and multi-entry `visible_text`
without over-generating.

### Prompt token cost

The few-shot prompt (`_SCENE_PROMPT`) costs approximately **360–420 tokens** at
inference — the instructions (~100 tokens, including the element-precision rule)
plus the filled example (~260 tokens with specific element descriptions).
This is intentionally front-loaded: a concrete example with specific element values
is worth more than additional generation budget for a 3B model.

**Source:** [`MAX_NEW_TOKENS`](street_plm_job.py) · [`_SCENE_PROMPT`](street_plm_job.py)

### Total context at inference (L4, full resolution)

| Component | Tokens |
|---|---|
| System / chat template | ~50 |
| Vision tokens (640×640 image) | ~1280 |
| Instruction + few-shot prompt | ~400 |
| Deep prime (`{"lighting":[{`) | ~6 |
| **Input total** | **~1736** |
| Generation budget | 640 |
| **Peak context** | **~2376** |

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
[Element precision instruction — material, style, colour, or scale]
[street_amenities scope definition]
[scene: 1 sentence instruction]
[Filled example for a different image — 2 observations per field, specific elements]
[Now analyse the given image. visible_text: upper 90% of frame only.]
[Output only JSON (scene last):]
```

The example shows two observations for most fields, making it clear that single-entry
arrays are the minimum, not the norm. Element values in the example model the desired
precision level.

**Source:** [`_SCENE_PROMPT`](street_plm_job.py)

### Scene ordering — list fields first

`scene` is placed **last** in the JSON output. Early versions put `scene` first;
the model spent ~60 tokens generating a long scene description before any list field,
exhausting the model's image attention before lighting/greenery were reached. Moving
scene to the end reserves the full attention budget for the zone-attribute lists.

### Deep JSON priming

```python
_PRIME = '{"lighting":[{'
text += _PRIME   # appended to chat-template output before tokenisation
```

The prime is appended to the *input*, not generated. It achieves two things:

1. The model cannot produce any preamble, code fences, or explanation — its first
   generated token is always inside the first lighting observation object.
2. The first field (`lighting`) is guaranteed to be populated, since the model inherits
   an open `{` and must close it with actual observation content before `]` is valid.

The decoded output is prepended with `_PRIME` to reconstruct the full object.

**Source:** [`_infer_scene`](street_plm_job.py)

### Generation sampling

```python
gen_kwargs = dict(
    max_new_tokens     = MAX_NEW_TOKENS,   # 640
    do_sample          = True,
    temperature        = 0.4,              # enough variance to choose { over ] on uncertain images
    top_p              = 0.9,              # nucleus sampling — excludes low-quality long-tail tokens
    repetition_penalty = 1.05,
    eos_token_id       = _processor.tokenizer.eos_token_id,
    pad_token_id       = _processor.tokenizer.pad_token_id,
)
```

`temperature=0.4` is the balance point: at `temperature=0.1` (near-greedy), `]` has
slightly higher probability than `{` for images the model is uncertain about, causing
`[]` collapse across all arrays. `temperature=0.4` with nucleus sampling gives `{`
enough probability mass to win ~70% of the time per attempt. Retry logic covers the
remaining ~30%.

**Source:** [`_infer_scene`](street_plm_job.py)

### Format cue in instructions

```
"Fill EVERY list field with 1-2 real observations — arrays MUST start with [{ not []."
```

The phrase `[{ not []` makes the constraint syntactically explicit: at the token
level, `{` is named as the expected character after `[`, not `]`. This biases the
model's token probability distribution before the retry safety net takes over.

### Echo detection

`_ECHO_SENTINELS` is a frozenset of every pipe-separated enum string that appears in
the prompt (e.g. `"dark|dim|adequate|bright"`). If a parsed value matches any
sentinel, the model has echoed the template rather than filling it. The result is
discarded and the default empty schema is returned.

**Source:** [`_ECHO_SENTINELS`](street_plm_job.py)

---

## Retry-Based Reliability

### The core problem

`temperature=0.4` sampling is probabilistic: any single inference attempt fills all
5 fields roughly 50–70% of the time. `Output_1.json` (in `Tests/Prompt consistency/5 Fields/`)
confirms the approach works when successful. The retry mechanism raises effective
per-image success rate to >90% without resorting to grammar-constrained decoding
(which proved incompatible with Qwen2.5-VL's multimodal tokenizer).

### Why grammar constraints were abandoned

[lm-format-enforcer](https://github.com/noamgat/lm-format-enforcer) integrates via
`prefix_allowed_tokens_fn` and enforces a JSON schema state machine during generation.
When applied to Qwen2.5-VL, visual tokens in the input confused the library's state
machine, causing it to block **all** valid tokens at certain steps — the model output
`approx_tokens: 3` and `scene: "unknown"`. The root cause is that the library
introspects the raw token sequence to advance its state machine, and cannot distinguish
visual embedding tokens from text tokens. It was removed from the pipeline entirely.

### Retry loop

```python
_MAX_ATTEMPTS = 4
t0 = time.perf_counter()
result, raw = None, ""
for attempt in range(1, _MAX_ATTEMPTS + 1):
    result, raw = _infer_scene(img)
    populated = sum(
        1 for k, v in result.items()
        if v not in ("unknown", "", None, {}, []) and not k.startswith("_")
    )
    if populated >= 3 or attempt == _MAX_ATTEMPTS:
        break
    log.warning("  Attempt %d/%d: only %d fields populated — retrying",
                attempt, _MAX_ATTEMPTS, populated)
latency_ms = (time.perf_counter() - t0) * 1000
```

Key design decisions:
- **Threshold `populated >= 3`** — requires at least 3 of 7 total fields (5 zone-attr + scene + visible_text) to have non-empty values. A result with 1–2 fields is considered incomplete; a result with ≥3 is acceptable.
- **4 attempts maximum** — at ~70% single-attempt success, the probability of all 4 failing is ~0.3⁴ ≈ 0.8%.
- **Cumulative latency** — `t0` is set before the loop, so `latency_ms` in the output record covers all attempts combined.
- **Final attempt always accepted** — even if populated < 3 on attempt 4, whatever was produced is written rather than discarding the record.

**Token cost of retries:** Failed attempts are short (~100–170 tokens). The extra attempts only fire when the first one was sparse, so worst-case cost per image is ~3 × 150 + 1 × 160 = ~610 additional tokens. In the common case (success on attempt 1–2), there is no token cost beyond the single inference.

**Source:** [`analyze_image`](street_plm_job.py)

---

## JSON Parsing Pipeline

Even with the retry mechanism, the raw model output may be truncated at
`MAX_NEW_TOKENS` or contain trailing junk. The parser applies four strategies in order:

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
Returns the **first syntactically balanced** `{...}` substring. Immune to
repeated `}}}` padding that greedy regex (`\{[\s\S]*\}`) mistakes for the object
boundary — greedy matching extends to the *last* `}`, which may be junk.

**Source:** [`_extract_first_json_obj`](street_plm_job.py)

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

**Source:** [`_parse_scene_json`](street_plm_job.py)

---

## Robustness Features

### Outdoor-only Street View

Both the metadata availability check and the image download include `source=outdoor`:

```python
# sv_available()
r = requests.get(_META_BASE, params={
    "location": f"{lat},{lon}",
    "radius"  : SV_RADIUS,
    "source"  : "outdoor",
    "key"     : STREETVIEW_API_KEY,
})

# fetch_sv()
r = requests.get(_SV_BASE, params={
    ...
    "source"  : "outdoor",
    "key"     : STREETVIEW_API_KEY,
})
```

Without `source=outdoor`, Google may return user-contributed Photo Spheres
(rooftops, building interiors, private courtyards) or indoor business panoramas
if they are the nearest panorama within `SV_RADIUS=50 m`. Official Google fleet
imagery only makes the dataset consistent for urban perception analysis.
Points with no outdoor coverage are recorded as `"status": "no_streetview"` stubs.

**Source:** [`sv_available`](street_plm_job.py) · [`fetch_sv`](street_plm_job.py)

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
consuming GPU time or retry attempts.

**Source:** [`_is_blank_image`](street_plm_job.py) · [`analyze_image`](street_plm_job.py)

### OCR noise filtering

`_filter_ocr_noise` removes three categories of spurious `visible_text` entries:

| Pattern | Regex | Example |
|---|---|---|
| Google watermark | `^(©\s*)?(20\d{2}\s+)?google(\s+maps)?` | `© 2024 Google Maps` |
| EU license plates | `^[A-Z]{1,4}[\s·-]?\d{2,4}...` | `VJL 360`, `1234 ABC` |
| Echoed enum string | `"\|" in type` | `sign\|label\|number\|other` |

The OCR instruction scans only the **upper 90% of the frame** (`visible_text: upper 90% of frame only` in the prompt) to avoid watermarks and vehicle plates in the bottom strip before they reach the filter.

**Source:** [`_GOOGLE_WATERMARK_RE`](street_plm_job.py) · [`_LICENSE_PLATE_RE`](street_plm_job.py) · [`_filter_ocr_noise`](street_plm_job.py)

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

**Source:** [`_grid_sample_bbox`](street_plm_job.py)

### Resume safety

The pipeline checks for an existing `*_analysis.json` before processing each point:

```python
pending = [pt for pt in sample_points
           if not _result_path(results_dir, pt["id"]).exists()]
```

Interrupted jobs resume from where they left off without re-fetching images or
re-running inference on completed points.

**Source:** [`run_pipeline`](street_plm_job.py)

---

## Repeatability Evidence

A run on `sv_41.396468_2.166667_h90.jpg` with the 5-field schema produced a fully
populated result across all categories:

| Field | Observations | Example element |
|---|---|---|
| `lighting` | 2 | `"natural daylight"` (adequate / bright) |
| `spatial_character` | 3 | width/enclosure/passability/lane_type/crossing per zone |
| `crowdedness` | 3 | `density_level: sparse` for left/center/right |
| `greenery` | 2 | `"plane trees"`, `"potted plants"` |
| `street_amenities` | 3 | `"street lamp"`, `"waste bin"` (many / few) |
| `visible_text` | 2 | `"santafé"` (store sign), `"Bar Calvet"` (store sign) |
| `scene` | — | `"Narrow street with historic buildings..."` |

`approx_tokens: 162`, `latency_ms: ~26 000` on L4.

**Source:** `Tests/Prompt consistency/5 Fields/Output_1.json`

### Why output is stable across successful attempts

Three mechanisms work together:

1. **Deep prime** (`{"lighting":[{`) forces the model into the first observation
   object immediately — the structure of the first field is invariant.

2. **Scene-last ordering** reserves the model's image attention for list fields.
   Scene is generated last when all zone observations are already committed.

3. **Pydantic normalisation** (`_coerce_to_list`, `_KEY_ALIASES`) smooths over
   surface variation in the raw model output — a bare dict becomes a one-item list,
   `"street_furniture"` maps to `"street_amenities"`, etc.

---

## Output Schema Reference

Each completed location writes a single `{point_id}_analysis.json`:

```jsonc
{
  "metadata": {
    "timestamp"         : "20260523_152453",
    "latitude"          : 41.396468,
    "longitude"         : 2.166667,
    "heading"           : 90.0,
    "street_name"       : "",           // from BigQuery / empty for grid fallback
    "highway_type"      : "unknown",
    "edge_id"           : "grid",
    "dist_along_edge_m" : null,
    "source_image"      : "sv_41.396468_2.166667_h90.jpg",
    "model"             : "Qwen/Qwen2.5-VL-3B-Instruct",
    "device"            : "cuda",
    "latency_ms"        : 25968.0,
    "status"            : "ok"          // "ok" | "no_streetview"
  },
  "scene_analysis": {
    "lighting": [
      {"zone": "center", "element": "direct overhead sunlight", "condition": "adequate"},
      {"zone": "right",  "element": "natural daylight on facade", "condition": "bright"}
    ],
    "spatial_character": [
      {"zone": "left",   "width": "moderate", "enclosure": "semi",     "passability": "clear", "lane_type": "sidewalk", "crossing": "yes"},
      {"zone": "center", "width": "narrow",   "enclosure": "enclosed", "passability": "clear", "lane_type": "road",     "crossing": "yes"}
    ],
    "crowdedness": [
      {"zone": "left",   "density_level": "sparse"},
      {"zone": "center", "density_level": "sparse"}
    ],
    "greenery": [
      {"zone": "left",  "element": "mature plane trees with mottled grey-green bark", "coverage": "moderate"},
      {"zone": "right", "element": "terracotta potted geraniums on windowsills",      "coverage": "sparse"}
    ],
    "street_amenities": [
      {"zone": "left",   "element": "cast-iron double-arm street lamp",  "presence": "few"},
      {"zone": "center", "element": "grey cylindrical municipal waste bin", "presence": "many"}
    ],
    "visible_text": [
      {"text": "santafé",   "zone": "left",  "type": "store sign"},
      {"text": "Bar Calvet","zone": "right", "type": "store sign"}
    ],
    "scene": "Narrow street with historic buildings, potted plants, and several waste bins lining the sidewalk."
  },
  "nearby_landmarks": [
    {"name": "Bar Calvet", "category": "bar", "distance_m": 42.1, "bearing": "NE"}
  ]
}
```

Points with no outdoor Street View coverage write a stub with `"status": "no_streetview"`
and `"scene_analysis": null`. Blank/placeholder images write `"_blank": true`
alongside a default empty analysis.
