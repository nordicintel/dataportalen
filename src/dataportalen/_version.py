"""The package version, in one place.

`pyproject.toml` reads `__version__` from here via hatchling's version source,
and both `dataportalen/__init__.py` and `dataportalen/transport.py` import it.
Keeping it in a module of its own avoids an import cycle: `transport` cannot
import from `__init__`, since `__init__` imports `transport`.

To release a new version, edit this line and nothing else. See RELEASING.md.
"""

__version__ = "0.1.0"
