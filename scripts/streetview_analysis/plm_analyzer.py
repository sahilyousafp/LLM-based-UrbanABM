"""
PLM Analyzer — Use Meta's PerceptionLM-1B to describe a full street view image
across all nine 3×3 quadrants in a single inference pass.
"""

import json
import math
import os
import re
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoTokenizer

MODEL_PATH = "facebook/Perception-LM-1B"
LOCAL_CACHE = Path(
    r"C:\Users\Lenovo\.cache\huggingface\hub\models--facebook--Perception-LM-1B"
)
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful language and vision assistant. You are able to understand the "
    "visual content that the user provides, and assist the user with a variety of tasks "
    "using natural language."
)

URBAN_PROMPT = """\
You are an urban design expert walking through Barcelona. You are looking at a full street-view photograph.

Mentally divide the image into a 3×3 grid of nine quadrants, laid out as follows:
  top_left    | top_center    | top_right
  middle_left | middle_center | middle_right
  bottom_left | bottom_center | bottom_right

For EACH of the nine quadrants, analyse the urban characteristics visible in that portion of the image.

Respond ONLY with valid JSON (no markdown, no extra text). The top-level keys must be exactly the nine quadrant names above, and the value for each key must follow this schema:
{
  "top_left": {
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
    "narrative": "A 1-2 sentence first-person walking narrative of what you see in this quadrant"
  },
  "top_center":    { <same schema> },
  "top_right":     { <same schema> },
  "middle_left":   { <same schema> },
  "middle_center": { <same schema> },
  "middle_right":  { <same schema> },
  "bottom_left":   { <same schema> },
  "bottom_center": { <same schema> },
  "bottom_right":  { <same schema> }
}"""


def _resolve_model_source(model_path: str) -> str | Path:
    """Prefer a fully cached local snapshot before falling back to the gated repo ID."""
    override = os.getenv("PERCEPTION_LM_PATH")
    if override:
        override_path = Path(override)
        return override_path if override_path.exists() else override

    ref_path = LOCAL_CACHE / "refs" / "main"
    if ref_path.exists():
        snapshot_id = ref_path.read_text(encoding="utf-8").strip()
        snapshot_path = LOCAL_CACHE / "snapshots" / snapshot_id
        if snapshot_path.exists():
            return snapshot_path

    snapshots_dir = LOCAL_CACHE / "snapshots"
    if snapshots_dir.exists():
        snapshots = sorted(path for path in snapshots_dir.iterdir() if path.is_dir())
        if snapshots:
            return snapshots[-1]

    return model_path


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _device_diagnostics(selected_device: str, auto_selected: bool) -> list[str]:
    cuda_build = torch.version.cuda or "None"
    cuda_available = torch.cuda.is_available()
    device_count = torch.cuda.device_count()
    diagnostics = [
        f"[PLM] Torch version: {torch.__version__}",
        f"[PLM] Torch CUDA build: {cuda_build}",
        f"[PLM] torch.cuda.is_available(): {cuda_available}",
        f"[PLM] Visible CUDA devices: {device_count}",
    ]

    if selected_device == "cuda" and cuda_available and device_count > 0:
        diagnostics.append(f"[PLM] Using GPU: {torch.cuda.get_device_name(0)}")
        return diagnostics

    if not auto_selected:
        diagnostics.append(
            f"[PLM] Using {selected_device.upper()} because it was requested explicitly."
        )
        return diagnostics

    if torch.version.cuda is None:
        reason = "the installed PyTorch build has no CUDA support"
    elif device_count == 0:
        reason = "no CUDA devices are visible to this process"
    else:
        reason = "CUDA is unavailable in the current runtime"

    diagnostics.append(f"[PLM] Using CPU because {reason}.")
    diagnostics.append(
        "[PLM] Install a CUDA-enabled PyTorch build and ensure your driver or container runtime exposes the GPU if you want GPU inference."
    )
    return diagnostics


class _PerceptionLMImagePreprocessor:
    """Local image preprocessor that avoids the torchvision-only HF fast processor."""

    def __init__(
        self,
        tile_size: int = 448,
        max_num_tiles: int = 36,
        image_mean: list[float] | None = None,
        image_std: list[float] | None = None,
        rescale_factor: float = 1.0 / 255.0,
    ):
        self.tile_size = tile_size
        self.max_num_tiles = max_num_tiles
        self.image_mean = image_mean or [0.5, 0.5, 0.5]
        self.image_std = image_std or [0.5, 0.5, 0.5]
        self.rescale_factor = rescale_factor

    @staticmethod
    def _factors(n: int) -> set[int]:
        values = set()
        for i in range(1, int(n**0.5) + 1):
            if n % i == 0:
                values.add(i)
                values.add(n // i)
        return values

    def _find_supported_aspect_ratios(self) -> dict[float, list[tuple[int, int]]]:
        aspect_ratios: dict[float, list[tuple[int, int]]] = {}
        for chunk_size in range(self.max_num_tiles, 0, -1):
            factors = sorted(self._factors(chunk_size))
            for ratio in ((x, chunk_size // x) for x in factors):
                aspect_ratios.setdefault(ratio[0] / ratio[1], []).append(ratio)
        return aspect_ratios

    @staticmethod
    def _get_image_height_width(
        image_width: int,
        image_height: int,
        target_width: int,
        target_height: int,
    ) -> tuple[float, float]:
        scale = image_width / image_height
        rescaling_factor = min(target_width / image_width, target_height / image_height)
        if scale > 1.0:
            new_width = rescaling_factor * image_width
            new_height = math.floor(new_width / scale)
        else:
            new_height = rescaling_factor * image_height
            new_width = math.floor(new_height * scale)
        return new_width, new_height

    def _fit_image_to_canvas(self, img_width: int, img_height: int) -> tuple[int, int] | None:
        optimal_canvas: tuple[int, int] | None = None
        optimal_image_size: tuple[float, float] | None = None
        scale = img_width / img_height
        potential_arrangements = [
            item
            for sublist in self._find_supported_aspect_ratios().values()
            for item in sublist
        ]

        for n_w, n_h in potential_arrangements:
            canvas_width = n_w * self.tile_size
            canvas_height = n_h * self.tile_size
            if canvas_width < img_width or canvas_height < img_height:
                continue

            image_size = self._get_image_height_width(
                img_width,
                img_height,
                canvas_width,
                canvas_height,
            )
            if optimal_canvas is None:
                optimal_canvas = (n_w, n_h)
                optimal_image_size = image_size
                continue

            if optimal_image_size is None:
                optimal_canvas = (n_w, n_h)
                optimal_image_size = image_size
                continue

            if (scale < 1.0 and image_size[0] >= optimal_image_size[0]) or (
                scale >= 1.0 and image_size[1] >= optimal_image_size[1]
            ):
                optimal_canvas = (n_w, n_h)
                optimal_image_size = image_size

        return optimal_canvas

    def _find_closest_aspect_ratio(self, img_width: int, img_height: int) -> tuple[int, int]:
        target_aspect_ratio = img_width / img_height
        aspect_ratios = self._find_supported_aspect_ratios()
        if target_aspect_ratio >= 1:
            closest = min(
                (ratio for ratio in aspect_ratios if ratio <= target_aspect_ratio),
                key=lambda ratio: abs(ratio - target_aspect_ratio),
            )
            return max(aspect_ratios[closest], key=lambda ratio: ratio[0])

        closest = min(
            (ratio for ratio in aspect_ratios if ratio > target_aspect_ratio),
            key=lambda ratio: abs(1 / ratio - 1 / target_aspect_ratio),
        )
        return max(aspect_ratios[closest], key=lambda ratio: ratio[1])

    @staticmethod
    def _split(image: torch.Tensor, tiles_w: int, tiles_h: int) -> torch.Tensor:
        batch_size, channels, height, width = image.size()
        image = image.view(batch_size, channels, tiles_h, height // tiles_h, tiles_w, width // tiles_w)
        image = image.permute(0, 2, 4, 1, 3, 5).contiguous()
        return image.view(batch_size, tiles_w * tiles_h, channels, height // tiles_h, width // tiles_w)

    @staticmethod
    def _as_image(image: str | Path | Image.Image) -> Image.Image:
        if isinstance(image, Image.Image):
            return image.convert("RGB")
        return Image.open(image).convert("RGB")

    @staticmethod
    def _to_tensor(image: Image.Image) -> torch.Tensor:
        array = np.asarray(image, dtype=np.float32)
        if array.ndim == 2:
            array = np.repeat(array[:, :, None], 3, axis=2)
        return torch.from_numpy(array).permute(2, 0, 1)

    def _resize(self, image: Image.Image, max_num_tiles: int) -> tuple[Image.Image, tuple[int, int]]:
        width, height = image.size
        if max_num_tiles > 1:
            aspect_ratio = self._fit_image_to_canvas(width, height)
            if aspect_ratio is None:
                aspect_ratio = self._find_closest_aspect_ratio(width, height)
        else:
            aspect_ratio = (1, 1)

        resized = image.resize(
            (aspect_ratio[0] * self.tile_size, aspect_ratio[1] * self.tile_size),
            Image.Resampling.BICUBIC,
        )
        return resized, aspect_ratio

    def preprocess(self, image: str | Path | Image.Image) -> torch.Tensor:
        image = self._as_image(image)
        thumbnail, _ = self._resize(image, max_num_tiles=1)
        tiled_image, (tiles_w, tiles_h) = self._resize(image, max_num_tiles=self.max_num_tiles)

        thumbnail_tensor = self._to_tensor(thumbnail).unsqueeze(0)
        tiled_tensor = self._to_tensor(tiled_image).unsqueeze(0)
        image_tiles = self._split(tiled_tensor, tiles_w, tiles_h)
        stacked = torch.cat([thumbnail_tensor.unsqueeze(1), image_tiles], dim=1).squeeze(0)

        stacked = stacked * self.rescale_factor
        image_mean = torch.tensor(self.image_mean, dtype=stacked.dtype).view(1, 3, 1, 1)
        image_std = torch.tensor(self.image_std, dtype=stacked.dtype).view(1, 3, 1, 1)
        return (stacked - image_mean) / image_std


class PLMAnalyzer:
    """Wraps PerceptionLM-1B for urban scene analysis."""

    def __init__(self, model_path: str = MODEL_PATH, device: str | None = None):
        auto_device = device is None
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        self.model_source = _resolve_model_source(model_path)
        local_files_only = isinstance(self.model_source, Path)

        processor_config = {"patch_size": 14, "pooling_ratio": 2}
        image_config = {
            "tile_size": 448,
            "max_num_tiles": 36,
            "image_mean": [0.5, 0.5, 0.5],
            "image_std": [0.5, 0.5, 0.5],
            "rescale_factor": 1.0 / 255.0,
        }
        if isinstance(self.model_source, Path):
            processor_config_path = self.model_source / "processor_config.json"
            preprocessor_config_path = self.model_source / "preprocessor_config.json"
            if processor_config_path.exists():
                processor_config.update(_read_json(processor_config_path))
            if preprocessor_config_path.exists():
                image_config.update(_read_json(preprocessor_config_path))

        self.patch_size = int(processor_config["patch_size"])
        self.pooling_ratio = int(processor_config["pooling_ratio"])
        self.image_preprocessor = _PerceptionLMImagePreprocessor(
            tile_size=int(image_config["tile_size"]),
            max_num_tiles=int(image_config["max_num_tiles"]),
            image_mean=list(image_config["image_mean"]),
            image_std=list(image_config["image_std"]),
            rescale_factor=float(image_config["rescale_factor"]),
        )

        for line in _device_diagnostics(self.device, auto_selected=auto_device):
            print(line)
        print(f"[PLM] Loading model from {self.model_source} on {self.device} ({self.dtype}) …")
        t0 = time.perf_counter()

        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_source,
            local_files_only=local_files_only,
        )
        self.model = AutoModelForImageTextToText.from_pretrained(
            self.model_source,
            local_files_only=local_files_only,
            torch_dtype=self.dtype,
        ).to(self.device)
        self.model.eval()
        self.image_token = getattr(self.tokenizer, "image_token", "<|image|>")

        elapsed = time.perf_counter() - t0
        print(f"[PLM] Model loaded in {elapsed:.1f}s")

    # ------------------------------------------------------------------

    def _format_prompt(self, prompt: str) -> str:
        bos_token = self.tokenizer.bos_token or "<|begin_of_text|>"
        return (
            f"{bos_token}<|start_header_id|>system<|end_header_id|>\n\n"
            f"{DEFAULT_SYSTEM_PROMPT}<|eot_id|>"
            f"<|start_header_id|>user<|end_header_id|>\n\n"
            f"{self.image_token}{prompt.strip()}<|eot_id|>"
            f"<|start_header_id|>assistant<|end_header_id|>\n\n"
        )

    def _build_inputs(self, image_path: str | Path, prompt: str) -> dict[str, torch.Tensor]:
        pixel_values = self.image_preprocessor.preprocess(image_path)
        num_tiles = pixel_values.shape[0]
        tokens_per_tile = (
            (pixel_values.shape[-2] // self.patch_size // self.pooling_ratio)
            * (pixel_values.shape[-1] // self.patch_size // self.pooling_ratio)
        )
        expanded_prompt = self._format_prompt(prompt).replace(
            self.image_token,
            self.image_token * (tokens_per_tile * num_tiles),
            1,
        )

        text_inputs = self.tokenizer(
            expanded_prompt,
            add_special_tokens=False,
            return_tensors="pt",
        )
        return {
            "input_ids": text_inputs["input_ids"].to(self.device),
            "attention_mask": text_inputs["attention_mask"].to(self.device),
            "pixel_values": pixel_values.unsqueeze(0).to(self.device, dtype=self.dtype),
        }

    def analyze_image(self, image_path: str | Path) -> dict:
        """Run PLM on the full image and return a dict keyed by quadrant name."""
        image_path = str(Path(image_path).resolve())
        inputs = self._build_inputs(image_path, URBAN_PROMPT)

        t0 = time.perf_counter()
        with torch.no_grad():
            gen_ids = self.model.generate(**inputs, max_new_tokens=2048)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        input_len = inputs["input_ids"].shape[1]
        output_ids = gen_ids[:, input_len:]
        raw_text = self.tokenizer.batch_decode(output_ids, skip_special_tokens=True)[0]

        result = self._parse_quadrant_json(raw_text)
        result["_latency_ms"] = round(elapsed_ms, 1)
        result["_raw"] = raw_text
        return result

    # ------------------------------------------------------------------

    @staticmethod
    def _empty_quadrant(narrative: str = "unknown") -> dict:
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
            "narrative": narrative,
        }

    _QUADRANT_NAMES = [
        "top_left", "top_center", "top_right",
        "middle_left", "middle_center", "middle_right",
        "bottom_left", "bottom_center", "bottom_right",
    ]

    def _parse_quadrant_json(self, text: str) -> dict:
        """Best-effort extraction of a 9-quadrant JSON dict from PLM output."""
        candidate: dict | None = None

        try:
            candidate = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    candidate = json.loads(match.group())
                except json.JSONDecodeError:
                    pass

        if candidate and all(k in candidate for k in self._QUADRANT_NAMES):
            return candidate

        # Fallback: wrap whatever we got in a full 9-key structure
        fallback = {name: self._empty_quadrant() for name in self._QUADRANT_NAMES}
        if candidate:
            # If model returned one flat object, put it in middle_center
            fallback["middle_center"] = candidate
            fallback["_parse_error"] = True
        else:
            fallback["_parse_error"] = True
            fallback["_raw_text"] = text.strip()[:500]
        return fallback


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python plm_analyzer.py <image_path>")
        sys.exit(1)

    analyzer = PLMAnalyzer()
    result = analyzer.analyze_image(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
