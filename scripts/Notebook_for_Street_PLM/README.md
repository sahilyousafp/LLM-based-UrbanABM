# StreetPLM — Street Perception Language Model Pipeline

VLM-based street-perception pipeline for the Barcelona Eixample study area.
Fetches Google Street View images (official outdoor imagery only), runs
**Qwen2.5-VL-7B-Instruct** (4-bit NF4 quantized) on each, and produces a
structured JSON record describing **6 perceptual categories** per location —
grounded to five horizontal image zones — alongside nearby landmark data.

Designed to run as a [Lightning AI](https://lightning.ai) job on a **T4 (16 GB)
or L4 (24 GB) GPU**. The 7B model is loaded with 4-bit NF4 quantization via
`bitsandbytes`, reducing peak VRAM to ~4.5 GB so it fits comfortably on a T4.
Results feed directly into the LLM-based Urban ABM as environmental perception
data for agent decision-making.

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
GCP_PROJECT_ID=...              # GCP project for BigQuery (optional — OSMnx fallback used if absent)
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

### Use the 3B model (faster, lower VRAM, less detail)

```bash
python street_plm_job.py --model-size 3b
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

### 6 perceptual categories

The schema covers 6 categories plus a free-text scene summary. The categories were
selected to maximally cover pedestrian comfort signals while staying within the
model's effective output budget — each adds actionable information for agent
decision-making that cannot be derived from the others.

| Category | Captures | Key fields |
|---|---|---|
| `lighting` | Natural / artificial light quality per zone | `element` (specific source description), `condition` (dark/dim/adequate/bright) |
| `spatial_character` | Geometry of the walkable envelope | `width`, `enclosure`, `passability`, `lane_type`, `crossing` |
| `crowdedness` | Pedestrian density | `density_level` (empty/sparse/moderate/dense) |
| `greenery` | Vegetation type and coverage | `element` (species + visual description), `coverage` (none/sparse/moderate/dense) |
| `street_amenities` | All fixed street objects: seating, lamps, bins, bollards, fountains, hydrants, bike racks, info boards, bus shelters, kiosks, advertising panels | `element` (type + material + colour), `presence` (none/few/several/many) |
| `visible_text` | Legible signs and labels (upper 90% of frame) | `text` (exact string), `zone`, `type` (sign/label/graffiti) |

**Why `spatial_character` has no `element` field:** Early iterations included a
free-text `element` field in this category. The model consistently filled it with
greenery descriptions ("plane trees", "hedges"), bleeding vegetation data into a
geometry-only category. Removing `element` from `spatial_character` cleanly
separates walkability geometry from vegetation perception.

### Multiple entries per category

The schema explicitly supports — and encourages — multiple list entries per
category, including multiple entries **within the same zone**:

- **Cross-zone entries:** one observation per visible zone (e.g. left sidewalk
  vs centre road vs right façade)
- **Same-zone entries:** multiple distinct objects in the same zone appear as
  separate list items (e.g. a lamp post and a bench both on the left)

The prompt enforces this with: `"Multiple distinct elements in the same zone = multiple list entries"`,
and the few-shot example demonstrates it directly in `street_amenities` (two entries
both tagged `"zone":"left"`).

### Element precision

Every `element` value must describe the specific visual appearance of the object —
material, style, colour, or scale — not its generic type. The prompt enforces this:

```
element fields: name material, colour, style, scale
(e.g. 'grey cast-iron double-arm lamp post',
      'mature London plane trees with mottled grey-green bark',
      'dark green metal municipal waste bin with foot-pedal lid')
```

This yields richer data for downstream agent comfort scoring without increasing
token count, because specificity is injected into the `element` string value rather
than adding new fields.

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
instead of a list — a common failure mode on nested schemas that the 7B model
occasionally exhibits for single-observation fields.

A `_KEY_ALIASES` dictionary maps 40+ model-hallucinated key names
(`"openness"`, `"vegetation"`, `"visual_clarity"`, `"street_furniture"`, etc.) to
canonical field names, so the schema tolerates natural model paraphrasing without
requiring exact key matches.

---

## Token Budget Analysis

### Model and quantization

**Default model:** `Qwen/Qwen2.5-VL-7B-Instruct` with 4-bit NF4 quantization
(`BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True)`).

| Aspect | Value |
|---|---|
| Full model size | ~14 GB (bf16) |
| Quantized size | ~4.5 GB (4-bit NF4) |
| Compute dtype | float16 on T4 (sm75), bfloat16 on Ampere+ (sm80+) |
| Fits on T4 (16 GB)? | Yes — ~4.5 GB weights + ~3 GB activations leaves headroom |

The `--model-size 3b` flag switches to `Qwen2.5-VL-3B-Instruct` in full precision
(bfloat16, ~6 GB) for faster iteration or when VRAM is very constrained.

### Vision tokens

Qwen2.5-VL uses 28×28 pixel patches. Resolution and vision token budget scale with
available VRAM, detected automatically at load time:

| GPU VRAM | Compute dtype | MAX_PIXELS | Vision tokens | Notes |
|---|---|---|---|---|
| ≥ 23 GB (L4, A100) | bf16 | 1 280 × 28² ≈ 1 M | ~1280 | Full quality |
| ≥ 15 GB (T4) | fp16 | 784 × 28² ≈ 615 K | ~784 | T4 safe — ~15.84 GB reported |
| < 15 GB | fp16 | 512 × 28² ≈ 401 K | ~512 | Minimum |
| CPU | fp32 | 256 × 28² ≈ 201 K | ~256 | Fallback |

The T4 threshold is `>= 15` (not `>= 16`) because a 16 GB T4 reports ~15.84 GB
usable after driver overhead.

**Source:** [`load_model`](street_plm_job.py)

### Generation tokens

```
MAX_NEW_TOKENS = 900
# Derivation: 6 categories × 2-3 observations × ~50 tokens/obs ≈ 750
#             + visible_text (0-3 entries × ~20 tok) + scene (~30 tok) ≈ 840
#             900 adds ~8% headroom for verbose same-zone element pairs
```

Per-observation token cost (approximate BPE tokens) with specific elements:

```
{"zone":"left","element":"ornate cast-iron double-arm street lamp with frosted globe","presence":"few"}
= ~55–65 tokens   (longer than generic "street lamp" by ~15–20 tokens)
```

With 6 categories × 2.5 average observations × ~50 tokens = **~750 tokens minimum**.
`MAX_NEW_TOKENS = 900` absorbs verbose element strings, same-zone pairs, and
multi-entry `visible_text` without over-generating.

### Prompt token cost

The few-shot prompt (`_SCENE_PROMPT`) costs approximately **420–480 tokens** at
inference — the instructions (~140 tokens, including zone definitions, per-field
schemas, and element precision rules) plus the filled example (~310 tokens with
specific element descriptions and the same-zone street_amenities pair).
This is intentionally front-loaded: a concrete example with specific element values
and demonstrated same-zone behaviour is worth more than additional generation budget.

**Source:** [`MAX_NEW_TOKENS`](street_plm_job.py) · [`_SCENE_PROMPT`](street_plm_job.py)

### Total context at inference (T4, 784 vision tokens)

| Component | Tokens |
|---|---|
| System / chat template | ~50 |
| Vision tokens (640×640 image on T4) | ~784 |
| Instruction + few-shot prompt | ~450 |
| Deep prime (`{"lighting":[{`) | ~6 |
| **Input total** | **~1290** |
| Generation budget | 900 |
| **Peak context** | **~2190** |

Well within Qwen2.5-VL-7B's 32 768 token context window.

---

## Prompt Engineering

### Why few-shot over template-only

Early versions used a compact schema template with placeholder strings
(`"<zone>"`, `"dark|dim|adequate|bright"`). The model consistently echoed
these placeholder strings verbatim rather than filling them with image observations.
A filled example of a *different* image gives the model a concrete output to imitate,
which is far more reliable than instruction-following alone.

### Prompt structure

```
[Urban analyst role] + [Zone definitions (far_left|left|center|right|far_right)]
[7-field output instruction with per-field sub-schemas]
[Rules: coverage, same-zone multiple entries, element precision, visible_text scope]
[Filled example — DIFFERENT image — 2-4 entries per field, specific elements,
 same-zone pair in street_amenities to demonstrate intra-zone multiple entries]
[Now analyse THIS image. Output only JSON:]
```

The example shows entries for the same zone in `street_amenities` (both `"zone":"left"`),
making it unambiguous that a zone can appear multiple times in a list. Element values
in the example model the desired precision level (material, colour, style).

**Source:** [`_SCENE_PROMPT`](street_plm_job.py)

### Scene ordering — list fields first

`scene` is placed **last** in the JSON output. Early versions put `scene` first;
the model spent ~60 tokens generating a long scene description before any list field,
exhausting image attention before lighting/greenery were reached. Moving `scene`
to the end reserves the full attention budget for the zone-attribute lists.

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
    max_new_tokens     = 900,
    do_sample          = True,
    temperature        = 0.3,    # 7B handles sampling well; adds description variety
    top_p              = 0.95,   # nucleus sampling — excludes low-quality long-tail tokens
    repetition_penalty = 1.1,    # prevents looping on repetitive street descriptions
    eos_token_id       = _processor.tokenizer.eos_token_id,
    pad_token_id       = _processor.tokenizer.pad_token_id,
)
# Final retry attempt uses greedy decoding (do_sample=False)
```

`temperature=0.3` is lower than earlier 3B configurations (which used 0.4) because
the 7B model is more instruction-following: it rarely collapses arrays to `[]` at
low temperatures. The lower temperature produces more consistent element
descriptions across attempts while still varying phrasing.

`repetition_penalty=1.1` prevents the model from repeating the same zone/element
pair when it runs out of new observations to report.

**Source:** [`_infer_scene`](street_plm_job.py)

### Echo detection

`_ECHO_SENTINELS` is a frozenset of every pipe-separated enum string that appears in
the prompt (e.g. `"dark|dim|adequate|bright"`). If a parsed value matches any
sentinel, the model has echoed the template rather than filling it. The result is
discarded and retried.

**Source:** [`_ECHO_SENTINELS`](street_plm_job.py)

---

## Retry-Based Reliability

### The core problem

Temperature sampling is probabilistic: any single inference attempt may fill fewer
than the required fields when the model is uncertain about a low-contrast or
ambiguous image. The retry mechanism raises the effective per-image success rate to
>95% without grammar-constrained decoding (which proved incompatible with
Qwen2.5-VL's multimodal tokenizer).

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
    greedy = (attempt == _MAX_ATTEMPTS)   # last attempt: greedy decoding
    result, raw = _infer_scene(img, greedy=greedy)
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

- **Threshold `populated >= 3`** — requires at least 3 of 7 total fields (6 zone-attr + scene) to have non-empty values. A result with 1–2 fields is considered incomplete.
- **4 attempts maximum** — at the 7B model's empirical success rate (~80–90% per attempt), the probability of all 4 failing is <0.2%.
- **Greedy final attempt** — the last attempt uses `do_sample=False` (greedy decoding), which maximises the probability of any output appearing rather than the model stochastically collapsing to `[]`. This biases the final fallback toward producing something rather than nothing.
- **Cumulative latency** — `t0` is set before the loop, so `latency_ms` in the output record covers all attempts combined.
- **Final attempt always accepted** — even if `populated < 3` on attempt 4, whatever was produced is written rather than discarding the record.

**Source:** [`analyze_image`](street_plm_job.py)

---

## JSON Parsing Pipeline

Even with the retry mechanism, raw model output may be truncated at `MAX_NEW_TOKENS`
or contain trailing junk. The parser applies four strategies in order:

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
params = {"location": f"{lat},{lon}", "radius": SV_RADIUS, "source": "outdoor", "key": ...}
```

Without `source=outdoor`, Google may return user-contributed Photo Spheres
(rooftops, building interiors, private courtyards) or indoor business panoramas
if they are the nearest panorama within `SV_RADIUS=50 m`. Official Google fleet
imagery only makes the dataset consistent for urban perception analysis.
Points with no outdoor coverage are recorded as `"status": "no_streetview"` stubs.

**Source:** [`sv_available`](street_plm_job.py) · [`fetch_sv`](street_plm_job.py)

### Blank image detection

Google Street View serves grey placeholder frames (~5–50 KB) for areas with limited
panoramic coverage. These pass file-size checks but cause the VLM to output
near-nothing. Images are screened before inference:

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

The OCR instruction scans only the **upper 90% of the frame** to avoid watermarks
and vehicle plates in the bottom strip before they reach the filter.

**Source:** [`_filter_ocr_noise`](street_plm_job.py)

### Three-tier point sampling

Sample points are drawn from the pedestrian walk network via a three-tier fallback:

```
1. BigQuery (Overture Maps) — precise walk edges with street names + highway type
       ↓ (if GCP credentials unavailable or query fails)
2. OSMnx — live OpenStreetMap walk network, sampled every 200 m along edges
       ↓ (if OSM download fails or returns empty graph)
3. BBOX grid — uniform 50 m grid across study area (~160 points), headings 0° / 90°
```

OSMnx uses `graph_from_polygon(shapely.box(...))` which is stable across both
osmnx 1.x and 2.x (the `graph_from_bbox` API changed argument order between
versions). Walk edges shorter than 10 m are filtered out before sampling.

**Source:** [`_load_walk_edges_from_osmnx`](street_plm_job.py) · [`_grid_sample_bbox`](street_plm_job.py)

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

A batch run of 157 Eixample locations on a T4 GPU (Qwen2.5-VL-7B-Instruct, 4-bit NF4)
produced ~7/7 fields populated for the majority of images at ~26–28 s/location:

| Field | Typical observations | Example element (7B quality) |
|---|---|---|
| `lighting` | 2 | `"dappled shade from mature plane tree canopy"` (dim) |
| `spatial_character` | 2–3 | width/enclosure/passability/lane_type/crossing per zone |
| `crowdedness` | 2–3 | `density_level: sparse` for left/center/right |
| `greenery` | 1–2 | `"mature London plane trees with mottled grey-green bark"` |
| `street_amenities` | 2–4 | `"grey cast-iron double-arm lamp post"`, `"dark green metal waste bin with foot-pedal lid"` |
| `visible_text` | 0–2 | `"MERCAT DE L'EIXAMPLE"` (sign) |
| `scene` | — | `"Wide tree-lined boulevard with parked bicycles and sparse pedestrian activity."` |

`latency_ms: ~26 000–28 000` per location on T4 (including Street View fetch and
all retry attempts). Estimated full batch time: ~70 minutes.

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
    "timestamp"         : "20260523_224425",
    "latitude"          : 41.399276,
    "longitude"         : 2.167502,
    "heading"           : 225.2,
    "street_name"       : "Carrer de Provença",
    "highway_type"      : "residential",
    "edge_id"           : "30243206_3207273339_0",
    "dist_along_edge_m" : 0.0,
    "source_image"      : "sv_41.399276_2.167502_h225.jpg",
    "model"             : "Qwen/Qwen2.5-VL-7B-Instruct",
    "device"            : "cuda",
    "latency_ms"        : 27616.0,
    "status"            : "ok"          // "ok" | "no_streetview"
  },
  "scene_analysis": {
    "lighting": [
      {"zone": "left",   "element": "dappled shade from mature plane tree canopy", "condition": "dim"},
      {"zone": "center", "element": "direct overhead overcast daylight",           "condition": "adequate"}
    ],
    "spatial_character": [
      {"zone": "left",   "width": "narrow",   "enclosure": "enclosed", "passability": "clear", "lane_type": "sidewalk", "crossing": "none"},
      {"zone": "center", "width": "moderate", "enclosure": "semi",     "passability": "clear", "lane_type": "road",     "crossing": "zebra"}
    ],
    "crowdedness": [
      {"zone": "left",   "density_level": "sparse"},
      {"zone": "center", "density_level": "sparse"}
    ],
    "greenery": [
      {"zone": "left",  "element": "mature London plane trees with mottled grey-green bark", "coverage": "dense"},
      {"zone": "right", "element": "terracotta potted geraniums on window ledges",           "coverage": "sparse"}
    ],
    "street_amenities": [
      {"zone": "left",   "element": "grey cast-iron double-arm lamp post with frosted globe",   "presence": "few"},
      {"zone": "left",   "element": "dark grey granite kerb-side bench with metal armrests",    "presence": "few"},
      {"zone": "center", "element": "yellow painted concrete bollard",                          "presence": "several"},
      {"zone": "right",  "element": "dark green metal municipal waste bin with foot-pedal lid", "presence": "few"}
    ],
    "visible_text": [
      {"text": "FARMÀCIA", "zone": "right", "type": "sign"}
    ],
    "scene": "Narrow residential street lined with mature plane trees, parked cars, and sparse pedestrian activity under an overcast sky."
  },
  "nearby_landmarks": [
    {"name": "Farmàcia Provença", "category": "pharmacy", "distance_m": 18.3, "bearing": "SE"}
  ]
}
```

Points with no outdoor Street View coverage write a stub with `"status": "no_streetview"`
and `"scene_analysis": null`. Blank/placeholder images write `"_blank": true`
alongside a default empty analysis.
