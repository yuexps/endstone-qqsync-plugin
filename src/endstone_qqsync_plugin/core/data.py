"""
结构化 SQLite 存储层与计时器系统
"""

import json
from pathlib import Path
import sqlite3
import threading
from typing import Any
from ..utils.timing import Timing


class Data:
    """提供基于 SQLite3 的高可靠、线程安全的游戏统计与绑定持久化"""

    def __init__(self, plugin: Any, data_folder: Path, logger: Any) -> None:
        self.plugin = plugin
        self.data_folder = data_folder
        self.logger = logger
        self.db_file = data_folder / "data.db"

        # 并发锁，确保主游戏线程与网络异步线程的写事务绝对互斥
        self._lock = threading.Lock()

        # 在线玩家计时器内存缓存
        self._online_timer_start_times: dict[str, int] = {}
        # 在线玩家单次会话绝对开始时间缓存
        self._session_start_times: dict[str, int] = {}
        self._last_timer_update: int = Timing.get_timestamp()

        self._init_db()
        self._migrate_old_json()

    def _execute_write(self, sql: str, params: tuple = ()) -> None:
        """线程安全的 SQL 写操作事务"""
        with self._lock:
            conn = sqlite3.connect(self.db_file, check_same_thread=False)
            try:
                conn.execute(sql, params)
                conn.commit()
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
        self._execute_write(
            """
            CREATE TABLE IF NOT EXISTS players (
                player_name TEXT PRIMARY KEY,
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
        )
        self.logger.info("SQLite 数据库初始化完成")

    def _migrate_old_json(self) -> None:
        """从旧的 data.json 增量迁移数据"""
        old_file = self.data_folder / "data.json"
        if not old_file.exists():
            return

        self.logger.info("检测到旧版 JSON 数据，开始自动迁移至 SQLite3...")
        try:
            with open(old_file, "r", encoding="utf-8") as f:
                old_data = json.load(f)

            for name, d in old_data.items():
                self._execute_write(
                    """
                    INSERT OR REPLACE INTO players (
                        player_name, xuid, qq, bind_time, rebind_time, unbind_time, 
                        unbind_by, original_qq, previous_qq, total_playtime, 
                        session_count, last_join_time, last_quit_time, 
                        is_banned, ban_time, ban_by, ban_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        d.get("xuid", ""),
                        d.get("qq", ""),
                        d.get("bind_time"),
                        d.get("rebind_time"),
                        d.get("unbind_time"),
                        d.get("unbind_by", ""),
                        d.get("original_qq", ""),
                        d.get("previous_qq", ""),
                        d.get("total_playtime", 0),
                        d.get("session_count", 0),
                        d.get("last_join_time"),
                        d.get("last_quit_time"),
                        1 if d.get("is_banned") else 0,
                        d.get("ban_time"),
                        d.get("ban_by", ""),
                        d.get("ban_reason", ""),
                    ),
                )

            # 备份原 JSON 文件
            backup_file = old_file.with_suffix(".json.bak")
            old_file.rename(backup_file)
            self.logger.info(f"数据迁移成功，旧文件已备份为: {backup_file.name}")
        except Exception as e:
            self.logger.error(f"数据迁移失败: {e}")

    @property
    def binding_data(self) -> dict[str, dict[str, Any]]:
        """
        获取与旧版数据字典结构等价的完整副本映射 (向后兼容专用)
        """
        rows = self._execute_read("SELECT * FROM players")
        data_dict: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = row[0]
            data_dict[name] = {
                "name": name,
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
        return data_dict

    def is_player_bound(self, player_name: str, player_xuid: str | None = None) -> bool:
        """通过玩家ID或XUID判定玩家是否已绑定QQ"""
        if player_xuid:
            res = self._execute_read("SELECT qq FROM players WHERE xuid = ?", (player_xuid,))
            if res and res[0][0] and res[0][0].strip():
                return True

        res = self._execute_read("SELECT qq FROM players WHERE player_name = ?", (player_name,))
        return bool(res and res[0][0] and res[0][0].strip())

    def get_player_qq(self, player_name: str) -> str:
        """获取玩家绑定的QQ号"""
        res = self._execute_read("SELECT qq FROM players WHERE player_name = ?", (player_name,))
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
        rows = self._execute_read("SELECT player_name FROM players WHERE xuid = ?", (xuid,))
        if rows:
            return self.binding_data.get(rows[0][0], {})
        return {}

    def bind_player_qq(self, player_name: str, player_xuid: str, qq_number: str) -> bool:
        """将玩家角色与QQ进行持久化绑定"""
        qq_clean = qq_number.strip()
        if not qq_clean.isdigit() or not (5 <= len(qq_clean) <= 11):
            return False

        # 检查是否已有记录
        res = self._execute_read("SELECT qq FROM players WHERE player_name = ?", (player_name,))
        now = Timing.get_timestamp()

        if res:
            old_qq = res[0][0]
            if old_qq and old_qq.strip():
                # 重新绑定
                self._execute_write(
                    """
                    UPDATE players SET qq = ?, xuid = ?, rebind_time = ?, previous_qq = ?
                    WHERE player_name = ?
                    """,
                    (qq_clean, player_xuid, now, old_qq, player_name),
                )
            else:
                # 首次绑定（曾经解绑过）
                self._execute_write(
                    """
                    UPDATE players SET qq = ?, xuid = ?, rebind_time = ?
                    WHERE player_name = ?
                    """,
                    (qq_clean, player_xuid, now, player_name),
                )
        else:
            # 全新记录
            self._execute_write(
                """
                INSERT INTO players (player_name, xuid, qq, bind_time, total_playtime, session_count)
                VALUES (?, ?, ?, ?, 0, 0)
                """,
                (player_name, player_xuid, qq_clean, now),
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

    def update_player_name(self, old_name: str, new_name: str, xuid: str) -> bool:
        """更新玩家名称（改名事件处理）"""
        res = self._execute_read("SELECT qq FROM players WHERE player_name = ?", (old_name,))
        if not res:
            return False

        self._execute_write(
            "UPDATE players SET player_name = ? WHERE player_name = ?",
            (new_name, old_name),
        )
        return True

    def update_player_join(self, player_name: str, player_xuid: str | None = None) -> None:
        """玩家进入游戏，记录最后上线时间并递增会话数"""
        now = Timing.get_timestamp()
        res = self._execute_read("SELECT session_count FROM players WHERE player_name = ?", (player_name,))

        if res:
            self._execute_write(
                """
                UPDATE players SET last_join_time = ?, session_count = session_count + 1, xuid = ?
                WHERE player_name = ?
                """,
                (now, player_xuid or "", player_name),
            )
        else:
            self._execute_write(
                """
                INSERT INTO players (player_name, xuid, last_join_time, session_count)
                VALUES (?, ?, ?, 1)
                """,
                (player_name, player_xuid or "", now),
            )

    def update_player_quit(self, player_name: str) -> None:
        """玩家离开游戏，记录最后退出时间"""
        now = Timing.get_timestamp()
        self._execute_write(
            "UPDATE players SET last_quit_time = ? WHERE player_name = ?",
            (now, player_name),
        )

    def get_player_playtime_info(self, player_name: str, online_players: list[Any]) -> dict[str, Any]:
        """获取玩家当前在线时长的结算与绑定历史结构数据"""
        data = self.binding_data.get(player_name)
        if not data:
            return {}

        now = Timing.get_timestamp()
        total_playtime = data.get("total_playtime", 0)

        is_online = False
        current_session_time = 0

        # 如果玩家当前在计时器记录中，进行增量求和
        if player_name in self._online_timer_start_times:
            is_online = True
            start_time = self._online_timer_start_times[player_name]
            current_session_time = max(0, now - start_time)

        return {
            "total_playtime": total_playtime + current_session_time,
            "session_count": data.get("session_count", 0),
            "last_join_time": data.get("last_join_time"),
            "last_quit_time": data.get("last_quit_time"),
            "is_online": is_online,
            "current_session_time": current_session_time,
            "bind_time": data.get("bind_time"),
        }

    def is_player_banned(self, player_name: str) -> bool:
        """检查玩家是否处于封禁状态"""
        res = self._execute_read("SELECT is_banned FROM players WHERE player_name = ?", (player_name,))
        return bool(res and res[0][0])

    def ban_player(self, player_name: str, admin_name: str = "system", reason: str = "") -> bool:
        """封禁玩家，并强制解除其 QQ 绑定"""
        now = Timing.get_timestamp()
        res = self._execute_read("SELECT qq FROM players WHERE player_name = ?", (player_name,))

        # 解绑与封禁合并为一个事务写入
        original_qq = res[0][0] if res else ""
        sql_ban = """
            INSERT INTO players (player_name, is_banned, ban_time, ban_by, ban_reason, qq, unbind_time, unbind_by, original_qq)
            VALUES (?, 1, ?, ?, ?, '', ?, ?, ?)
            ON CONFLICT(player_name) DO UPDATE SET
                is_banned = 1, ban_time = excluded.ban_time, ban_by = excluded.ban_by, ban_reason = excluded.ban_reason,
                qq = '', unbind_time = excluded.unbind_time, unbind_by = excluded.unbind_by, original_qq = CASE WHEN qq != '' THEN qq ELSE original_qq END
        """
        self._execute_write(sql_ban, (player_name, now, admin_name, reason or "管理员封禁", now, admin_name, original_qq))
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

    def start_player_timer(self, player_name: str, player_xuid: str | None = None) -> None:
        """开始对新加入游戏的角色进行在线累时"""
        if player_name in self._online_timer_start_times:
            return

        now = Timing.get_timestamp()
        self._online_timer_start_times[player_name] = now
        if player_name not in self._session_start_times:
            self._session_start_times[player_name] = now

        # 确保玩家记录在库
        res = self._execute_read("SELECT player_name FROM players WHERE player_name = ?", (player_name,))
        if not res:
            self._execute_write(
                """
                INSERT INTO players (player_name, xuid, total_playtime, session_count)
                VALUES (?, ?, 0, 0)
                """,
                (player_name, player_xuid or ""),
            )

    def stop_player_timer(self, player_name: str) -> None:
        """结算并移除角色当前的在线时长"""
        if player_name not in self._online_timer_start_times:
            return

        start_time = self._online_timer_start_times[player_name]
        now = Timing.get_timestamp()
        duration = max(0, now - start_time)

        if duration > 0:
            self._execute_write(
                "UPDATE players SET total_playtime = total_playtime + ? WHERE player_name = ?",
                (duration, player_name),
            )

        del self._online_timer_start_times[player_name]
        self._session_start_times.pop(player_name, None)

    def update_online_timers(self, online_players: list[Any]) -> None:
        """每分钟自愈结算：增加新在线角色的累时，剔除离线角色的计时"""
        now = Timing.get_timestamp()
        active_names = set()

        for p in online_players:
            if hasattr(p, "name") and hasattr(p, "xuid"):
                active_names.add(p.name)
                # 对新增上线角色开启计时
                if p.name not in self._online_timer_start_times:
                    self.start_player_timer(p.name, p.xuid)

        # 收集并结算已离线角色的时长
        offline_names = [name for name in self._online_timer_start_times if name not in active_names]
        for name in offline_names:
            self.stop_player_timer(name)

        # 每 5 分钟（300秒）将当前在线玩家已累积的时长安全同步落盘一次
        if now - self._last_timer_update >= 300:
            self._save_timer_progress()
            self._last_timer_update = now

    def _save_timer_progress(self) -> None:
        """批量同步当前在线未下线角色的部分累计时长进库"""
        now = Timing.get_timestamp()
        for name, start_time in list(self._online_timer_start_times.items()):
            duration = max(0, now - start_time)
            if duration > 0:
                self._execute_write(
                    "UPDATE players SET total_playtime = total_playtime + ? WHERE player_name = ?",
                    (duration, name),
                )
                # 重置该玩家的计时起点为当前，防止重复计算
                self._online_timer_start_times[name] = now
        self.logger.info("已保存当前在线玩家的阶段计时进度")

    def cleanup_timer_system(self) -> None:
        """插件禁用时强制结算所有计时器并将时长落盘"""
        if self._online_timer_start_times:
            for name in list(self._online_timer_start_times.keys()):
                self.stop_player_timer(name)
            self.logger.info("在线计时器已全部安全离线结算")

    def save_data(self) -> None:
        """向后兼容存盘接口（SQLite3 每次写入即落盘，此方法中仅保存当前计时进度）"""
        self._save_timer_progress()
