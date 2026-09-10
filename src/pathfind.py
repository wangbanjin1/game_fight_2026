# -*- coding: utf-8 -*-
"""移动与寻路。

初步方案采用贪心单步移动：每回合朝目标方向移动一格（8 方向），
避开已知障碍。后续可替换为 BFS/A* 并考虑碰撞预测。
"""
from typing import Optional, Set, Tuple

from models import Pos

# 8 个移动方向
DIRECTIONS = (
    (-1, -1), (0, -1), (1, -1),
    (-1, 0),           (1, 0),
    (-1, 1),  (0, 1),  (1, 1),
)


def greedy_step(frm: Pos, to: Pos,
                blocked: Set[Tuple[int, int]],
                width: int, height: int) -> Optional[Pos]:
    """返回朝目标移动一格后的位置；无可行位置时返回 None（原地不动）。"""
    best: Optional[Pos] = None
    best_key = None
    cur_dist = frm.cheb(to)
    for dx, dy in DIRECTIONS:
        p = Pos(frm.x + dx, frm.y + dy)
        if not (0 <= p.x < width and 0 <= p.y < height):
            continue
        if (p.x, p.y) in blocked:
            continue
        d = p.cheb(to)
        if d > cur_dist:
            continue  # 不允许走远
        # 优先缩短距离，其次贴近 x/y 中较大差值方向（简单 tie-break）
        key = (d, abs(p.x - to.x) + abs(p.y - to.y))
        if best_key is None or key < best_key:
            best, best_key = p, key
    return best
