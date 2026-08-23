"""One way to render a user as a label (#781).

Free-text `owner` is gone from evidence tracking, so every surface that used to
print that string now prints a resolved person instead — the task list, the
task summary, and the generated evidence schedule. Three copies of
``display_name or email`` is how the next inconsistency starts, and this PR's
whole argument is that one concept should have one representation.

Mirrors ``userLabel()`` in ``webclient/src/data/userDisplay.ts``.
"""
from __future__ import annotations

from typing import Optional


def user_label(user) -> Optional[str]:
    """Display label for a resolved user, or None when there is no user.

    Display name where there is one, email otherwise. An email is a worse label
    than a name, and a much better one than a blank cell.
    """
    if user is None:
        return None
    return (getattr(user, "display_name", None) or "").strip() or user.email
