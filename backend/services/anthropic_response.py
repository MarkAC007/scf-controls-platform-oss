"""Extract the text answer from an Anthropic Messages API response.

Models with adaptive thinking enabled — which Claude Opus 5 runs BY DEFAULT —
return their reasoning as ``ThinkingBlock`` entries ahead of the ``TextBlock``
that carries the actual answer. ``message.content[0].text`` therefore raised
``AttributeError: 'ThinkingBlock' object has no attribute 'text'`` the moment
evidence assessment moved to Opus 5 (v0.26.0 production incident, 2026-09-03).

Every call site that reads a Messages response goes through this helper so the
assumption "the first block is the answer" cannot be reintroduced one service
at a time. Selecting by block type is the shape the API contract actually
promises; block ORDER is not part of it.
"""

from typing import List


class NoTextBlockError(ValueError):
    """The response carried no text block at all.

    Distinct from malformed JSON inside the text: this means the model returned
    only non-text blocks (thinking, tool use), so there is nothing to parse and
    the caller should surface the block types it did get.
    """


def extract_text(message) -> str:
    """The concatenated text blocks of a Messages API response, in order.

    Accepts the SDK ``Message`` object (or any object whose ``content`` is a
    list of blocks with ``type``/``text`` attributes, which is what tests pass).
    """
    parts: List[str] = [
        block.text
        for block in message.content
        if getattr(block, "type", None) == "text"
    ]
    if not parts:
        kinds = [getattr(block, "type", "?") for block in message.content]
        raise NoTextBlockError(
            f"model response contained no text block (got: {kinds}); "
            f"stop_reason={getattr(message, 'stop_reason', None)!r}"
        )
    return "".join(parts)
