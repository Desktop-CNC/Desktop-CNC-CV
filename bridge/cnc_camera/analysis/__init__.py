"""
Host-side vision analyzer
To add a feature:
    1. Subclass ``Analyzer`` in a new module in this package.
    2. Decorate it with ``@register("my_name")``.
    3. Import the module in ``_BUILTINS`` below.
    4. Enable it per camera in config.toml:  analyzers = ["analyzer_name"]

Analyzers get uncompressed images plus the machine state from the UGS pendant if setup for it
"""
from .base import AnalysisContext, Analyzer, AnalysisRunner, available, register

_BUILTINS = ("camera_health",)
for _name in _BUILTINS:
    __import__(f"{__name__}.{_name}")

__all__ = ["Analyzer", "AnalysisContext", "AnalysisRunner", "register", "available"]
