"""Break-glass scrapers for the *official* sources (TWSE BSR + TPEx brokerBS).

Not on the daily path. The pipeline runs on the SysJust ``.djhtm`` mirrors
(``ingest.djhtm_*``) because they are plain HTTP and answer from GitHub-Actions
IPs. These modules -- image-CAPTCHA solving for BSR, a stealth headed browser for
TPEx's managed Turnstile -- are kept, tested, and importable so the project can
switch back to the primary sources if every ``.djhtm`` mirror goes away.
"""
# Windows DLL-load-order guard: onnxruntime (via ddddocr in `captcha`) must bind
# its native libs before pyarrow, or `import onnxruntime` fails with an
# ImportError. Bind it first here so importing anything from `fallback` is safe.
try:  # pragma: no cover - environment guard
    import onnxruntime as _onnxruntime  # noqa: F401
except Exception:
    pass
