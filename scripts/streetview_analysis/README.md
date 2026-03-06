# Street View Analysis

This folder contains a small pipeline for analyzing a street-view image with Meta's `facebook/Perception-LM-1B` model.

The main workflow is:
1. fetch or provide a source image,
2. split it into a 3x3 grid,
3. analyze each cell,
4. save a JSON result,
5. inspect the result in `viewer.html`.

All commands below assume you are running them from the repository root (`LLM_Based_UrbanABM`).

## What is here

- `run_analysis.py` - end-to-end entry point
- `streetview_fetcher.py` - download a Street View image from coordinates
- `grid_splitter.py` - split an image into a labeled 3x3 grid
- `plm_analyzer.py` - run PerceptionLM on one image or grid cell
- `viewer.html` - browse saved result JSON files

## Prerequisites

- Python 3.10+
- Packages from `requirements.txt`
- `torch` and `transformers` for `plm_analyzer.py`
- Access to the gated Hugging Face model `facebook/Perception-LM-1B`
- Internet access on the first model run if the model is not already cached locally
- Optional: a Google Street View Static API key if you want to fetch images by latitude/longitude

Install the Python dependencies:

```powershell
python -m pip install -r requirements.txt
python -m pip install torch transformers
```

`python -m pip install torch transformers` often gives you a CPU-only Torch build. If you want GPU acceleration, install the Torch build that matches your CUDA version instead of the default CPU build.

Before the first analysis run, make sure your machine is authenticated with Hugging Face and that your account has access to `facebook/Perception-LM-1B`. If the model is already cached locally, this step is not needed.

## Environment setup

`streetview_fetcher.py` loads environment variables from `scripts\.env`.

Create the file from the template:

```powershell
Copy-Item scripts\.env.example scripts\.env
```

For this pipeline, only `GOOGLE_STREETVIEW_API_KEY` is required, and only when you use `--lat` / `--lng` or `streetview_fetcher.py` directly.

```env
GOOGLE_STREETVIEW_API_KEY=your_google_streetview_key_here
```

The other values in `scripts\.env.example` are used by other parts of the repository and are not required for local-image analysis in this folder.

## How device selection works

`PLMAnalyzer` chooses `cuda` only when `torch.cuda.is_available()` returns `True`. Otherwise it falls back to CPU.

At startup, `plm_analyzer.py` now prints:

- the installed Torch version
- the Torch CUDA build value
- `torch.cuda.is_available()`
- the visible CUDA device count
- the reason CPU was selected when GPU is unavailable

Quick local check:

```powershell
python -c "import torch; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available()); print(torch.cuda.device_count())"
```

If this prints a `+cpu` Torch build or `None` for `torch.version.cuda`, the current environment cannot use GPU inference until you install a CUDA-enabled Torch build.

## Quick start

### Option A - Analyze a local image (recommended)

This is the easiest way to verify the pipeline because it does not require any API key.

```powershell
python scripts\streetview_analysis\run_analysis.py --image path\to\street_image.jpg
```

### Option B - Fetch a Google Street View image and analyze it

```powershell
python scripts\streetview_analysis\run_analysis.py --lat 41.3874 --lng 2.1686 --heading 90
```

You can also provide the API key directly on the command line:

```powershell
python scripts\streetview_analysis\run_analysis.py --lat 41.3874 --lng 2.1686 --heading 90 --api-key YOUR_KEY
```

### Start the viewer

```powershell
python scripts\streetview_analysis\run_analysis.py --serve
```

Then open:

```text
http://localhost:8500/viewer.html
```

Use `--port` if you want a different port:

```powershell
python scripts\streetview_analysis\run_analysis.py --serve --port 8600
```

The viewer reads JSON files from `output\results\` and can also load a local JSON file through the file upload control.

## Command reference

Show the main CLI help:

```powershell
python scripts\streetview_analysis\run_analysis.py --help
```

Main options:

- `--image` - analyze a local image and skip the Street View fetch step
- `--lat` / `--lng` - fetch a Street View image before analysis
- `--heading` - camera heading in degrees
- `--api-key` - pass the Google Street View API key without using `scripts\.env`
- `--serve` - start the local viewer for `viewer.html`
- `--port` - override the viewer port (default: `8500`)

## Run each script directly

Fetch a Street View image only:

```powershell
python scripts\streetview_analysis\streetview_fetcher.py 41.3874 2.1686 90
```

Split a local image into a 3x3 grid:

```powershell
python scripts\streetview_analysis\grid_splitter.py path\to\street_image.jpg
```

Analyze a single image or grid cell with PerceptionLM:

```powershell
python scripts\streetview_analysis\plm_analyzer.py path\to\image.jpg
```

Run the full pipeline:

```powershell
python scripts\streetview_analysis\run_analysis.py --image path\to\street_image.jpg
```

## Output locations

The pipeline creates and reuses these folders under `scripts\streetview_analysis\output\`:

- `images\` - downloaded source images
- `grids\` - generated 3x3 grid cells
- `results\` - combined JSON output files

`run_analysis.py` saves a timestamped JSON result file in `output\results\`.

## Troubleshooting

- `PerceptionLM` keeps using CPU
  - Check the startup diagnostics from `plm_analyzer.py`.
  - If Torch reports a `+cpu` build or `torch.version.cuda == None`, install a CUDA-enabled Torch build that matches your NVIDIA driver and CUDA runtime.
  - If Torch has CUDA support but device count is `0`, the GPU is not visible to the current process. On Docker or WSL setups, make sure the NVIDIA runtime exposes the GPU to the container.
- `ValueError: GOOGLE_STREETVIEW_API_KEY not set in environment or .env`
  - Set `GOOGLE_STREETVIEW_API_KEY` in `scripts\.env`, pass `--api-key`, or use `--image` instead of `--lat` / `--lng`.
- `401 Unauthorized` or `You are trying to access a gated repo`
  - `facebook/Perception-LM-1B` is a gated Hugging Face model. Make sure your account has access and that the machine is authenticated before running `plm_analyzer.py` or `run_analysis.py`.
- `ModuleNotFoundError: No module named 'torch'` or `No module named 'transformers'`
  - Install the missing packages manually because they are not currently listed in `requirements.txt`.
- The first run is slow
  - The model may need to download from Hugging Face, and CPU inference is slower than GPU inference.
- The viewer shows an empty dropdown
  - Run an analysis first so `output\results\` contains JSON files, or upload a JSON file manually in the browser.
