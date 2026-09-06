"""
GC guard for background-thread phases.

Tcl/Tk is single-threaded: if Python's cyclic GC happens to run inside a
worker thread and collects a dead Tkinter object (an old window, dialog
root, or PhotoImage), its deallocation calls into Tcl from the wrong
thread and Tcl aborts the whole process (Tcl_AsyncDelete panic, SIGILL).

The guard makes collection deterministic and main-thread-only around any
threaded phase:

    guard_begin()   # MAIN thread, before starting workers:
                    #   collect existing garbage safely, then disable GC
    ... worker threads run with GC off (allocation still works; the heap
        just grows until re-enabled) ...
    guard_end()     # MAIN thread (completion/error callback):
                    #   re-enable GC and collect what accumulated

Both calls are idempotent and safe to pair loosely, an unmatched
guard_begin only costs memory growth until the next guard_end.
"""

import gc


def guard_begin() -> None:
    """Call on the MAIN thread immediately before spawning worker threads."""
    gc.collect()
    gc.disable()


def guard_end() -> None:
    """Call on the MAIN thread when the workers have finished (or failed)."""
    gc.enable()
    gc.collect()
