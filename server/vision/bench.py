"""CPU latency benchmark for the perception pipeline.

    cd server
    .venv\\Scripts\\python -m vision.bench            # synthetic 1280x800 form
    .venv\\Scripts\\python -m vision.bench shot.png   # your own screenshot

Prints cold (model load + first run) and warm timings.
"""

import base64
import os
import statistics
import sys
import time


def _load(path=None) -> str:
    if path:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))
    from conftest import make_form_image, to_b64  # type: ignore

    img, _ = make_form_image()
    return to_b64(img)


def main():
    from vision.pipeline import perceive

    b64 = _load(sys.argv[1] if len(sys.argv) > 1 else None)
    t0 = time.perf_counter()
    first = perceive(b64)
    cold = (time.perf_counter() - t0) * 1000
    runs = [perceive(b64)["timingsMs"] for _ in range(5)]
    totals = [r["total"] for r in runs]
    print(f"image {first['imageSize']}, elements {len(first['elements'])}")
    print(f"cold (incl. model load): {cold:.0f} ms")
    print(
        "warm total ms: median %d, min %d, max %d | ocr median %d, cv median %d"
        % (
            statistics.median(totals),
            min(totals),
            max(totals),
            statistics.median(r["ocr"] for r in runs),
            statistics.median(r["cv"] for r in runs),
        )
    )
    print("summary:", first["summary"])


if __name__ == "__main__":
    main()
