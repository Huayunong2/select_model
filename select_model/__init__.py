"""Public Python API for select-model."""

from .constants import PROJECT_VERSION
from .router import choose, route

__all__ = ["PROJECT_VERSION", "choose", "route"]
