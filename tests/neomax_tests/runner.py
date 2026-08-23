"""Registry-driven Neomax regression-test runner."""

import importlib

from . import support
from .suite_registry import SUITE_REGISTRY, TEST_ORDER


_TESTS = {}
for _spec in SUITE_REGISTRY:
    _module = importlib.import_module(f"{__package__}.{_spec.name}")
    for _name in _spec.tests:
        if _name in _TESTS:
            raise RuntimeError(f"duplicate Neomax test owner: {_name}")
        _test = getattr(_module, _name, None)
        if not callable(_test):
            raise RuntimeError(f"missing Neomax test: {_spec.name}.{_name}")
        _TESTS[_name] = _test

if set(_TESTS) != set(TEST_ORDER) or len(TEST_ORDER) != len(set(TEST_ORDER)):
    raise RuntimeError("Neomax test registry and execution order differ")


def main():
    for name in TEST_ORDER:
        _TESTS[name]()
    print("\nALL %d CHECKS PASSED" % support.PASS)
