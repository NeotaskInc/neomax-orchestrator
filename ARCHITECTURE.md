# Neomax architecture

Neomax is a provider-neutral orchestration package. Its public launchers are intentionally thin;
runtime behavior lives in the `lib/neomax/` Python package.

## Package contract

`lib/neomax/module_registry.py` is authoritative. Each registry entry declares:

- the module's import/load order;
- its stable responsibility;
- every compatibility symbol that module owns.

`lib/neomax/__init__.py` loads only registered modules, rejects duplicate symbol ownership, rejects
missing exports, and exposes the registered API through a compatibility facade. The facade also
forwards test and integration overrides to the owning module, preserving the existing public Python
surface without copying state.

`lib/neomax_core.py` is only the executable compatibility entrypoint used by the shell launchers.
It imports the package and invokes `main()`. It must never regain product behavior.

## Domain boundaries

The registry groups implementation around stable responsibilities:

- configuration, models, provider authentication, and isolated profile state;
- provider telemetry, event parsing, usage windows, reporting, and keepalive;
- account selection, quota deadlines, rotation, handoff, and failover;
- projects, tasks, credentials, durable runs, dispatch, cleanup, and git operations;
- status aggregation, session history, modes, and orientation;
- scheduling, reconciliation, merging, issue lifecycle, and admission queues;
- command authorization and CLI dispatch.

Modules are split when behavior has a distinct owner or dependency boundary. There is no arbitrary
line-count rule: a cohesive implementation may remain together, while unrelated behavior must not
be combined merely to reduce file count.

## Dependency rules

- Domain modules use explicit relative imports of other domain owners.
- Domain modules never import the package facade (`import neomax`) to reach another domain.
- Shared mutable state has exactly one owning module and is accessed through that owner.
- The portal consumes `neomax status --json`; it does not implement a parallel status model.
- Provider-specific parsing and command construction stay behind provider-neutral orchestration
  contracts so every launcher can use every permitted worker pool.
- Imports must remain side-effect safe: importing the package must not start a provider, touch an
  authenticated endpoint, launch a worker, or mutate user state.

## Changing the package

When adding or moving behavior:

1. Choose the domain that owns the responsibility; create a new module only for a real boundary.
2. Add or update its single registry entry and move its owned exports with it.
3. Replace cross-domain global access with an explicit relative owner import.
4. Keep `lib/neomax_core.py` and public shell launchers free of domain behavior.
5. Add hermetic regression coverage, update documentation, and append a product-safe work-log entry.
6. Run the complete gate from `AGENTS.md`. Authenticated provider calls require separate explicit
   operator authorization and are never part of repository verification.

The product-contract test verifies that every implementation module is registered exactly once,
registry names and exports are unique, every declared export exists, and every module imports through
the normal Python package loader.

## Test architecture

`tests/neomax_tests/suite_registry.py` is the test counterpart to the implementation registry. It
declares each responsibility-based suite, the tests it owns, and the explicit compatibility execution
order. `tests/neomax_tests/support.py` owns shared hermetic fixtures and the package facade used for
monkeypatching. `tests/test_neomax.py` only invokes the registry-driven runner.

Every test must have exactly one suite owner. Add a new test to both its domain module and registry
entry; change `TEST_ORDER` only when ordering is intentional. Keep provider credentials and live
network requests out of this suite.
