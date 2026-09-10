"""Present so that pytest puts the project root on ``sys.path``.

Without this, ``tests/`` (which has no ``__init__.py``) becomes the import root
and ``from app.parser import ...`` fails with ModuleNotFoundError. pytest
prepends the directory containing the top-level conftest.py, so an empty file
here is enough.
"""