"""Plain-language descriptions. They only describe what the original SQL/code
computes (see analysis/src/sql); they do not add new metrics."""

TERMS = {
    "Ripple": "A service dependency graph. Two processes are linked when they interact through TCP sockets, unix sockets or pipes (from `pid_connections.sql`).",
    "Baseline": "A time range where the application behaved normally.",
    "Compare": "A time range where behaviour was degraded and you want an explanation.",
    "Share": "Fraction of a thread's sampled time spent in a state (e.g. running, waiting). Defined in the `taskstats_view` view.",
    "run": "Share of time the thread was running on a CPU.",
    "runqueue": "Share of time the thread was runnable but waiting for a CPU.",
    "uninterruptible": "Share of time in uninterruptible sleep.",
    "blkio": "Share of time delayed by block I/O.",
    "futex_schedule": "Futex wait time on futexes the original SQL classifies as *schedule*.",
    "futex_contention": "Futex wait time on futexes the original SQL classifies as *contention* (the same thread both waits on and wakes the futex).",
    "Degradation score": "Per-service vector norm of the six share scores above, computed by the original `compute_scores`. Higher = larger change in the compare range relative to baseline.",
    "Machine": "Id of the host the data was collected on. `?` in the graph marks destinations with no known machine.",
}


def glossary_markdown() -> str:
    return "\n".join(f"- **{k}** — {v}" for k, v in TERMS.items())
