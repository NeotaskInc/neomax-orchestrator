"""Neomax orchestration package with a compatibility facade."""

import importlib
import sys
import types

from .module_registry import MODULE_REGISTRY


_MODULES = {}
_OWNERS = {}
for _spec in MODULE_REGISTRY:
    _module = importlib.import_module(f"{__name__}.{_spec.name}")
    _MODULES[_spec.name] = _module
    for _symbol in _spec.exports:
        if _symbol in _OWNERS:
            raise RuntimeError(f"duplicate Neomax symbol owner: {_symbol}")
        if not hasattr(_module, _symbol):
            raise RuntimeError(f"missing Neomax export: {_spec.name}.{_symbol}")
        _OWNERS[_symbol] = _module


class _PackageFacade(types.ModuleType):
    def __getattr__(self, name):
        owner = _OWNERS.get(name)
        if owner is not None:
            return getattr(owner, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        owner = _OWNERS.get(name)
        if owner is not None:
            setattr(owner, name, value)
            return
        super().__setattr__(name, value)

    def __delattr__(self, name):
        owner = _OWNERS.get(name)
        if owner is not None:
            delattr(owner, name)
            return
        super().__delattr__(name)

    def __dir__(self):
        return sorted(set(super().__dir__()) | set(_OWNERS))


__all__ = tuple(_OWNERS)
sys.modules[__name__].__class__ = _PackageFacade
