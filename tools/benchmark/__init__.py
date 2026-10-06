__all__ = ["run_benchmark"]


def __getattr__(name: str):
    if name == "run_benchmark":
        from .harness import run_benchmark

        return run_benchmark
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
