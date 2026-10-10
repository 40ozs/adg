"""Demo Mode: the real ADG application, running on a synthetic estate.

Selected by deployment configuration (``ADG_RUNTIME_MODE=demo``), never by a request. The
synthetic data itself is owned elsewhere -- :mod:`app.demo` and the generators registered in
:mod:`app.demo_mode.datasets` -- and this package is the boundary around it:

* :mod:`app.demo_mode.datasets` -- the contract a synthetic dataset satisfies, and the registry.
* :mod:`app.demo_mode.providers` -- which observation source the runtime mode selects.
* :mod:`app.demo_mode.ingest` -- the in-process synthetic provider, through the real routes.
* :mod:`app.demo_mode.marker` -- positively identifying a demo database.
* :mod:`app.demo_mode.lifecycle` -- reset, synthetic scan, status, startup verification.
* :mod:`app.demo_mode.policy` -- what each mutating route does in demo mode.

Deliberately empty of imports: :mod:`app.config` imports the dataset registry, and a package
``__init__`` that imported the rest would import the application from its own configuration.
See docs/architecture/demo-mode.md.
"""
