#!/usr/bin/env python3
"""R-177 (D-144): start the SUT with an in-process resource sampler.

Equivalent to ``python -m uvicorn <args>`` (cwd first on sys.path, same
uvicorn entry point), plus, when ``R177_RES_CSV`` is set, one daemon thread
that samples this process's own /proc entries every ``R177_RES_INTERVAL_S``
seconds (default 10). The server's non-dumpable hardening
(``safety.harden_server_process``) is NOT touched: a process may always read
its own /proc/<pid>/fd and smaps_rollup. Test harness only, never production.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import r177_resource_sampler as _rs  # noqa: E402

# behave like `python -m uvicorn`: cwd first, and do not leave scripts/ on the
# import path (avoids top-level name clashes with the server's modules)
sys.path.remove(_HERE)
sys.path.insert(0, os.getcwd())

if os.environ.get("R177_RES_CSV"):
    _rs.start_inprocess(os.environ["R177_RES_CSV"],
                        float(os.environ.get("R177_RES_INTERVAL_S", "10")))

if __name__ == "__main__":
    from uvicorn.main import main
    sys.argv = ["uvicorn"] + sys.argv[1:]
    sys.exit(main())
