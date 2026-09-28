"""
结构化 SQLite 存储层与计时器系统
"""

from pathlib import Path
import sqlite3
import threading
from typing import Any
from ..utils.timing import Timing

# players 表业务字段（不含自增主键 id）
_PLAYER_COLUMNS = (
    "player_name, xuid, qq, bind_time, rebind_time, unbind_time, unbind_by, "
    "original_qq, previous_qq, total_playtime, session_count, last_join_time, "
    "last_quit_time, is_banned, ban_time, ban_by, ban_reason"
)
_PLAYER_FIELDS = tuple(field.strip() for field in _PLAYER_COLUMNS.split(","))

_CREATE_PLAYERS_SQL = """
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_name TEXT NOT NULL,
        xuid TEXT,
        qq TEXT,
        bind_time INTEGER,
        rebind_time INTEGER,
        unbind_time INTEGER,
        unbind_by TEXT,
        original_qq TEXT,
        previous_qq TEXT,
        total_playtime INTEGER DEFAULT 0,
        session_count INTEGER DEFAULT 0,
        last_join_time INTEGER,
        last_quit_time INTEGER,
        is_banned INTEGER DEFAULT 0,
        ban_time INTEGER,
        ban_by TEXT,
        ban_reason TEXT
    )
"""

_INSERT_PLAYER_SQL = (
    f"INSERT INTO players ({_PLAYER_COLUMNS}) VALUES ({', '.join('?' * len(_PLAYER_FIELDS))})"
)


class Data:
    """提供基于 SQLite3 的高可靠、线程安全的游戏统计与绑定持久化"""

    def __init__(self, plugin: Any, data_folder: Path, logger: Any) -> None:
        self.plugin = plugin
        self.data_folder = data_folder
        self.logger = logger
        self.db_file = data_folder / "data.db"

        # 并发锁，确保主游戏线程与网络异步线程的写事务绝对互斥
        self._lock = threading.Lock()

        # 在线玩家计时缓存：身份键(XUID，未知时退回玩家名) -> (玩家记录 id, 本次计时起点)
        self._online_timers: dict[str, tuple[int, int]] = {}
        # 在线玩家单次会话绝对开始时间缓存：身份键 -> 时间戳
        self._session_start_times: dict[str, int] = {}
        self._last_timer_update: int = Timing.get_timestamp()

        self._init_db()

    def _execute_write(self, sql: str, params: tuple = ()) -> int | None:
        """线程安全的 SQL 写操作事务，返回自增主键（非插入语句为 None）"""
        with self._lock:
            conn = sqlite3.connect(self.db_file, check_same_thread=False)
            try:
                cursor = conn.execute(sql, params)
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                conn.rollback()
                self.logger.error(f"SQL写入失败: {sql}, 错误: {e}")
                raise e
            finally:
                conn.close()

    def _execute_read(self, sql: str, params: tuple = ()) -> list[tuple]:
        """线程安全的 SQL 读操作"""
        with self._lock:
            conn = sqlite3.connect(self.db_file, check_same_thread=False)
            try:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                return cursor.fetchall()
            except Exception as e:
                self.logger.error(f"SQL读取失败: {sql}, 错误: {e}")
                raise e
            finally:
                conn.close()

    def _init_db(self) -> None:
        """初始化 SQLite 数据库及对应表结构"""
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_schema()
        self._execute_write(_CREATE_PLAYERS_SQL)
        # XUID 是玩家的稳定身份键；玩家名可变，仅作展示与查询字段
        self._execute_write("CREATE UNIQUE INDEX IF NOT EXISTS idx_players_xuid ON players(xuid)")
        self._execute_write("CREATE INDEX IF NOT EXISTS idx_players_name ON players(player_name)")
        self.logger.info("SQLite 数据库初始化完成")

    @staticmethod
    def _normalize_xuid(xuid: Any) -> str | None:
        """统一空 XUID 为 NULL，避免唯一索引把未知 XUID 视为同一身份"""
        text = str(xuid).strip() if xuid else ""
        return text or None

    def _migrate_schema(self) -> None:
        """将旧版以 player_name 为主键的表结构迁移为 id 主键 + XUID 唯一索引"""
        columns = self._execute_read("PRAGMA table_info(players)")
        if not columns or any(column[1] == "id" for column in columns):
            return

        self.logger.info("检测到旧版 players 表结构，开始迁移为 XUID 身份模型...")
        legacy_rows = self._execute_read(
            f"SELECT rowid, {_PLAYER_COLUMNS} FROM players ORDER BY rowid"
        )
        merged_rows = self._merge_legacy_rows(legacy_rows)

        with self._lock:
            conn = sqlite3.connect(self.db_file, check_same_thread=False)
            try:
                conn.execute("ALTER TABLE players RENAME TO players_legacy")
                conn.execute(_CREATE_PLAYERS_SQL)
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_players_xuid ON players(xuid)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_players_name ON players(player_name)")
                conn.executemany(_INSERT_PLAYER_SQL, merged_rows)
                conn.execute("DROP TABLE players_legacy")
                conn.commit()
            except Exception as e:
                conn.rollback()
                self.logger.error(f"players 表结构迁移失败: {e}")
                raise e
            finally:
                conn.close()
        self.logger.info(f"players 表结构迁移完成，合并后共 {len(merged_rows)} 条记录")

    def _merge_legacy_rows(self, rows: list[tuple]) -> list[tuple]:
        """按 XUID 对旧记录分组（无 XUID 的按玩家名分组），消除改名产生的重复行"""
        groups: dict[tuple, list[dict[str, Any]]] = {}
        for row in rows:
            record: dict[str, Any] = dict(zip(_PLAYER_FIELDS, row[1:]))
            record["_order"] = row[0]
            record["xuid"] = self._normalize_xuid(record["xuid"])
            key = ("xuid", record["xuid"]) if record["xuid"] else ("name", record["player_name"])
            groups.setdefault(key, []).append(record)
        return [self._merge_player_group(group) for group in groups.values()]

    def _merge_player_group(self, group: list[dict[str, Any]]) -> tuple:
        """合并同一身份的多条旧记录：取最新名称，累计时长与会话，保留最新绑定与封禁状态"""
        latest = max(group, key=lambda d: (d["last_join_time"] or 0, d["_order"]))
        bound = [d for d in group if (d["qq"] or "").strip()]
        binding = (
            max(bound, key=lambda d: (d["rebind_time"] or d["bind_time"] or 0, d["_order"]))
            if bound
            else None
        )
        unbind = max(
            (d for d in group if d["unbind_time"]), key=lambda d: (d["unbind_time"], d["_order"]), default=None
        )
        ban = max(
            (d for d in group if d["is_banned"]), key=lambda d: (d["ban_time"] or 0, d["_order"]), default=None
        )

        bind_times = [d["bind_time"] for d in group if d["bind_time"]]
        rebind_times = [d["rebind_time"] for d in group if d["rebind_time"]]
        unbind_times = [d["unbind_time"] for d in group if d["unbind_time"]]
        join_times = [d["last_join_time"] for d in group if d["last_join_time"]]
        quit_times = [d["last_quit_time"] for d in group if d["last_quit_time"]]
        original_qq = next((d["original_qq"] for d in group if (d["original_qq"] or "").strip()), "")
        previous_qq = next(
            (d["previous_qq"] for d in reversed(group) if (d["previous_qq"] or "").strip()), ""
        )

        return (
            latest["player_name"],
            self._normalize_xuid(latest["xuid"]),
            binding["qq"] if binding else "",
            min(bind_times) if bind_times else None,
            max(rebind_times) if rebind_times else None,
            max(unbind_times) if unbind_times else None,
            unbind["unbind_by"] if unbind else "",
            original_qq,
            previous_qq,
            sum(d["total_playtime"] or 0 for d in group),
            sum(d["session_count"] or 0 for d in group),
            max(join_times) if join_times else None,
            max(quit_times) if quit_times else None,
            1 if ban else 0,
            ban["ban_time"] if ban else None,
            ban["ban_by"] if ban else "",
            ban["ban_reason"] if ban else "",
        )

    @staticmethod
    def _row_to_dict(row: tuple) -> dict[str, Any]:
        """按 players 字段顺序把数据行转换为业务字典"""
        return {
            "name": row[0],
            "xuid": row[1] or "",
            "qq": row[2] or "",
            "bind_time": row[3],
            "rebind_time": row[4],
            "unbind_time": row[5],
            "unbind_by": row[6] or "",
            "original_qq": row[7] or "",
            "previous_qq": row[8] or "",
            "total_playtime": row[9] or 0,
            "session_count": row[10] or 0,
            "last_join_time": row[11],
            "last_quit_time": row[12],
            "is_banned": bool(row[13]),
            "ban_time": row[14],
            "ban_by": row[15] or "",
            "ban_reason": row[16] or "",
        }

    @property
    def binding_data(self) -> dict[str, dict[str, Any]]:
        """
        获取与旧版数据字典结构等价的完整副本映射 (向后兼容专用)
        """
        rows = self._execute_read(f"SELECT {_PLAYER_COLUMNS} FROM players")
        return {row[0]: self._row_to_dict(row) for row in rows}

    def _find_player_id(self, player_name: str, player_xuid: str | None = None) -> tuple | None:
        """按 XUID 优先、玩家名兜底定位玩家记录，返回 (id, player_name, xuid, qq)"""
        xuid = self._normalize_xuid(player_xuid)
        if xuid:
            rows = self._execute_read(
                "SELECT id, player_name, xuid, qq FROM players WHERE xuid = ?", (xuid,)
            )
            if rows:
                return rows[0]

        rows = self._execute_read(
            "SELECT id, player_name, xuid, qq FROM players WHERE player_name = ? LIMIT 1",
            (player_name,),
        )
        return rows[0] if rows else None

    def is_player_bound(self, player_name: str, player_xuid: str | None = None) -> bool:
        """通过玩家ID或XUID判定玩家是否已绑定QQ"""
        xuid = self._normalize_xuid(player_xuid)
        if xuid:
            res = self._execute_read("SELECT qq FROM players WHERE xuid = ?", (xuid,))
            if res and res[0][0] and res[0][0].strip():
                return True

        res = self._execute_read(
            "SELECT qq FROM players WHERE player_name = ? AND qq IS NOT NULL AND qq != '' LIMIT 1",
            (player_name,),
        )
        return bool(res)

    def get_player_qq(self, player_name: str, player_xuid: str | None = None) -> str:
        """获取玩家绑定的QQ号，已知 XUID 时按 XUID 精确匹配"""
        xuid = self._normalize_xuid(player_xuid)
        if xuid:
            res = self._execute_read("SELECT qq FROM players WHERE xuid = ? LIMIT 1", (xuid,))
            if res:
                return res[0][0] if res[0][0] else ""

        res = self._execute_read(
            "SELECT qq FROM players WHERE player_name = ? AND qq IS NOT NULL AND qq != '' LIMIT 1",
            (player_name,),
        )
        return res[0][0] if res and res[0][0] else ""

    def get_qq_player(self, qq_number: str) -> str:
        """根据QQ号查找玩家ID"""
        res = self._execute_read("SELECT player_name FROM players WHERE qq = ?", (qq_number.strip(),))
        return res[0][0] if res and res[0][0] else ""

    def get_qq_player_history(self, qq_number: str) -> str:
        """查找原绑定或曾绑定过该QQ的游戏ID"""
        res = self._execute_read("SELECT player_name FROM players WHERE qq = ? OR original_qq = ?", (qq_number, qq_number))
        return res[0][0] if res and res[0][0] else ""

    def get_player_by_xuid(self, xuid: str) -> dict[str, Any]:
        """根据 XUID 获取玩家的所有记录字段"""
        rows = self._execute_read(
            f"SELECT {_PLAYER_COLUMNS} FROM players WHERE xuid = ? LIMIT 1",
            (self._normalize_xuid(xuid),),
        )
        return self._row_to_dict(rows[0]) if rows else {}

    def bind_player_qq(self, player_name: str, player_xuid: str, qq_number: str) -> bool:
        """将玩家角色与QQ进行持久化绑定"""
        qq_clean = qq_number.strip()
        if not qq_clean.isdigit() or not (5 <= len(qq_clean) <= 11):
            return False

        xuid = self._normalize_xuid(player_xuid)
        now = Timing.get_timestamp()
        record = self._find_player_id(player_name, xuid)
        # 同名记录属于其它 XUID 时另建记录，避免覆盖他人绑定与统计
        if record and xuid and record[2] and record[2] != xuid:
            record = None

        if record:
            record_id, old_qq = record[0], record[3]
            if old_qq and old_qq.strip():
                # 重新绑定
                self._execute_write(
                    """
                    UPDATE players SET qq = ?, xuid = ?, rebind_time = ?, previous_qq = ?
                    WHERE id = ?
                    """,
                    (qq_clean, xuid, now, old_qq, record_id),
                )
            else:
                # 首次绑定（曾经解绑过）
                self._execute_write(
                    """
                    UPDATE players SET qq = ?, xuid = ?, rebind_time = ?
                    WHERE id = ?
                    """,
                    (qq_clean, xuid, now, record_id),
                )
        else:
            # 全新记录
            self._execute_write(
                """
                INSERT INTO players (player_name, xuid, qq, bind_time, total_playtime, session_count)
                VALUES (?, ?, ?, ?, 0, 0)
                """,
                (player_name, xuid, qq_clean, now),
            )
        return True

    def unbind_player_qq(self, player_name: str, admin_name: str = "system") -> bool:
        """解除玩家的 QQ 绑定关系，保留其游戏统计"""
        res = self._execute_read("SELECT qq FROM players WHERE player_name = ?", (player_name,))
        if not res or not res[0][0]:
            return False

        original_qq = res[0][0]
        now = Timing.get_timestamp()

        self._execute_write(
            """
            UPDATE players SET qq = '', unbind_time = ?, unbind_by = ?, original_qq = ?
            WHERE player_name = ?
            """,
            (now, admin_name, original_qq, player_name),
        )
        return True

    def update_player_name(self, player_xuid: str, new_name: str) -> str:
        """按 XUID 同步玩家名（改名事件处理），返回改名前的旧名，未改名返回空串"""
        xuid = self._normalize_xuid(player_xuid)
        if not xuid:
            return ""

        res = self._execute_read("SELECT player_name FROM players WHERE xuid = ?", (xuid,))
        if not res:
            return ""

        old_name = res[0][0]
        if old_name == new_name:
            return ""

        # 玩家名不是唯一键，同名的其它账号记录不受影响
        self._execute_write("UPDATE players SET player_name = ? WHERE xuid = ?", (new_name, xuid))
        return old_name

    def update_player_join(self, player_name: str, player_xuid: str | None = None) -> None:
        """玩家进入游戏，按 XUID 归一化身份并记录最后上线时间、递增会话数"""
        now = Timing.get_timestamp()
        xuid = self._normalize_xuid(player_xuid)
        record = self._find_player_id(player_name, xuid)

        if record:
            record_id, record_xuid = record[0], record[2]
            # 同名记录属于其它 XUID 时不合并，另建独立记录
            if xuid and record_xuid and record_xuid != xuid:
                record = None
            else:
                self._execute_write(
                    """
                    UPDATE players
                    SET player_name = ?, xuid = ?, last_join_time = ?, session_count = session_count + 1
                    WHERE id = ?
                    """,
                    (player_name, xuid or record_xuid, now, record_id),
                )

        if not record:
            self._execute_write(
                """
                INSERT INTO players (player_name, xuid, last_join_time, session_count)
                VALUES (?, ?, ?, 1)
                """,
                (player_name, xuid, now),
            )

    def update_player_quit(self, player_name: str, player_xuid: str | None = None) -> None:
        """玩家离开游戏，记录最后退出时间"""
        record = self._find_player_id(player_name, player_xuid)
        if not record:
            return

        self._execute_write(
            "UPDATE players SET last_quit_time = ? WHERE id = ?",
            (Timing.get_timestamp(), record[0]),
        )

    def get_player_playtime_info(
        self, player_name: str, online_players: list[Any], player_xuid: str | None = None
    ) -> dict[str, Any]:
        """获取玩家当前在线时长的结算与绑定历史结构数据"""
        data = self.binding_data.get(player_name)
        if not data:
            return {}

        now = Timing.get_timestamp()
        total_playtime = data.get("total_playtime", 0)

        is_online = False
        current_session_time = 0

        # 如果玩家当前在计时器记录中，进行增量求和
        timer = self._online_timers.get(self._identity_key(player_name, player_xuid))
        if timer:
            is_online = True
            current_session_time = max(0, now - timer[1])

        return {
            "total_playtime": total_playtime + current_session_time,
            "session_count": data.get("session_count", 0),
            "last_join_time": data.get("last_join_time"),
            "last_quit_time": data.get("last_quit_time"),
            "is_online": is_online,
            "current_session_time": current_session_time,
            "bind_time": data.get("bind_time"),
        }

    def is_player_banned(self, player_name: str, player_xuid: str | None = None) -> bool:
        """检查玩家是否处于封禁状态，已知 XUID 时按 XUID 精确匹配"""
        xuid = self._normalize_xuid(player_xuid)
        if xuid:
            res = self._execute_read("SELECT is_banned FROM players WHERE xuid = ? LIMIT 1", (xuid,))
            if res:
                return bool(res[0][0])

        res = self._execute_read("SELECT is_banned FROM players WHERE player_name = ?", (player_name,))
        return bool(res and res[0][0])

    def ban_player(self, player_name: str, admin_name: str = "system", reason: str = "") -> bool:
        """封禁玩家，并强制解除其 QQ 绑定"""
        now = Timing.get_timestamp()
        res = self._execute_read("SELECT id FROM players WHERE player_name = ? LIMIT 1", (player_name,))

        if res:
            # 解绑与封禁合并为一次写入，历史绑定号保留在 original_qq
            self._execute_write(
                """
                UPDATE players
                SET is_banned = 1, ban_time = ?, ban_by = ?, ban_reason = ?,
                    original_qq = CASE WHEN qq IS NOT NULL AND qq != '' THEN qq ELSE original_qq END,
                    qq = '', unbind_time = ?, unbind_by = ?
                WHERE player_name = ?
                """,
                (now, admin_name, reason or "管理员封禁", now, admin_name, player_name),
            )
        else:
            self._execute_write(
                """
                INSERT INTO players (player_name, is_banned, ban_time, ban_by, ban_reason, qq, unbind_time, unbind_by)
                VALUES (?, 1, ?, ?, ?, '', ?, ?)
                """,
                (player_name, now, admin_name, reason or "管理员封禁", now, admin_name),
            )
        return True

    def unban_player(self, player_name: str, admin_name: str = "system") -> bool:
        """解除玩家封禁状态"""
        res = self._execute_read("SELECT is_banned FROM players WHERE player_name = ?", (player_name,))
        if not res or not res[0][0]:
            return False

        now = Timing.get_timestamp()
        self._execute_write(
            """
            UPDATE players SET is_banned = 0, ban_time = NULL, ban_by = NULL, ban_reason = NULL
            WHERE player_name = ?
            """,
            (player_name,),
        )
        return True

    def get_banned_players(self) -> list[dict[str, Any]]:
        """获取所有封禁用户的列表数据"""
        rows = self._execute_read("SELECT player_name, ban_time, ban_by, ban_reason FROM players WHERE is_banned = 1")
        return [
            {
                "name": r[0],
                "ban_time": r[1],
                "ban_by": r[2] or "unknown",
                "ban_reason": r[3] or "无原因",
            }
            for r in rows
        ]

    def get_player_binding_history(self, player_name: str) -> dict[str, Any]:
        """获取玩家当前绑定的分类历史字典数据"""
        data = self.binding_data.get(player_name)
        if not data:
            return {}

        is_bound = bool(data.get("qq", "").strip())
        history = {
            "current_qq": data.get("qq", ""),
            "is_bound": is_bound,
            "bind_time": data.get("bind_time"),
            "unbind_time": data.get("unbind_time"),
            "rebind_time": data.get("rebind_time"),
            "unbind_by": data.get("unbind_by"),
            "original_qq": data.get("original_qq"),
            "previous_qq": data.get("previous_qq"),
            "total_playtime": data.get("total_playtime", 0),
            "session_count": data.get("session_count", 0),
        }

        if is_bound:
            history["status"] = "重新绑定" if data.get("rebind_time") else "已绑定"
        else:
            history["status"] = "已解绑" if data.get("unbind_time") else "从未绑定"

        return history

    def get_complete_player_binding_status(self, player_name: str, player_xuid: str) -> dict[str, Any]:
        """双向核验玩家绑定数据一致性"""
        result = {
            "is_bound": False,
            "qq_number": "",
            "binding_source": "",
            "data_consistent": True,
            "issues": [],
        }

        # 1. 查找玩家名下的绑定
        res_name = self._execute_read("SELECT qq FROM players WHERE player_name = ?", (player_name,))
        name_qq = res_name[0][0] if res_name else ""
        name_bound = bool(name_qq and name_qq.strip())

        # 2. 查找 XUID 名下的绑定
        res_xuid = self._execute_read("SELECT qq, player_name FROM players WHERE xuid = ?", (player_xuid,))
        xuid_qq = res_xuid[0][0] if res_xuid else ""
        xuid_bound = bool(xuid_qq and xuid_qq.strip())

        if name_bound and xuid_bound:
            if name_qq == xuid_qq:
                result["is_bound"] = True
                result["qq_number"] = name_qq
                result["binding_source"] = "both"
            else:
                result["is_bound"] = False
                result["data_consistent"] = False
                result["issues"].append(f"QQ号不一致: 玩家名对应 {name_qq}, XUID对应 {xuid_qq}")
        elif name_bound:
            result["is_bound"] = True
            result["qq_number"] = name_qq
            result["binding_source"] = "name"
            result["issues"].append("仅玩家名绑定，XUID无关联数据")
        elif xuid_bound:
            result["is_bound"] = True
            result["qq_number"] = xuid_qq
            result["binding_source"] = "xuid"
            result["issues"].append("仅XUID绑定，当前游戏ID无关联数据")
        else:
            result["is_bound"] = False
            result["binding_source"] = "none"

        return result

    # ================= 定时任务计时系统 =================

    @staticmethod
    def _identity_key(player_name: str, player_xuid: str | None = None) -> str:
        """计时身份键：优先 XUID，未知 XUID 时退回玩家名"""
        return Data._normalize_xuid(player_xuid) or player_name

    def _ensure_player_record(self, player_name: str, player_xuid: str | None = None) -> int | None:
        """确保玩家记录存在并返回其 id，优先匹配已有 XUID 记录"""
        xuid = self._normalize_xuid(player_xuid)
        record = self._find_player_id(player_name, xuid)
        # 同名记录属于其它 XUID 时另建独立记录，避免两个账号共用一个计时
        if record and xuid and record[2] and record[2] != xuid:
            record = None

        if record:
            if xuid and not record[2]:
                self._execute_write("UPDATE players SET xuid = ? WHERE id = ?", (xuid, record[0]))
            return record[0]

        return self._execute_write(
            """
            INSERT INTO players (player_name, xuid, total_playtime, session_count)
            VALUES (?, ?, 0, 0)
            """,
            (player_name, xuid),
        )

    def start_player_timer(self, player_name: str, player_xuid: str | None = None) -> None:
        """开始对新加入游戏的角色进行在线累时"""
        key = self._identity_key(player_name, player_xuid)
        if key in self._online_timers:
            return

        now = Timing.get_timestamp()
        player_id = self._ensure_player_record(player_name, player_xuid)
        if player_id is None:
            return

        self._online_timers[key] = (player_id, now)
        self._session_start_times.setdefault(key, now)

    def stop_player_timer(self, player_name: str, player_xuid: str | None = None) -> None:
        """结算并移除角色当前的在线时长"""
        key = self._identity_key(player_name, player_xuid)
        if key not in self._online_timers:
            key = player_name
        self._settle_timer(key)

    def _settle_timer(self, key: str) -> None:
        """按身份键结算在线时长并落盘到对应玩家记录，随后移除计时"""
        timer = self._online_timers.pop(key, None)
        if timer is None:
            return

        player_id, start_time = timer
        duration = max(0, Timing.get_timestamp() - start_time)
        if duration > 0:
            self._execute_write(
                "UPDATE players SET total_playtime = total_playtime + ? WHERE id = ?",
                (duration, player_id),
            )
        self._session_start_times.pop(key, None)

    def get_session_start_time(self, player_name: str, player_xuid: str | None = None) -> int | None:
        """获取玩家本次会话的绝对开始时间"""
        key = self._identity_key(player_name, player_xuid)
        start_time = self._session_start_times.get(key)
        if start_time is None:
            start_time = self._session_start_times.get(player_name)
        return start_time

    def update_online_timers(self, online_players: list[Any]) -> None:
        """每分钟自愈结算：增加新在线角色的累时，剔除离线角色的计时"""
        now = Timing.get_timestamp()
        active_keys = set()

        for p in online_players:
            if hasattr(p, "name") and hasattr(p, "xuid"):
                key = self._identity_key(p.name, p.xuid)
                active_keys.add(key)
                # 对新增上线角色开启计时
                if key not in self._online_timers:
                    self.start_player_timer(p.name, p.xuid)

        # 收集并结算已离线角色的时长
        offline_keys = [key for key in self._online_timers if key not in active_keys]
        for key in offline_keys:
            self._settle_timer(key)

        # 每 5 分钟（300秒）将当前在线玩家已累积的时长安全同步落盘一次
        if now - self._last_timer_update >= 300:
            self._save_timer_progress()
            self._last_timer_update = now

    def _save_timer_progress(self) -> None:
        """批量同步当前在线未下线角色的部分累计时长进库"""
        now = Timing.get_timestamp()
        for key, (player_id, start_time) in list(self._online_timers.items()):
            duration = max(0, now - start_time)
            if duration > 0:
                self._execute_write(
                    "UPDATE players SET total_playtime = total_playtime + ? WHERE id = ?",
                    (duration, player_id),
                )
                # 重置该玩家的计时起点为当前，防止重复计算
                self._online_timers[key] = (player_id, now)
        self.logger.info("已保存当前在线玩家的阶段计时进度")

    def cleanup_timer_system(self) -> None:
        """插件禁用时强制结算所有计时器并将时长落盘"""
        if self._online_timers:
            for key in list(self._online_timers.keys()):
                self._settle_timer(key)
            self.logger.info("在线计时器已全部安全离线结算")

    def save_data(self) -> None:
        """向后兼容存盘接口（SQLite3 每次写入即落盘，此方法中仅保存当前计时进度）"""
        self._save_timer_progress()
