"""Skip the entire fallback test tree unless its heavy optional deps are present
(ddddocr / onnxruntime for the CAPTCHA solver, patchright for the TPEx browser).
The daily pipeline does not install these; `pip install -r requirements-fallback.txt`
does.
"""
import pytest

pytest.importorskip("ddddocr", reason="fallback dep not installed")
pytest.importorskip("patchright", reason="fallback dep not installed")
