"""
PLM Analyzer — Use Meta's PerceptionLM-1B to describe street view grid cells
with architecturally-relevant urban characteristics.
"""

import json
import re
import time
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoProcessor

MODEL_PATH = "facebook/Perception-LM-1B"
LOCAL_CACHE = Path(
    r"C:\Users\Lenovo\.cache\huggingface\hub\models--facebook--Perception-LM-1B"
)

URBAN_PROMPT = """\
You are an urban design expert walking through Barcelona. Analyse this section of a street view image.
Respond ONLY with valid JSON (no markdown, no extra text):
{
  "building_typology": "type of buildings visible (e.g., residential block, mixed-use, historic facade, modern tower, none)",
  "architectural_style": "dominant architectural style (e.g., Modernisme, Neo-classical, Contemporary, Industrial, N/A)",
  "materials": ["primary building/surface materials visible"],
  "condition": "overall condition (excellent / good / fair / poor / N/A)",
  "street_furniture": ["benches, bollards, streetlights, bins, bus stops, etc."],
  "vegetation": "description of greenery (mature trees, potted plants, grass, none)",
  "signage": ["visible signs, shop names, traffic signs"],
  "pedestrian_activity": "level and type of pedestrian presence (crowded, moderate, sparse, none)",
  "dominant_colors": ["2-3 dominant colors in this section"],
  "spatial_impression": "sense of space (narrow canyon, wide boulevard, open plaza, enclosed courtyard)",
  "narrative": "A 1-2 sentence first-person walking narrative of what you see here, as if you are a person strolling through the neighbourhood"
}"""


class PLMAnalyzer:
    """Wraps PerceptionLM-1B for urban scene analysis."""

    def __init__(self, model_path: str = MODEL_PATH, device: str | None = None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32

        print(f"[PLM] Loading model on {self.device} ({dtype}) …")
        t0 = time.perf_counter()

        self.processor = AutoProcessor.from_pretrained(model_path, use_fast=True)
        self.model = AutoModelForImageTextToText.from_pretrained(
            model_path, torch_dtype=dtype
        ).to(self.device)
        self.model.eval()

        elapsed = time.perf_counter() - t0
        print(f"[PLM] Model loaded in {elapsed:.1f}s")

    # ------------------------------------------------------------------

    def analyze_cell(self, image_path: str | Path, position: str = "") -> dict:
        """Run PLM on a single grid-cell image and return structured JSON."""
        image_path = str(Path(image_path).resolve())

        conversation = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "url": image_path},
                    {"type": "text", "text": URBAN_PROMPT},
                ],
            }
        ]

        inputs = self.processor.apply_chat_template(
            [conversation],
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        t0 = time.perf_counter()
        with torch.no_grad():
            gen_ids = self.model.generate(**inputs, max_new_tokens=512)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        input_len = inputs["input_ids"].shape[1]
        output_ids = gen_ids[:, input_len:]
        raw_text = self.processor.batch_decode(output_ids, skip_special_tokens=True)[0]

        result = self._parse_json(raw_text)
        result["position"] = position
        result["_latency_ms"] = round(elapsed_ms, 1)
        result["_raw"] = raw_text
        return result

    def analyze_grid(self, cells: dict[str, Path]) -> list[dict]:
        """Analyze all 9 grid cells and return a list of result dicts."""
        results = []
        total = len(cells)
        for i, (position, cell_path) in enumerate(cells.items(), 1):
            print(f"[PLM] Analyzing cell {i}/{total}: {position} …")
            res = self.analyze_cell(cell_path, position)
            results.append(res)
        return results

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Best-effort extraction of JSON from PLM output."""
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting first JSON block
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # Fallback: return raw text as narrative
        return {
            "building_typology": "unknown",
            "architectural_style": "unknown",
            "materials": [],
            "condition": "unknown",
            "street_furniture": [],
            "vegetation": "unknown",
            "signage": [],
            "pedestrian_activity": "unknown",
            "dominant_colors": [],
            "spatial_impression": "unknown",
            "narrative": text.strip()[:500],
            "_parse_error": True,
        }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python plm_analyzer.py <image_path>")
        sys.exit(1)

    analyzer = PLMAnalyzer()
    result = analyzer.analyze_cell(sys.argv[1], position="test")
    print(json.dumps(result, indent=2, ensure_ascii=False))
