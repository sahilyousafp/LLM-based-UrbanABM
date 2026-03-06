"""
Grid Splitter — Split a street view image into a 3×3 grid of labeled cells.
"""

from pathlib import Path
from PIL import Image


GRID_POSITIONS = [
    "top_left",     "top_center",     "top_right",
    "middle_left",  "middle_center",  "middle_right",
    "bottom_left",  "bottom_center",  "bottom_right",
]


def split_image_3x3(image_path: str | Path, output_dir: str | Path | None = None) -> dict[str, Path]:
    """Split *image_path* into 9 equal cells and return {position: path} mapping."""
    image_path = Path(image_path)
    img = Image.open(image_path)
    w, h = img.size
    cell_w, cell_h = w // 3, h // 3

    if output_dir is None:
        output_dir = image_path.parent / "grids"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = image_path.stem
    cells: dict[str, Path] = {}

    for idx, pos in enumerate(GRID_POSITIONS):
        row, col = divmod(idx, 3)
        left = col * cell_w
        upper = row * cell_h
        right = left + cell_w if col < 2 else w
        lower = upper + cell_h if row < 2 else h

        cell_img = img.crop((left, upper, right, lower))
        cell_path = output_dir / f"{stem}_{pos}.jpg"
        cell_img.save(cell_path, "JPEG", quality=92)
        cells[pos] = cell_path

    return cells


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python grid_splitter.py <image_path> [output_dir]")
        sys.exit(1)
    out = sys.argv[2] if len(sys.argv) > 2 else None
    result = split_image_3x3(sys.argv[1], out)
    for pos, p in result.items():
        print(f"  {pos}: {p}")
