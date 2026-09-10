# -*- coding: utf-8 -*-
"""数据模型与请求解析。

将判题系统下发的 request JSON 解析为结构化的 GameState，
坐标统一使用切比雪夫距离（Chebyshev distance）。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---- 常量 -----------------------------------------------------------------

MAP_DAY_ROUNDS = 70      # 白天回合数
MAP_NIGHT_ROUNDS = 60    # 黑夜回合数
DAY_TOTAL = MAP_DAY_ROUNDS + MAP_NIGHT_ROUNDS  # 一天 130 回合

# 建筑 / 角色类型
WEAPON_TYPES = ("gatling", "railgun", "rocket")
BUILDING_TYPES = ("station", "gatling", "railgun", "rocket", "wall")
UNIT_TYPES = ("pioneer", "worker")
ORE_TYPES = ("stone", "iron", "copper")

# 武器建造成本 / 数量上限
WEAPON_COST = 25
WEAPON_LIMIT = 3


@dataclass(frozen=True)
class Pos:
    x: int
    y: int

    def cheb(self, other: "Pos") -> int:
        """切比雪夫距离"""
        return max(abs(self.x - other.x), abs(self.y - other.y))

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y}


@dataclass
class Role:
    id: int
    pos: Pos
    role_type: str
    health: int = 0
    attack_power: int = 0
    attack_range: int = 0
    level: int = 1
    cooldown: int = 0
    backpack_cap: int = 0
    backpack: List[str] = field(default_factory=list)
    abnormal: str = ""        # 机器人被眩晕时为 "dizzy"
    target_team: str = ""     # 机器人攻击的目标阵营


@dataclass
class Zone:
    pos: Pos
    neutral_type: str


@dataclass
class PlayerTask:
    task_type: str
    pos: Pos
    cooldown_rounds: int = 0
    score_reward: int = 0
    gold_reward: int = 0
    is_valid: bool = False
    timeout_rounds: int = 0


@dataclass
class GameState:
    round_no: int
    width: int
    height: int
    zones: List[Zone]
    team_type: str
    gold: int
    total_score: int
    tasks: List[PlayerTask]
    our_roles: Dict[int, Role]
    enemy_roles: Dict[int, Role]
    robots: Dict[int, Role]
    phase_task: str
    last_results: Dict[int, bool]
    summon_result: int
    llm_resp: str
    official_news: str
    folk_legends: str
    vendor_prices: Dict[str, int]
    shop_prices: Dict[str, int]
    errors: List[dict]

    # ---- 时间 ----
    @property
    def day_index(self) -> int:
        """第几天（从 1 开始）"""
        return (self.round_no - 1) // DAY_TOTAL + 1

    @property
    def round_in_day(self) -> int:
        """当天内的回合序号（0..129）"""
        return (self.round_no - 1) % DAY_TOTAL

    @property
    def is_day(self) -> bool:
        return self.round_in_day < MAP_DAY_ROUNDS

    # ---- 常用查询 ----
    def our_units(self) -> List[Role]:
        return [r for r in self.our_roles.values() if r.role_type in UNIT_TYPES]

    def our_weapons(self) -> List[Role]:
        return [r for r in self.our_roles.values() if r.role_type in WEAPON_TYPES]

    def our_station(self) -> Optional[Role]:
        for r in self.our_roles.values():
            if r.role_type == "station":
                return r
        return None

    def find_zone(self, neutral_type: str) -> List[Zone]:
        return [z for z in self.zones if z.neutral_type == neutral_type]


def _parse_pos(d: dict) -> Pos:
    return Pos(int(d.get("x", 0)), int(d.get("y", 0)))


def _parse_role(d: dict) -> Role:
    return Role(
        id=int(d.get("id", 0)),
        pos=_parse_pos(d.get("pos", {})),
        role_type=d.get("roleType", ""),
        health=int(d.get("health", 0)),
        attack_power=int(d.get("attackPower", 0)),
        attack_range=int(d.get("attackRange", 0)),
        level=int(d.get("level", 1) or 1),
        cooldown=int(d.get("cooldown", 0) or 0),
        backpack_cap=int(d.get("backPackCapability", 0) or 0),
        backpack=list(d.get("backpack", []) or []),
        abnormal=d.get("abnormalState", "") or "",
        target_team=d.get("targetTeam", "") or "",
    )


def _parse_task(d: dict) -> PlayerTask:
    return PlayerTask(
        task_type=d.get("taskType", ""),
        pos=_parse_pos(d.get("taskPosition", {})),
        cooldown_rounds=int(d.get("coldDownRounds", 0) or 0),
        score_reward=int(d.get("scoreReward", 0) or 0),
        gold_reward=int(d.get("goldReward", 0) or 0),
        is_valid=bool(d.get("isValid", False)),
        timeout_rounds=int(d.get("timeoutRounds", 0) or 0),
    )


def parse(data: dict) -> GameState:
    """将 request JSON dict 解析为 GameState"""
    map_info = data.get("mapInfo", {}) or {}
    team_our = data.get("teamOur", {}) or {}
    team_enemy = data.get("teamEnemy", {}) or {}
    robot = data.get("robot", {}) or {}
    news = data.get("worldNews", {}) or {}

    zones = [Zone(pos=_parse_pos(z.get("pos", {})),
                  neutral_type=z.get("neutralType", ""))
             for z in map_info.get("zones", []) or []]

    our_roles = {r.id: r for r in (_parse_role(x) for x in team_our.get("roles", []) or [])}
    enemy_roles = {r.id: r for r in (_parse_role(x) for x in team_enemy.get("roles", []) or [])}
    robots = {r.id: r for r in (_parse_role(x) for x in robot.get("roles", []) or [])}

    last_results = {int(k): bool(v)
                    for k, v in (data.get("lastRoundRoleActionResults", {}) or {}).items()}

    return GameState(
        round_no=int(data.get("roundNo", 0)),
        width=int(map_info.get("width", 41)),
        height=int(map_info.get("height", 32)),
        zones=zones,
        team_type=team_our.get("type", ""),
        gold=int(team_our.get("goldNum", 0) or 0),
        total_score=int(team_our.get("totalScore", 0) or 0),
        tasks=[_parse_task(t) for t in team_our.get("playerTasks", []) or []],
        our_roles=our_roles,
        enemy_roles=enemy_roles,
        robots=robots,
        phase_task=data.get("phaseTask", "") or "",
        last_results=last_results,
        summon_result=int(data.get("lastSummonTreasureResult", 0) or 0),
        llm_resp=data.get("llmResp", "") or "",
        official_news=news.get("officialNews", "") or "",
        folk_legends=news.get("folkLegends", "") or "",
        vendor_prices={i.get("name", ""): int(i.get("price", 0))
                       for i in data.get("vendorShopList", []) or []},
        shop_prices={i.get("name", ""): int(i.get("price", 0))
                     for i in data.get("weaponShopList", []) or []},
        errors=list(data.get("errors", []) or []),
    )
