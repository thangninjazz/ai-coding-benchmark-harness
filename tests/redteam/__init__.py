"""Red-team test corpus.

Every test in this package executes with the static AST scan DISABLED (the runtime layer
must stand entirely on its own). Adversarial submissions attempting to forge reports,
monkeypatch test runners, inspect secrets, exhaust resources, or tamper with workspaces
must all end NOT RESOLVED.
"""
