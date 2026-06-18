from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
FRONTEND_DIR = PROJECT_ROOT / "Frontend"

DB_PATH = PROJECT_ROOT / "Backend" / "Environment" / "eixample_overture.duckdb"
PERCEPTION_DB_PATH = PROJECT_ROOT / "Backend" / "Environment" / "perception.duckdb"

SV_OUTPUT_DIR   = PROJECT_ROOT / "Backend" / "Environment" / "output"
SV_IMAGES_DIR   = SV_OUTPUT_DIR / "images"
SV_RESULTS_DIR  = SV_OUTPUT_DIR / "results"

BENCHMARK_FILE     = PROJECT_ROOT / "benchmark" / "vlm_results.json"
COMPARE_IMAGES_DIR = PROJECT_ROOT / "benchmark" / "vlm_compare_images"
VLM_ANALYSIS_DIR   = PROJECT_ROOT / "benchmark" / "vlm_analysis_outputs"

COMPARE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
