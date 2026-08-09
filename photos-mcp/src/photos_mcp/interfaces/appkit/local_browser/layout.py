"""Pure local-browser pane sizing helpers."""

_SIDEBAR_MIN_WIDTH = 240.0
_SIDEBAR_MAX_FRACTION = 0.40
_CONTENT_MIN_WIDTH = 500.0
_INSPECTOR_MIN_WIDTH = 320.0


def maximum_sidebar_width(
    total_width: float,
    divider_width: float,
    inspector_width: float = _INSPECTOR_MIN_WIDTH,
) -> float:
    available_with_minimum_center = (
        total_width
        - (divider_width * 2.0)
        - _CONTENT_MIN_WIDTH
        - max(_INSPECTOR_MIN_WIDTH, inspector_width)
    )
    return max(
        _SIDEBAR_MIN_WIDTH,
        min(total_width * _SIDEBAR_MAX_FRACTION, available_with_minimum_center),
    )


__all__ = ["maximum_sidebar_width"]
