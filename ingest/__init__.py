# Windows DLL-load-order guard.
#
# On Windows, `import onnxruntime` fails with
#   ImportError: DLL load failed while importing onnxruntime_pybind11_state
# whenever `pyarrow` has already been imported in the same process (pyarrow's
# bundled native libs shadow what onnxruntime needs). Several modules in this
# package import pyarrow (ingest.schema) and onnxruntime via ddddocr
# (ingest.captcha); the daily pipeline (ingest.run) imports both. Binding
# onnxruntime first here — before any submodule can pull in pyarrow — keeps
# the order correct for every entry point.
try:  # pragma: no cover - environment guard
    import onnxruntime as _onnxruntime  # noqa: F401
except Exception:  # onnxruntime is a hard dep; tolerate absence in odd envs
    pass
