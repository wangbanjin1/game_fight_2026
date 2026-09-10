# -*- coding: utf-8 -*-
"""核心决策：根据每回合 GameState 产出角色指令。

整体思路：
- 白天：工人采集矿石 / 建造武器工事 / 找小贩贩卖；开拓者接取并完成任务；
        白天末尾所有角色回撤到武器旁准备夜战。
- 黑夜：三名角色就近绑定三座武器，站上武器周围一格后操控武器攻击机器人。

跨回合记忆（进程常驻）：
- failed_builds   建造失败的坐标黑名单（下回合换个位置再试）
- pending_builds  上一回合发出的 build 指令，用于对照 lastRoundRoleActionResults
- waiting_llm     已向 LLM 发出 prompt、等待 llmResp 回填答案
"""
from typing import Dict, List, Optional, Set, Tuple

from models import (GameState, PlayerTask, Pos, Role,
                    ORE_TYPES, WEAPON_COST, WEAPON_LIMIT, WEAPON_TYPES)
from pathfind import greedy_step

# 白天剩余多少回合时开始回撤防守
NIGHT_PREPARE_ROUND = 55
# 背包矿石达到该数量就去贩卖
SELL_THRESHOLD = 15


class Strategy:
    def __init__(self) -> None:
        self.failed_builds: Set[Tuple[int, int]] = set()
        self.pending_builds: Dict[int, Tuple[int, int]] = {}
        self.waiting_llm = False

    # ------------------------------------------------------------------ API

    def decide(self, st: GameState) -> dict:
        """主入口：返回 response JSON（dict）"""
        self._update_memory(st)
        cmds: Dict[int, dict] = {}
        prompt = ""
        try:
            if st.is_day:
                prompt = self._day(st, cmds)
            else:
                self._night(st, cmds)
        except Exception:
            # 兜底：任何异常都不能导致响应格式错误（会被计为异常响应）
            cmds, prompt = {}, ""
        return {
            "roleCommandMap": {str(k): v for k, v in cmds.items()},
            "prompt": prompt,
            "executeCmd": "",
        }

    # ------------------------------------------------------------- 记忆更新

    def _update_memory(self, st: GameState) -> None:
        """根据上回合动作执行结果更新黑名单等记忆"""
        for role_id, cell in list(self.pending_builds.items()):
            if st.last_results.get(role_id) is False:
                self.failed_builds.add(cell)
            del self.pending_builds[role_id]

    # ---------------------------------------------------------------- 白天

    def _day(self, st: GameState, cmds: Dict[int, dict]) -> str:
        prompt = ""
        pioneer = self._find(st, "pioneer")
        workers = sorted((r for r in st.our_units() if r.role_type == "worker"),
                         key=lambda r: r.id)

        if pioneer is not None:
            prompt = self._pioneer_day(st, pioneer, cmds)
        # 建造任务只交给 1 号工人，避免两人抢同一个建造点
        for i, w in enumerate(workers):
            self._worker_day(st, w, cmds, builder=(i == 0))

        # 白天末尾：闲置角色回撤到基地附近，准备夜战
        if st.round_in_day >= NIGHT_PREPARE_ROUND:
            station = st.our_station()
            if station is not None:
                for r in st.our_units():
                    if r.id not in cmds and r.pos.cheb(station.pos) > 2:
                        self._move_toward(st, r, station.pos, cmds)
        return prompt

    def _pioneer_day(self, st: GameState, p: Role, cmds: Dict[int, dict]) -> str:
        """开拓者：接任务 -> 调 LLM 解题 -> 提交答案"""
        if st.phase_task:
            # 任务进行中：上一轮已发出 prompt 且拿到了 llmResp，则提交答案
            if self.waiting_llm and st.llm_resp:
                cmds[p.id] = {"action": "submitAnswer",
                              "taskAnswer": st.llm_resp.strip()}
                self.waiting_llm = False
                return ""
            # 尚未请求 LLM：发出 prompt（答案在下回合 llmResp 中返回）
            if not self.waiting_llm:
                self.waiting_llm = True
                return ("请解答以下任务，只输出最终答案本身，不要输出任何解释：\n"
                        + st.phase_task)
            return ""  # 等待 llmResp

        # 无任务：前往最近的可用任务点接取
        valid = [t for t in st.tasks if t.is_valid]
        if not valid:
            return ""
        t = min(valid, key=lambda t: p.pos.cheb(t.pos))
        if p.pos.cheb(t.pos) <= 1:
            cmds[p.id] = {"action": "acceptTask"}
        else:
            self._move_toward(st, p, t.pos, cmds)
        return ""

    def _worker_day(self, st: GameState, w: Role, cmds: Dict[int, dict],
                    builder: bool) -> None:
        """工人：建造武器 > 卖矿 > 采矿；白天末尾回撤"""
        # 0. 白天末尾回撤
        if st.round_in_day >= NIGHT_PREPARE_ROUND:
            return  # 交给 _day 的统一回撤逻辑

        # 1. 建造武器工事（仅指定的建造工人）
        weapons = st.our_weapons()
        if builder and st.gold >= WEAPON_COST and len(weapons) < WEAPON_LIMIT:
            spot = self._find_build_spot(st, w)
            if spot is not None:
                if w.pos.cheb(spot) <= 1:
                    name = WEAPON_TYPES[len(weapons)]
                    cmds[w.id] = {"action": "build", "name": name,
                                  "targetPos": [spot.to_dict()]}
                    self.pending_builds[w.id] = (spot.x, spot.y)
                else:
                    self._move_toward(st, w, spot, cmds)
                return

        # 2. 背包矿石够多（或攒钱造武器）时去小贩处贩卖
        ore_counts = {o: w.backpack.count(o) for o in ORE_TYPES}
        total_ore = sum(ore_counts.values())
        vendors = st.find_zone("vendor")
        if vendors and (total_ore >= SELL_THRESHOLD
                        or (st.gold < WEAPON_COST and len(weapons) < WEAPON_LIMIT
                            and total_ore > 0)):
            vendor = vendors[0]
            if w.pos.cheb(vendor.pos) <= 1:
                name = self._best_sell(st, ore_counts)
                if name:
                    cmds[w.id] = {"action": "sell", "name": name,
                                  "num": ore_counts[name]}
            else:
                self._move_toward(st, w, vendor.pos, cmds)
            return

        # 3. 采集最近的矿
        mine = self._nearest_mine(st, w.pos)
        if mine is not None:
            if w.pos.cheb(mine.pos) <= 1:
                cmds[w.id] = {"action": "collect",
                              "targetPos": [mine.pos.to_dict()]}
            else:
                self._move_toward(st, w, mine.pos, cmds)

    def _best_sell(self, st: GameState, ore_counts: Dict[str, int]) -> Optional[str]:
        """选择卖出收益最高的矿石种类"""
        best, best_gain = None, 0
        for name, cnt in ore_counts.items():
            if cnt <= 0:
                continue
            gain = st.vendor_prices.get(name, 1) * cnt
            if gain > best_gain:
                best, best_gain = name, gain
        return best

    def _nearest_mine(self, st: GameState, frm: Pos):
        mines = [z for t in ORE_TYPES for z in st.find_zone(t)]
        return min(mines, key=lambda z: frm.cheb(z.pos), default=None)

    def _find_build_spot(self, st: GameState, w: Role) -> Optional[Pos]:
        """在基地周围环形区域寻找武器建造点（建造区不可知，失败点位进黑名单轮换尝试）"""
        station = st.our_station()
        if station is None:
            return None
        blocked = self._blocked_cells(st, exclude_id=w.id)
        cands: List[Pos] = []
        # 基地占 2x2，pos 为左上角（y 向上），覆盖 (x,y),(x+1,y),(x,y-1),(x+1,y-1)
        cx, cy = station.pos.x, station.pos.y
        for dx in range(-4, 6):
            for dy in range(-5, 5):
                p = Pos(cx + dx, cy + dy)
                if not (0 <= p.x < st.width and 0 <= p.y < st.height):
                    continue
                d = min(p.cheb(Pos(cx + ox, cy + oy))
                        for ox in (0, 1) for oy in (0, -1))
                if d < 2 or d > 4:  # 距基地 2~4 格的环带
                    continue
                if (p.x, p.y) in blocked or (p.x, p.y) in self.failed_builds:
                    continue
                cands.append(p)
        if not cands:
            return None
        # 优先选离工人近的，减少走位
        return min(cands, key=lambda p: w.pos.cheb(p))

    # ---------------------------------------------------------------- 黑夜

    def _night(self, st: GameState, cmds: Dict[int, dict]) -> None:
        weapons = sorted(st.our_weapons(), key=lambda r: r.id)
        fighters = st.our_units()
        robots = [r for r in st.robots.values() if r.abnormal != "dizzy"]
        used: Set[int] = set()

        for weapon in weapons:
            free = [f for f in fighters if f.id not in used]
            if not free:
                break
            c = min(free, key=lambda f: f.pos.cheb(weapon.pos))
            used.add(c.id)
            if c.pos.cheb(weapon.pos) <= 1:
                targets = self._pick_targets(weapon, robots)
                if targets:
                    cmds[weapon.id] = {
                        "action": "attack",
                        "controllerId": str(c.id),
                        "targetPos": [t.to_dict() for t in targets],
                    }
            else:
                self._move_toward(st, c, weapon.pos, cmds)

        # 未绑定武器的角色撤到基地附近
        station = st.our_station()
        if station is not None:
            for f in fighters:
                if f.id not in used and f.id not in cmds \
                        and f.pos.cheb(station.pos) > 2:
                    self._move_toward(st, f, station.pos, cmds)

    def _pick_targets(self, weapon: Role, robots: List[Role]) -> List[Pos]:
        """按武器类型选择攻击目标"""
        in_range = [r for r in robots
                    if weapon.pos.cheb(r.pos) <= weapon.attack_range]
        if not in_range:
            return []

        if weapon.role_type == "railgun":
            # 单发穿透：优先低血量可击杀，其次近
            t = min(in_range, key=lambda r: (r.health, weapon.pos.cheb(r.pos)))
            return [t.pos]

        if weapon.role_type == "gatling":
            # 多目标须落在同一 90° 锥形内（方向向量点积 >= 0）
            in_range.sort(key=lambda r: (weapon.pos.cheb(r.pos), r.health))
            anchor = in_range[0]
            av = (anchor.pos.x - weapon.pos.x, anchor.pos.y - weapon.pos.y)
            picked = [anchor]
            for r in in_range[1:]:
                if len(picked) >= weapon.level:
                    break
                bv = (r.pos.x - weapon.pos.x, r.pos.y - weapon.pos.y)
                if all((bv[0] * (p.pos.x - weapon.pos.x)
                        + bv[1] * (p.pos.y - weapon.pos.y)) >= 0
                       for p in picked):
                    picked.append(r)
            return [r.pos for r in picked]

        if weapon.role_type == "rocket":
            if weapon.cooldown > 0:
                return []
            # 选溅射收益最高的落点（周围 8 格机器人最多）
            def splash(r: Role) -> int:
                return 1 + sum(1 for o in robots
                               if o.id != r.id and o.pos.cheb(r.pos) <= 1)
            in_range.sort(key=lambda r: (-splash(r), r.health))
            return [r.pos for r in in_range[:weapon.level]]

        return []

    # ---------------------------------------------------------------- 通用

    def _move_toward(self, st: GameState, r: Role, to: Pos,
                     cmds: Dict[int, dict]) -> None:
        """朝目标移动一格；目标被占时移动到其周围空格"""
        blocked = self._blocked_cells(st, exclude_id=r.id)
        target = to
        if (to.x, to.y) in blocked:
            # 目标本身不可达（矿、小贩、武器等），走到其相邻空格即可
            nbrs = [Pos(to.x + dx, to.y + dy)
                    for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]
            nbrs = [p for p in nbrs
                    if 0 <= p.x < st.width and 0 <= p.y < st.height
                    and (p.x, p.y) not in blocked]
            if not nbrs:
                return
            target = min(nbrs, key=lambda p: r.pos.cheb(p))
        step = greedy_step(r.pos, target, blocked, st.width, st.height)
        if step is not None and step != r.pos:
            cmds[r.id] = {"action": "move", "targetPos": [step.to_dict()]}

    def _blocked_cells(self, st: GameState, exclude_id: int = -1) -> Set[Tuple[int, int]]:
        """所有已知阻挡格：中立元素、双方单位（除自己）、机器人；基地按 2x2 展开"""
        blocked: Set[Tuple[int, int]] = set()
        for z in st.zones:
            blocked.add((z.pos.x, z.pos.y))
        for r in list(st.our_roles.values()) + list(st.enemy_roles.values()):
            if r.id == exclude_id:
                continue
            blocked.add((r.pos.x, r.pos.y))
            if r.role_type == "station":  # 基地 2x2，pos 为左上角
                blocked.update({(r.pos.x + 1, r.pos.y),
                                (r.pos.x, r.pos.y - 1),
                                (r.pos.x + 1, r.pos.y - 1)})
        for r in st.robots.values():
            blocked.add((r.pos.x, r.pos.y))
        return blocked

    @staticmethod
    def _find(st: GameState, role_type: str) -> Optional[Role]:
        for r in st.our_units():
            if r.role_type == role_type:
                return r
        return None
