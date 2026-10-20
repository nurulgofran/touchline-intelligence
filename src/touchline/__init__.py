"""Touchline Intelligence -- a synthetic match insights engine.

The package is layered so that the heavy, optional dependencies (the
Microsoft Agent Framework, the Azure SDKs, numpy, OpenCV) are only imported
inside the modules that need them. Importing :mod:`touchline` and the
simulator, the shared state store and the template provider therefore works
with nothing but ``pydantic`` installed.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
