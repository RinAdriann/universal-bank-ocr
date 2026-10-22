# Spec 002 — Camera-Photo Detection, Illumination Normalization & Strip-Slope Dewarping

## Overview
Camera photos of physical bank statements (often wrapped inside `.pdf` files) exhibit dark gray/uneven background illumination (`median ~ 163` instead of `255`), non-uniform perspective tilt across the page (`-0.8°` at top vs `0.0°` at bottom), and blurry character strokes where `RapidOCR` misreads thin slashes `/` in dates or `.00D`/`.00K` suffixes on amounts.

This specification introduces a fast (`< 1 ms`) automatic camera-photo detector (`is_camera_photo`) in `component/ocr.py` that routes camera-taken statement images/PDF pages through local morphological background division (`normalize_illumination`) and 5-band vertical strip-slope dewarping (`dewarp_rows`), while leaving clean flatbed scans and digital PDFs on their existing fast path.

## Artifacts
- [Implementation Plan](./plan.md)
- [Task Checklist](./task.md)
- [Verification Walkthrough](./walkthrough.md)
