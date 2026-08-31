"""What a run did, in a vocabulary that is the same for every source.

Seven outcomes and one marker character each, fixed, so a stream of markers is
readable without knowing which source produced it. No per-source counters.

These runs last hours against a throttling archive, so the marker stream is
interrupted by a timestamped heartbeat: a wall of undated markers gives no way
to tell "alive but rate-limited" from "hung".
"""
