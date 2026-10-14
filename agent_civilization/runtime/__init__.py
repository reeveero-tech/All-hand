"""Runtime deadline helpers for the live handlers (R-104c2, D-047 amendment).

Production consumer: ``real_dispatcher._researcher`` imports ``make_deadline``
and ``remaining`` from here to enforce its overall budget (R-107c). The
package's role is live and tested — tests/test_r107_researcher_retry.py
exercises the budget through the handler that consumes these helpers.
"""

import time as _time
from typing import Callable


def make_deadline(budget_s: float) -> float:
    """A monotonic deadline ``budget_s`` seconds from now."""
    return _time.monotonic() + budget_s


def remaining(deadline: float) -> float:
    """Seconds left until ``deadline`` (never negative)."""
    return max(0.0, deadline - _time.monotonic())


__all__ = ["make_deadline", "remaining", "Callable"]
