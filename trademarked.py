from __future__ import annotations

from cashtube_utils import make_session, trademark_risk


def is_trademarked(word: str) -> bool:
    """Return True if the word has a registered trademark (USPTO).

    Requires USPTO_API_KEY in the environment.  Returns True (risky) when the
    API is not configured or the request fails, to default toward caution.
    """
    session = make_session()
    result = trademark_risk(session, word)
    return result in ("risky", "error", "not_configured")
