"""Per-instrument transfer writers. Each wraps an existing transport or emits a
staging format; none holds a shared connection, so parallel jobs stay isolated.
"""
