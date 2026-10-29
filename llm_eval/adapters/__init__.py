"""Each adapter invokes the framework's own metric."""

import importlib


def get_adapter(name: str):
    return importlib.import_module(f"llm_eval.adapters.{name}")
