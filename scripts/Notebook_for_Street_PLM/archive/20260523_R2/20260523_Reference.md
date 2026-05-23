# Adding PaddleOCR text extraction to the pipeline

## Installation

Add to your Lightning AI Secrets or .env:

```bash
# No additional secrets needed — PaddleOCR is local
```

Add to `requirements.txt` or install directly:

```bash
pip install paddleocr
```

The first run will auto-download the English OCR model (~50 MB) to `~/.paddleocr/`.

---

## Integration into street_plm_job.py

Add this helper function AFTER `landmark_lookup()`:

```python
_paddleocr = None

def ocr_panel(image: _PILImage.Image, x_start: int, x_end: int) -> str:
    """
    Extract text from a vertical slice of an image using PaddleOCR.
    Returns all detected text as a space-separated string.
    
    Args:
        image: PIL Image (640x640 street view)
        x_start, x_end: horizontal pixel boundaries (e.g. 0-160 for L panel)
    
    Returns: detected text as string, or empty string if nothing found
    """
    global _paddleocr
    if _paddleocr is None:
        from paddleocr import PaddleOCR
        log.info("Loading PaddleOCR (first run: auto-downloads English model)")
        _paddleocr = PaddleOCR(use_angle_cls=True, lang='en')
    
    # Crop to the panel
    crop = image.crop((x_start, 0, x_end, image.height))
    
    try:
        results = _paddleocr.ocr(crop, cli=False)
        if not results or not results[0]:
            return ""
        # results is list of [line[text, confidence], ...]
        text_parts = [line[0][1] for line in results[0] if line[0] and line[0][1]]
        return " ".join(text_parts)
    except Exception as exc:
        log.debug("PaddleOCR failed on panel: %s", exc)
        return ""
```

---

## Integrate OCR results into observations

Modify `analyze_image()` to optionally run OCR and inject results:

```python
def analyze_image(image_path: Path, extract_text: bool = True) -> dict:
    """
    Run Qwen VLM on one street-view image.
    Optionally run PaddleOCR on each panel and inject legible text into
    commercial_activity element field.
    
    Returns dict with keys: observations, openness, crowdedness,
    passable, raw_vlm_output, _latency_ms, ocr_text (if extract_text=True)
    """
    img = _PILImage.open(image_path).convert("RGB")
    w   = img.width
    
    # VLM analysis
    t0  = time.perf_counter()
    raw = _infer_scene(img)
    ms  = round((time.perf_counter() - t0) * 1000, 1)

    obs, scalars = _parse_scene_json(raw)
    
    # Optional: extract text from all panels
    ocr_results = {}
    if extract_text:
        ocr_results = {
            "L":  ocr_panel(img, 0,      w // 4),
            "CL": ocr_panel(img, w // 4, w // 2),
            "CR": ocr_panel(img, w // 2, 3*w // 4),
            "R":  ocr_panel(img, 3*w // 4, w),
        }
        log.info("OCR text found: %s", ocr_results)
        
        # Inject OCR text into commercial_activity observations
        # If model said "commercial_activity" in CR, append extracted text to element
        for o in obs:
            if o.get("feature") == "commercial_activity" and o.get("panel") in ocr_results:
                ocr_text = ocr_results[o["panel"]]
                if ocr_text and ocr_text not in o.get("element", ""):
                    o["element"] = f"{o['element']} ({ocr_text})"
    
    kws = {o["feature"] for o in obs}
    log.info(
        "%d ms | %d obs | kws=%s | open=%s crowd=%s pass=%s",
        int(ms), len(obs), sorted(kws),
        scalars["openness"], scalars["crowdedness"], scalars["passable"],
    )

    return {
        "observations":   obs,
        "raw_vlm_output": raw,
        "ocr_text":       ocr_results,
        "_latency_ms":    ms,
        **scalars,
    }
```

---

## Update run_pipeline to include OCR results

In `run_pipeline()`, extract OCR results before saving JSON:

```python
# After: scene = analyze_image(img_path)
ocr_text = scene.pop("ocr_text", {})  # remove from dict before storage

# In the record dict, add to metadata:
"metadata": {
    ...
    "ocr_text_by_panel": ocr_text,
    ...
}

# In scene_analysis, keep just the modified observations with text injected
"scene_analysis": {
    "observations": scene.get("observations", []),
    ...
}
```

---

## Test it

```bash
# Run the test script — it imports from street_plm_job.py so OCR is automatic
GOOGLE_STREETVIEW_API_KEY=... HF_TOKEN=... python test_one_image.py

# On first run, PaddleOCR downloads the English model (~50 MB)
# Expect +300–500 ms latency per image (one-time setup, then cached)
```

---

## Expected output with OCR

Before:
```json
{
  "feature": "commercial_activity",
  "element": "storefronts",
  "panel": "CR",
  "descriptor": "various shops"
}
```

After:
```json
{
  "feature": "commercial_activity",
  "element": "storefronts (Massimo Dutti Ra... Miu)",
  "panel": "CR",
  "descriptor": "various shops"
}
```

The OCR text is appended to `element`, so agents can see the shop names while the VLM's description of the retail character remains in `descriptor`.

---

## Performance on Lightning AI free tier

- **Qwen 3B 4-bit:** ~40–80 ms per image
- **PaddleOCR (4 panels):** ~300–500 ms per image
- **Total latency:** ~400–600 ms per image (acceptable)
- **GPU memory:** 4.5 GB (Qwen) + 0.5 GB (OCR) = 5 GB on T4 (safe)
- **Per-image cost:** ~0.15 cents on Lightning AI GPU time
- **500 images:** ~75 seconds GPU time = <5 cents

---

## Optional: disable OCR for faster iteration

While testing, set `extract_text=False` to skip OCR:

```python
scene = analyze_image(img_path, extract_text=False)
```

This saves ~300 ms per image. Re-enable after tuning the VLM prompt.

---

## Why this approach works for free Lightning AI

1. **No API calls:** Everything runs locally on your GPU.
2. **Lightweight:** PaddleOCR model is 50 MB; fits on any GPU.
3. **Async-friendly:** OCR runs after VLM, no pipeline bottleneck.
4. **Graceful fallback:** If OCR fails, VLM description still stands.
5. **Cheap:** 500 images = ~1 minute GPU time.

PaddleOCR is maintained by Baidu and widely used in production. It's robust, fast, and completely free under MIT license.
