# Windows DLL-load-order guard: onnxruntime must bind its native libs BEFORE
# pyarrow loads, or `import onnxruntime` fails with
#   ImportError: DLL load failed while importing onnxruntime_pybind11_state
# pytest collects test modules alphabetically, so test_aggregate.py imports
# pyarrow (via ingest.schema) before test_captcha.py imports onnxruntime.
# Importing it here, at collection start, fixes the order for the whole suite.
try:  # pragma: no cover - environment guard
    import onnxruntime  # noqa: F401
except Exception:  # onnxruntime is a hard dep; tolerate absence in odd envs
    pass
