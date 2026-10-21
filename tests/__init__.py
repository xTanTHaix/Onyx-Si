"""
OnyxSi test package marker with automatic 9p filesystem cache bypass.
"""
import os
import sys
import importlib.util
from pathlib import Path

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_TESTS_DIR)

class _TestsPackageFinder:
    def find_spec(self, fullname, path, target=None):
        if not (fullname == "tests" or fullname.startswith("tests.")):
            return None
        parts = fullname.split(".")
        subpath = os.path.join(_PROJECT_DIR, *parts)
        init_py = os.path.join(subpath, "__init__.py")
        if os.path.exists(init_py):
            return importlib.util.spec_from_file_location(fullname, init_py, submodule_search_locations=[subpath])
        py_file = subpath + ".py"
        if os.path.exists(py_file):
            return importlib.util.spec_from_file_location(fullname, py_file)
        return None

if not any(isinstance(f, _TestsPackageFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _TestsPackageFinder())
