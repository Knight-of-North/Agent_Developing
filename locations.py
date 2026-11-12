"""搜证地点：把隐藏线索映射到少量场景地点，供玩家选择调查。

这是轻量玩法层，不改变剧本 schema：旧剧本没有 locations 字段时也能用。
"""

DEFAULT_LOCATIONS = ("书房", "花园", "门厅")


def location_for_index(index: int) -> str:
    """按隐藏线索顺序稳定分配地点（同一局每次 rerun 结果一致）。"""
    return DEFAULT_LOCATIONS[index % len(DEFAULT_LOCATIONS)]


def clues_at_location(hidden_clues: list[str] | None, investigated: list[str] | None, location: str) -> list[str]:
    """返回某个地点尚未被发现的线索。"""
    found = set(investigated or [])
    return [
        clue for i, clue in enumerate(hidden_clues or [])
        if clue not in found and location_for_index(i) == location
    ]


def location_status(hidden_clues: list[str] | None, investigated: list[str] | None) -> list[dict]:
    """给 UI 用的地点状态：remaining / searched。"""
    hidden_clues = hidden_clues or []
    found = set(investigated or [])
    return [
        {
            "name": loc,
            "remaining": sum(
                1 for i, clue in enumerate(hidden_clues)
                if clue not in found and location_for_index(i) == loc
            ),
            "searched": sum(
                1 for i, clue in enumerate(hidden_clues)
                if clue in found and location_for_index(i) == loc
            ),
        }
        for loc in DEFAULT_LOCATIONS
    ]
