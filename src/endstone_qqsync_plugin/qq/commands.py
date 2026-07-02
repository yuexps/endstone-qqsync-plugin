"""
QQ 群内命令处理器与分发系统
"""

import asyncio
import datetime
import html
import shlex
from typing import Any
from ..utils import format_playtime, format_timestamp, Timing


async def sync_group_card_for_all(ws_client: Any, user_id: int, card: str) -> None:
    """在所有配置的群聊中修改对应QQ号的群昵称"""
    groups = ws_client.plugin.config_manager.groups
    for group in groups:
        try:
            payload = {
                "action": "set_group_card",
                "params": {
                    "group_id": group["id"],
                    "user_id": user_id,
                    "card": card,
                },
                "echo": f"set_group_card:{user_id}:{card}:{group['id']}",
            }
            await ws_client.send_message(payload)
        except Exception as e:
            ws_client.plugin.logger.error(f"在群 {group['id']} 中设置名片失败: {e}")


class GroupCommandHandler:
    """群内命令分发路由器，使用字典映射表取代冗长的 if-elif 判断分支"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.logger = plugin.logger

        # 命令路由表定义: {指令名: (处理方法, 是否需要管理员, 说明描述)}
        self.commands: dict[str, tuple[Any, bool, str]] = {}
        self._register_routes()

    def _register_routes(self) -> None:
        """注册所有群内可执行的命令路由"""
        self.commands = {
            "help": (self.cmd_help, False, "显示本帮助信息"),
            "list": (self.cmd_list, False, "查看游戏在线玩家"),
            "tps": (self.cmd_tps, False, "查看服务器性能指标"),
            "info": (self.cmd_info, False, "查看系统与硬件参数"),
            "bind": (self.cmd_bind, False, "查看QQ绑定状态"),
            "verify": (self.cmd_verify, False, "验证并激活QQ绑定"),
            # 管理员指令
            "cmd": (self.cmd_cmd, True, "执行后台控制台命令"),
            "check": (self.cmd_check, True, "查询玩家详细档案"),
            "bindqq": (self.cmd_bindqq, True, "强制绑定玩家"),
            "unbindqq": (self.cmd_unbindqq, True, "强行解除玩家绑定"),
            "ban": (self.cmd_ban, True, "封禁玩家游戏资格"),
            "unban": (self.cmd_unban, True, "解封玩家游戏资格"),
            "banlist": (self.cmd_banlist, True, "查看封禁黑名单"),
            "tog_qq": (self.cmd_tog_qq, True, "开启/关闭QQ侧同步"),
            "tog_game": (self.cmd_tog_game, True, "开启/关闭游戏侧同步"),
            "reload": (self.cmd_reload, True, "重载 TOML 配置文件"),
        }

    async def handle_command(self, ws_client: Any, user_id: int, raw_msg: str, display_name: str, group_id: int) -> None:
        """解析并路由单条群命令"""
        # 使用 shlex 进行词法分词，支持双引号包含带空格的参数
        try:
            parts = shlex.split(raw_msg.strip())
        except Exception:
            parts = raw_msg.strip().split()

        if not parts:
            return

        cmd_trigger = parts[0]
        cmd_name = cmd_trigger[1:] if cmd_trigger.startswith("/") else cmd_trigger
        args = parts[1:]

        route = self.commands.get(cmd_name)
        if not route:
            # 未注册命令不做任何回复
            return

        handler_method, need_admin, desc = route

        # 权限校验
        is_admin = str(user_id) in self.plugin.config_manager.admins
        if need_admin and not is_admin:
            reply = "[错误] 该命令仅限管理员使用！"
            await ws_client.send_group_message(group_id, f"@{display_name}\n{reply}")
            return

        try:
            # 执行命令并异步获取回复内容
            reply = await handler_method(ws_client, user_id, args, group_id, display_name)
            if reply:
                # 统一 At 消息回包
                await ws_client.send_group_message(
                    group_id,
                    [
                        {"type": "at", "data": {"qq": str(user_id)}},
                        {"type": "text", "data": {"text": f"\n{reply}"}},
                    ],
                )
        except Exception as e:
            self.logger.error(f"群命令 /{cmd_name} 执行失败: {e}")
            await ws_client.send_group_message(group_id, f"@{display_name}\n[错误] 指令执行失败: {e}")

    # ================= 群指令具体业务处理方法 =================

    async def cmd_help(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/help 命令"""
        is_admin = str(user_id) in self.plugin.config_manager.admins
        return self.plugin.config_manager.get_help_text(is_admin)

    async def cmd_list(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/list 命令"""
        online_players = self.plugin.server.online_players
        if not online_players:
            return "当前服务器内没有玩家在线。"

        lines = [f"在线玩家 ({len(online_players)}/{self.plugin.server.max_players})："]
        for p in online_players:
            try:
                ping = f"{p.ping}ms"
            except Exception:
                ping = "N/A"
            bound = self.plugin.data_manager.is_player_bound(p.name, p.xuid)
            status = "已绑定" if bound else "未绑定QQ"
            lines.append(f" • {p.name} [延迟: {ping}] [{status}]")

        return "\n".join(lines)

    async def cmd_tps(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/tps 命令"""
        try:
            srv = self.plugin.server
            reply = (
                f"服务器实时性能状态指标：\n"
                f" • 当前 TPS: {srv.current_tps:.2f} / 20.0\n"
                f" • 平均 TPS: {srv.average_tps:.2f} / 20.0\n"
                f" • 当前 MSPT: {srv.current_mspt:.2f}ms\n"
                f" • 平均 MSPT: {srv.average_mspt:.2f}ms\n"
                f" • 当前 Tick 使用率: {srv.current_tick_usage:.1f}%\n"
                f" • 平均 Tick 使用率: {srv.average_tick_usage:.1f}%"
            )
            return reply
        except Exception as e:
            self.logger.error(f"获取 TPS 发生错误: {e}")
            return "[错误] 无法抓取服务器 TPS 性能数据"

    async def cmd_info(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/info 命令"""
        try:
            from ..utils.system import get_system_info_dict

            srv = self.plugin.server
            sys_info = get_system_info_dict()

            uptime = Timing.calculate_uptime(srv.start_time)
            total_bindings = len(self.plugin.data_manager.binding_data)

            reply = (
                f"=== 服务器运行详情 ===\n"
                f" • 核心版本: Endstone {srv.version} (MC {srv.minecraft_version})\n"
                f" • 启动时间: {Timing.format_datetime(srv.start_time)}\n"
                f" • 持续运行: {uptime['uptime_str']}\n"
                f" • 在线状态: {len(srv.online_players)}/{srv.max_players} 玩家\n"
                f" • 总绑定记录数: {total_bindings}\n\n"
                f"=== 系统硬件详情 ===\n"
                f" • 操作系统: {sys_info['os']}\n"
                f" • CPU型号: {sys_info['cpu']['model']}\n"
                f" • 核心架构: {sys_info['cpu']['physical_cores']}核 {sys_info['cpu']['logical_cores']}线程\n"
                f" • CPU负载: {sys_info['cpu']['usage_percent']:.1f}%\n"
                f" • 内存使用: {sys_info['memory']['used_gb']:.1f}GB / {sys_info['memory']['total_gb']:.1f}GB ({sys_info['memory']['percent']:.1f}%)"
            )
            return reply
        except Exception as e:
            self.logger.error(f"生成系统指标报告失败: {e}")
            return "[错误] 服务器信息生成失败"

    async def cmd_bind(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/bind 命令"""
        qq_str = str(user_id)
        bound_player = self.plugin.data_manager.get_qq_player(qq_str)
        if not bound_player:
            return "您的 QQ 尚未绑定任何游戏角色。\n[引导] 请在游戏内输入 `/bindqq` 命令发起绑定流程。"

        info = self.plugin.data_manager.get_player_playtime_info(bound_player, self.plugin.server.online_players)

        banned = self.plugin.data_manager.is_player_banned(bound_player)
        status = "封禁中" if banned else ("在线" if info.get("is_online") else "离线")

        reply = (
            f"=== 您的 QQ 绑定档案 ===\n"
            f" • 绑定角色: {bound_player}\n"
            f" • 当前状态: {status}\n"
            f" • 累积游戏时长: {format_playtime(info.get('total_playtime', 0))}\n"
            f" • 登录服务器次数: {info.get('session_count', 0)} 次"
        )
        return reply

    async def cmd_verify(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/verify <验证码> 命令"""
        if len(args) != 1:
            return "[错误] 用法: /verify <6位验证码>"

        code = args[0]
        qq_str = str(user_id)

        if qq_str not in self.plugin.verification_manager.verification_codes:
            return "[错误] 未找到您的待验证申请，请先在游戏内使用 `/bindqq` 触发"

        cached = self.plugin.verification_manager.verification_codes[qq_str]
        player_name = cached.get("player_name")
        if not player_name:
            return "[错误] 验证信息结构损坏"

        # 检索游戏内在线玩家
        target_player = None
        for p in self.plugin.server.online_players:
            if p.name == player_name:
                target_player = p
                break

        if not target_player:
            return f"[错误] 绑定对应角色 {player_name} 当前处于离线状态，验证码激活失败！"

        success, msg, pending = self.plugin.verification_manager.verify_code(
            player_name, target_player.xuid, code, "qq"
        )

        if success:
            # 记录数据库
            self.plugin.data_manager.bind_player_qq(player_name, target_player.xuid, qq_str)

            # 主线程通知玩家和恢复权限
            def run_success_main() -> None:
                if self.plugin.is_valid_player(target_player):
                    from endstone import ColorFormat

                    target_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] QQ绑定激活成功！{ColorFormat.RESET}")
                    if self.plugin.config_manager.force_bind_qq:
                        self.plugin.permission_manager.restore_player_permissions(target_player)

            self.plugin.server.scheduler.run_task(self.plugin, run_success_main, delay=1)
            # 在 verify_code() 内部已进行了撤回和广播，这里返回空，不重复回复
            return ""
        return f"[错误] {msg}"

    async def cmd_bindqq(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/bindqq <游戏名> <QQ> 快捷强制双向绑定命令（管理员专用）"""
        if len(args) != 2:
            return "[错误] 用法: /bindqq <游戏内角色名称> <QQ号>"

        player_name = args[0]
        qq_str = args[1]

        if not qq_str.isdigit():
            return "[失败] 错误的 QQ 号格式，QQ 号必须为纯数字"

        existing = self.plugin.data_manager.get_qq_player(qq_str)
        if existing:
            return f"[失败] QQ 号 {qq_str} 已经绑定了角色: {existing}"

        if self.plugin.data_manager.is_player_bound(player_name):
            return f"[失败] 游戏角色 {player_name} 已经被其它 QQ 绑定"

        target_player = None
        for p in self.plugin.server.online_players:
            if p.name == player_name:
                target_player = p
                break

        if target_player:
            player_xuid = target_player.xuid
        else:
            res = self.plugin.data_manager._execute_read("SELECT xuid FROM players WHERE player_name = ?", (player_name,))
            player_xuid = res[0][0] if res and res[0][0] else ""

        if not player_xuid:
            return f"[失败] 玩家 {player_name} 当前不在线，且数据库中无该玩家的历史登录记录，无法获取唯一 XUID。请让该玩家先登录一次服务器，或在其在线时进行绑定。"

        # 直接记录到数据库中
        self.plugin.data_manager.bind_player_qq(player_name, player_xuid, qq_str)

        if target_player:
            # 在游戏内通知玩家并恢复其权限
            def restore_permissions() -> None:
                if self.plugin.is_valid_player(target_player):
                    from endstone import ColorFormat
                    target_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] 管理员已将您的账号与 QQ ({qq_str}) 强制绑定！{ColorFormat.RESET}")
                    if self.plugin.config_manager.force_bind_qq:
                        self.plugin.permission_manager.restore_player_permissions(target_player)

            self.plugin.server.scheduler.run_task(self.plugin, restore_permissions, delay=1)
            return f"[成功] 已成功将在线玩家 {player_name} 与 QQ {qq_str} 进行双向绑定！"
        else:
            return f"[成功] 已成功将离线玩家 {player_name} 与 QQ {qq_str} 进行双向绑定！"

    async def cmd_cmd(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/cmd <服务器命令> 命令"""
        if not args:
            return "[错误] 用法: /cmd <命令内容>"

        cmd_str = " ".join(args)
        cmd_str = html.unescape(cmd_str)

        # 异步主线程同步结果获取 (非阻塞 Future 实现)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        msg_outputs: list[str] = []
        err_outputs: list[str] = []

        def execute_main() -> None:
            try:
                from endstone.command import CommandSenderWrapper

                lang = self.plugin.server.language

                def on_msg(m: Any) -> None:
                    if isinstance(m, str):
                        msg_outputs.append(m)
                    else:
                        msg_outputs.append(lang.translate(m, lang.locale))

                def on_err(e: Any) -> None:
                    if isinstance(e, str):
                        err_outputs.append(e)
                    else:
                        err_outputs.append(lang.translate(e, lang.locale))

                wrapper = CommandSenderWrapper(
                    sender=self.plugin.server.command_sender,
                    on_message=on_msg,
                    on_error=on_err,
                )

                success = self.plugin.server.dispatch_command(wrapper, cmd_str)
                loop.call_soon_threadsafe(fut.set_result, success)
            except Exception as e:
                loop.call_soon_threadsafe(fut.set_exception, e)

        self.plugin.server.scheduler.run_task(self.plugin, execute_main, delay=0)

        try:
            # 10 秒防卡死超时控制
            success = await asyncio.wait_for(fut, timeout=10)
            merged = msg_outputs + [f"[错误] {x}" for x in err_outputs]
            output_text = "\n".join(merged) if merged else "指令已静默执行，无回显"
            status = "成功" if success else "失败"
            return f"控制台指令已执行: /{cmd_str}\n执行状态: {status}\n回显输出:\n{output_text}"
        except asyncio.TimeoutError:
            return "[错误] 执行超时"
        except Exception as e:
            return f"[错误] 执行失败: {e}"

    async def _resolve_target(self, input_str: str) -> tuple[str | None, str | None]:
        """解析搜索词为玩家 ID"""
        if not input_str:
            return None, None

        if input_str.isdigit():
            # 尝试作为当前 QQ 查找
            target = self.plugin.data_manager.get_qq_player(input_str)
            if target:
                return target, "QQ"
            # 历史 QQ 查找
            target = self.plugin.data_manager.get_qq_player_history(input_str)
            if target:
                return target, "QQ_History"

        # 作为角色名查找
        if input_str in self.plugin.data_manager.binding_data:
            return input_str, "Name"

        return None, None

    async def cmd_check(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/check <角色名|QQ> 命令"""
        if not args:
            return "[错误] 用法: /check <玩家游戏名|QQ号>"

        search_input = " ".join(args)
        target, match_type = await self._resolve_target(search_input)

        if not target:
            return f"[错误] 未在数据库中找到匹配 {search_input} 的玩家记录"

        p_data = self.plugin.data_manager.binding_data.get(target, {})
        info = self.plugin.data_manager.get_player_playtime_info(target, self.plugin.server.online_players)

        banned = self.plugin.data_manager.is_player_banned(target)
        status = "封禁中" if banned else ("在线" if info.get("is_online") else "离线")

        reply = (
            f"=== 玩家 {target} 详细档案 ===\n"
            f" • 匹配方式: {match_type}\n"
            f" • 绑定QQ: {p_data.get('qq') or '未绑定'}\n"
            f" • 角色XUID: {p_data.get('xuid') or 'N/A'}\n"
            f" • 当前状态: {status}\n"
            f" • 累积游戏时长: {format_playtime(info.get('total_playtime', 0))}\n"
            f" • 登录次数: {info.get('session_count', 0)} 次\n"
            f" • 上次加入: {format_timestamp(p_data.get('last_join_time'))}\n"
            f" • 上次退出: {format_timestamp(p_data.get('last_quit_time'))}"
        )
        return reply

    async def cmd_unbindqq(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/unbindqq <角色名|QQ> 命令"""
        if not args:
            return "[错误] 用法: /unbindqq <玩家游戏名|QQ号>"

        search_input = " ".join(args)
        target, _ = await self._resolve_target(search_input)

        if not target:
            return f"[错误] 未找到对应玩家 {search_input} 的绑定记录"

        if self.plugin.data_manager.unbind_player_qq(target, display_name):
            # 主线程重置在线玩家权限
            def force_demote() -> None:
                for p in self.plugin.server.online_players:
                    if p.name == target:
                        self.plugin.permission_manager.check_and_apply_permissions(p)
                        break

            self.plugin.server.scheduler.run_task(self.plugin, force_demote, delay=1)
            return f"[成功] 已强行解除了玩家 {target} 的 QQ 绑定记录"
        return f"[失败] 玩家 {target} 并没有绑定 QQ"

    async def cmd_ban(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/ban <角色名|QQ> [原因] 命令"""
        if not args:
            return "[错误] 用法: /ban <玩家游戏名|QQ号> [封禁原因]"

        search_input = args[0]
        reason = " ".join(args[1:]) if len(args) > 1 else "管理员封禁"

        target, _ = await self._resolve_target(search_input)
        if not target:
            return f"[错误] 未能在库中找到对应的角色: {search_input}"

        self.plugin.data_manager.ban_player(target, display_name, reason)

        # 主线程踢出或拉起通知并降权
        def apply_ban() -> None:
            for p in self.plugin.server.online_players:
                if p.name == target:
                    # 踢出玩家或下发通知
                    self.plugin.permission_manager.set_player_visitor_permissions(p)
                    self.plugin.permission_manager.send_ban_notification(p, reason, display_name, Timing.get_timestamp())
                    break

        self.plugin.server.scheduler.run_task(self.plugin, apply_ban, delay=1)
        return f"[成功] 已封禁玩家 {target} 的游戏功能并强制解绑其QQ，原因: {reason}"

    async def cmd_unban(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/unban <角色名|QQ> 命令"""
        if not args:
            return "[错误] 用法: /unban <玩家游戏名|QQ号>"

        search_input = " ".join(args)
        target, _ = await self._resolve_target(search_input)

        if not target:
            return f"[错误] 未在库中定位到该角色: {search_input}"

        if self.plugin.data_manager.unban_player(target, display_name):
            return f"[成功] 已解除玩家 {target} 的封禁限制"
        return f"[失败] 玩家 {target} 未处于被封禁状态"

    async def cmd_banlist(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/banlist 命令"""
        bans = self.plugin.data_manager.get_banned_players()
        if not bans:
            return "当前服务器黑名单中没有任何封禁记录"

        lines = [f"封禁玩家列表 ({len(bans)}):"]
        for b in bans[:15]:
            lines.append(f" • {b['name']} (原因: {b['ban_reason']} | 执行人: {b['ban_by']})")
        if len(bans) > 15:
            lines.append(f"... 还有 {len(bans) - 15} 个账号未列出")

        return "\n".join(lines)

    async def cmd_tog_qq(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/tog_qq 命令"""
        curr = self.plugin.config_manager.enable_qq_to_game
        self.plugin.config_manager.enable_qq_to_game = not curr
        self.plugin.config_manager.save_config()
        state = "开启" if not curr else "关闭"
        return f"QQ -> 游戏 的消息同步通道已 {state}"

    async def cmd_tog_game(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/tog_game 命令"""
        curr = self.plugin.config_manager.enable_game_to_qq
        self.plugin.config_manager.enable_game_to_qq = not curr
        self.plugin.config_manager.save_config()
        state = "开启" if not curr else "关闭"
        return f"游戏 -> QQ 的消息同步通道已 {state}"

    async def cmd_reload(self, ws_client: Any, user_id: int, args: list[str], group_id: int, display_name: str) -> str:
        """/reload 命令"""
        if self.plugin.config_manager.reload_config():
            return "[成功] 配置文件 config.toml 已成功重新加载"
        return "[失败] 载入 TOML 配置文件失败，请查看控制台日志"
