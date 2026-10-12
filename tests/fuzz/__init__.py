"""Deterministic synthetic fuzzing for silent-CLEAN failures.

The package renders synthetic loss-run packets, knows the truth about every
claim it printed, runs the real pipeline and reconciliation, and classifies the
outcome against the one status policy. Everything is synthetic (spec section
9): invented carriers, claim numbers and amounts. Nothing here reads a real
document, a corpus manifest or a vision recording.
"""
