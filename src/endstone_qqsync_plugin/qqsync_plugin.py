"""
QQsync 群服互通插件主入口文件
"""

import asyncio
from pathlib import Path
import threading
from typing import Any
from endstone.plugin import Plugin
from endstone import ColorFormat

# 导入重构后的全新强类型业务组件
from .core import Config, Data, Permissions, UI, Verification, Events
from .qq import WebSocketClient, GroupCommandHandler
from .utils import MessagePipeline

# Endstone 调度 tick 换算：20 tick = 1 秒
_TICKS_PER_SECOND = 20
# 验证码发送队列出队周期：3 秒
_SEND_QUEUE_PERIOD_TICKS = 3 * _TICKS_PER_SECOND
# 定时任务启动延迟：60 秒
_INITIAL_DELAY_TICKS = 60 * _TICKS_PER_SECOND
# 在线计时器增量结算周期：60 秒
_PLAYTIME_TIMER_PERIOD_TICKS = 60 * _TICKS_PER_SECOND
# 过期数据自愈清理周期：5 分钟
_CLEANUP_PERIOD_TICKS = 5 * 60 * _TICKS_PER_SECOND
# 群成员缓存拉取周期：1 小时
_GROUP_CACHE_PERIOD_TICKS = 60 * 60 * _TICKS_PER_SECOND


class qqsync(Plugin):
    """QQsync群服互通插件主类"""

    api_version = "0.11"

    commands = {
        "bindqq": {
            "description": "QQ绑定相关命令",
            "usages": ["/bindqq"],
            "aliases": ["qq"],
            "permissions": ["qqsync.command.bindqq"],
        }
    }

    # 注册初始权限节点
    permissions = {
        "qqsync.command.bindqq": {"description": "允许使用 /bindqq 命令", "default": True},
        "qqsync.visitor": {
            "description": "访客黑名单权限组",
            "default": False,
            "children": {
                "minecraft.command.say": False,
                "minecraft.command.tell": False,
                "minecraft.command.me": False,
                "minecraft.command.msg": False,
                "minecraft.command.w": False,
                "minecraft.command.whisper": False,
                "endstone.command.say": False,
                "endstone.command.tell": False,
                "endstone.command.me": False,
                "minecraft.command.setblock": False,
                "minecraft.command.fill": False,
                "minecraft.command.clone": False,
                "minecraft.command.give": False,
                "minecraft.command.clear": False,
                "minecraft.command.kill": False,
                "minecraft.command.summon": False,
                "minecraft.command.gamemode": False,
                "minecraft.command.tp": False,
                "minecraft.command.teleport": False,
                "endstone.command.setblock": False,
                "endstone.command.fill": False,
                "endstone.command.give": False,
                "endstone.command.clear": False,
                "endstone.command.kill": False,
                "endstone.command.gamemode": False,
                "endstone.command.tp": False,
                "minecraft.place": False,
                "minecraft.place.block": False,
                "minecraft.block.place": False,
                "minecraft.build": False,
                "minecraft.build.place": False,
                "minecraft.world.place": False,
                "endstone.place": False,
                "endstone.place.block": False,
                "endstone.block.place": False,
                "endstone.build": False,
                "endstone.build.place": False,
                "endstone.world.place": False,
                "place": False,
                "place.block": False,
                "block.place": False,
                "build": False,
                "build.place": False,
                "minecraft.use": False,
                "minecraft.use.item": False,
                "minecraft.item.use": False,
                "minecraft.interact": False,
                "minecraft.interact.block": False,
                "minecraft.interact.item": False,
                "minecraft.rightclick": False,
                "minecraft.click": False,
                "minecraft.activate": False,
                "endstone.use": False,
                "endstone.use.item": False,
                "endstone.item.use": False,
                "endstone.interact": False,
                "endstone.interact.block": False,
                "endstone.interact.item": False,
                "endstone.rightclick": False,
                "endstone.click": False,
                "endstone.activate": False,
                "use": False,
                "use.item": False,
                "item.use": False,
                "interact": False,
                "interact.block": False,
                "interact.item": False,
                "rightclick": False,
                "click": False,
                "activate": False,
                "minecraft.pickup": False,
                "minecraft.pickup.item": False,
                "minecraft.item.pickup": False,
                "minecraft.drop": False,
                "minecraft.drop.item": False,
                "minecraft.item.drop": False,
                "minecraft.collect": False,
                "minecraft.collect.item": False,
                "minecraft.item.collect": False,
                "minecraft.throw": False,
                "minecraft.throw.item": False,
                "minecraft.item.throw": False,
                "endstone.pickup": False,
                "endstone.pickup.item": False,
                "endstone.item.pickup": False,
                "endstone.drop": False,
                "endstone.drop.item": False,
                "endstone.item.drop": False,
                "endstone.collect": False,
                "endstone.collect.item": False,
                "endstone.item.collect": False,
                "endstone.throw": False,
                "endstone.throw.item": False,
                "endstone.item.throw": False,
                "pickup": False,
                "pickup.item": False,
                "item.pickup": False,
                "drop": False,
                "drop.item": False,
                "item.drop": False,
                "collect": False,
                "collect.item": False,
                "item.collect": False,
                "throw": False,
                "throw.item": False,
                "item.throw": False,
                "minecraft.interact.entity": False,
                "minecraft.attack.entity": False,
                "minecraft.damage.entity": False,
                "minecraft.hit.entity": False,
                "minecraft.pvp": False,
                "minecraft.combat": False,
                "minecraft.hurt.entity": False,
                "minecraft.kill.entity": False,
                "endstone.interact.entity": False,
                "endstone.attack.entity": False,
                "endstone.damage.entity": False,
                "endstone.hit.entity": False,
                "endstone.pvp": False,
                "endstone.combat": False,
                "endstone.hurt.entity": False,
                "endstone.kill.entity": False,
                "attack": False,
                "damage": False,
                "combat": False,
                "pvp": False,
                "entity.attack": False,
                "entity.damage": False,
                "entity.hurt": False,
            },
        },
    }



    def on_load(self) -> None:
        self.logger.info(f"{ColorFormat.BLUE}qqsync_plugin {ColorFormat.WHITE}主程序正加载加载中...{ColorFormat.RESET}")

    def on_enable(self) -> None:
        """插件启用生命周期接口"""
        try:
            # 1. 初始化各业务管理器
            self._init_managers()

            # 2. 注册核心游戏监听事件
            self.register_events(self.event_handlers)

            # 3. 启动并调度定时循环任务
            self._schedule_timer_tasks()

            # 4. 建立 WebSocket 子线程长连接
            self._init_websocket_connection()

            welcome_msg = f"{ColorFormat.GREEN}qqsync_plugin {ColorFormat.YELLOW}已激活{ColorFormat.RESET}"
            self.logger.info(welcome_msg)
        except Exception as e:
            self.logger.error(f"插件启用遭遇致命错误: {e}")
            raise e

    def _init_managers(self) -> None:
        """初始化全部管理模块并保持向后属性兼容"""
        self.config_manager = Config(Path(self.data_folder), self.logger)

        # 数据存储层
        self.data_manager = Data(self, Path(self.data_folder), self.logger)

        # 验证风控层
        self.verification_manager = Verification(self, self.logger)

        # 权限挂载控制层
        self.permission_manager = Permissions(self, self.logger)

        # 游戏监听事件层
        self.event_handlers = Events(self)

        # 游戏内绑定UI界面层
        self.ui_manager = UI(self)

        # 群成员隔离缓存 {群号: {QQ号集合}}
        self.group_members: dict[int, set[str]] = {}
        self.logged_left_players: set[str] = set()

        # 责任链过滤管道
        self.msg_pipeline = MessagePipeline()

        # 群命令分发路由
        self.group_command_handler = GroupCommandHandler(self)

        # 网络出队回发指令变量
        self._send_startup_message = True

    def _init_websocket_connection(self) -> None:
        """建立 WebSocket 自愈连接专用子线程事件循环"""
        self.ws_client = WebSocketClient(self)

        # 独立协程事件循环，规避 BDS 游戏主线程阻塞
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        # 提交异步长连接常驻任务
        self._task = asyncio.run_coroutine_threadsafe(self.ws_client.connect_forever(), self._loop)

    def _run_loop(self) -> None:
        """子线程事件循环常驻入口"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _schedule_timer_tasks(self) -> None:
        """注册调度 Endstone 定时任务"""
        # 1. 验证码防抖出队发送调度（每3秒，60tick）
        self.server.scheduler.run_task(
            self,
            self.verification_manager.process_verification_send_queue,
            delay=_SEND_QUEUE_PERIOD_TICKS,
            period=_SEND_QUEUE_PERIOD_TICKS,
        )

        # 2. 在线计时器增量结算（每60秒，1200tick）
        self.server.scheduler.run_task(
            self,
            self._update_online_playtime_timers,
            delay=_INITIAL_DELAY_TICKS,
            period=_PLAYTIME_TIMER_PERIOD_TICKS,
        )

        # 3. 超时过期数据自愈清理（每5分钟，6000tick）
        self.server.scheduler.run_task(
            self,
            self._cleanup_expired_data,
            delay=_INITIAL_DELAY_TICKS,
            period=_CLEANUP_PERIOD_TICKS,
        )

        # 4. 强制加退群成员列表缓存拉取（每小时，72000tick）
        self.server.scheduler.run_task(
            self,
            self._update_group_members,
            delay=_INITIAL_DELAY_TICKS,
            period=_GROUP_CACHE_PERIOD_TICKS,
        )

    def _update_online_playtime_timers(self) -> None:
        """定时任务回调：阶段性结算当前在线玩家的时长统计数据"""
        try:
            self.data_manager.update_online_timers(list(self.server.online_players))
        except Exception as e:
            self.logger.error(f"定时结算在线计时器失败: {e}")

    def _cleanup_expired_data(self) -> None:
        """定时任务回调：清理已退线玩家的临时数据与缓存文件"""
        try:
            self.verification_manager.cleanup_expired_verifications()
            self._cleanup_offline_players()
        except Exception as e:
            self.logger.error(f"清理过期系统数据失败: {e}")

    def _cleanup_offline_players(self) -> None:
        """回收已离线角色的临时资源与权限附件"""
        online_names = {p.name for p in self.server.online_players}
        for name in list(self.permission_manager.attachment_cache.keys()):
            if name not in online_names:
                self.permission_manager.cleanup_player_permissions(name)
                self.verification_manager.cleanup_player_data(name)

    def _update_group_members(self) -> None:
        """定时任务回调：从 OneBot 主动刷新群成员列表"""
        if not (self.config_manager.force_bind_qq and self.config_manager.check_group_member):
            return

        if self.ws_client and self.ws_client.is_connected:
            # 异步发送请求以刷新缓存
            async def refresh() -> None:
                for g in self.config_manager.groups:
                    await self.ws_client.send_message(
                        {
                            "action": "get_group_member_list",
                            "params": {"group_id": g["id"]},
                            "echo": f"get_group_member_list:{g['id']}",
                        }
                    )

            asyncio.run_coroutine_threadsafe(refresh(), self._loop)
        else:
            self.logger.warning("OneBot WS 连接目前离线，群成员缓存定时更新已被跳过")

    def on_command(self, sender: Any, command: Any, args: list[str]) -> bool:
        """指令监听回调：分发游戏内 bindqq 指令"""
        if command.name == "bindqq":
            return self._handle_bindqq_command(sender, args)
        return False

    def _handle_bindqq_command(self, sender: Any, args: list[str]) -> bool:
        """处理 /bindqq 指令逻辑"""
        try:
            if not (hasattr(sender, "name") and hasattr(sender, "xuid")):
                sender.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}此命令仅限游戏内玩家使用！{ColorFormat.RESET}")
                return True

            self._show_binding_state(sender)
            return True
        except Exception as e:
            self.logger.error(f"执行 /bindqq 命令失败: {e}")
            if hasattr(sender, "send_message"):
                sender.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}指令执行内部出错，请重试{ColorFormat.RESET}")
            return False

    def _show_binding_state(self, player: Any) -> None:
        """按绑定状态向玩家回执账号信息或拉起绑定表单"""
        player_name = player.name
        if self.data_manager.is_player_bound(player_name, player.xuid):
            qq = self.data_manager.get_player_qq(player_name, player.xuid)
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}账号状态：已绑定 QQ ({qq}){ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如需解绑，请联系管理员处理{ColorFormat.RESET}")
            return

        if not self.config_manager.force_bind_qq:
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}强制绑定 QQ 校验当前已在 TOML 配置中关闭。{ColorFormat.RESET}")
            return

        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}您尚未绑定 QQ 账号，正在加载绑定表单...{ColorFormat.RESET}")
        # 延时 5 个 tick 避开聊天发送冲突，弹出绑定 UI
        self.server.scheduler.run_task(
            self,
            lambda p=player: self.ui_manager.show_qq_binding_form(p) if self.is_valid_player(p) else None,
            delay=5,
        )

    def is_valid_player(self, player: Any) -> bool:
        """校验玩家对象在线且有效"""
        try:
            return bool(
                player
                and hasattr(player, "send_message")
                and hasattr(player, "name")
                and hasattr(player, "xuid")
                and getattr(player, "is_online", True)
            )
        except Exception:
            return False

    def api_send_message(self, text: str, group_id: int | None = None) -> bool:
        """公共 API 接口：供其它插件调用以向指定或所有群组投递消息"""
        if not self.config_manager.api_qq_enable:
            return False

        if group_id is not None and group_id not in {g["id"] for g in self.config_manager.groups}:
            return False

        if not (self.ws_client and self.ws_client.is_connected):
            return False

        try:
            if group_id is not None:
                asyncio.run_coroutine_threadsafe(self.ws_client.send_group_message(group_id, text), self._loop)
            else:
                asyncio.run_coroutine_threadsafe(self.ws_client.broadcast_to_groups(text), self._loop)
            return True
        except Exception as e:
            self.logger.debug(f"发送 QQ 消息失败: {e}")
        return False

    def on_disable(self) -> None:
        """卸载生命周期：关闭连接并结算计时数据"""
        try:
            self.logger.info("正在禁用 qqsync_plugin...")

            # 1. 广播服务器停止通知
            self._notify_server_stop()

            # 2. 结算全部在线玩家时长统计，并批量回写 SQLite
            if hasattr(self, "data_manager"):
                self.data_manager.cleanup_timer_system()
                self.data_manager.save_data()
                self.data_manager.close()

            # 3. 关闭网络层并注销子线程事件循环
            self._shutdown_network_layer()

            self.logger.info(f"{ColorFormat.YELLOW}qqsync_plugin 卸载清理完成。{ColorFormat.RESET}")
        except Exception as e:
            self.logger.error(f"插件卸载回收资源失败: {e}")

    def _notify_server_stop(self) -> None:
        """向所有群组广播服务器停止通知"""
        if not (self.ws_client and self.ws_client.is_connected):
            return

        try:
            fut = asyncio.run_coroutine_threadsafe(
                self.ws_client.broadcast_to_groups("[QQSync] 游戏服务器已停止"),
                self._loop,
            )
            # 强阻塞3秒等待发送回执
            fut.result(timeout=3)
        except Exception as e:
            self.logger.warning(f"广播服务器停止通知失败: {e}")

    def _shutdown_network_layer(self) -> None:
        """关闭 WebSocket 网络层与缓冲区写协程，并注销子线程事件循环"""
        if hasattr(self, "ws_client") and self.ws_client:
            self.ws_client.stop()

        if not (hasattr(self, "_loop") and self._loop):
            return

        try:
            if not self._loop.is_closed() and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception as e:
            self.logger.debug(f"通知事件循环退出失败: {e}")

        # 等待网络线程退出
        if hasattr(self, "_thread") and self._thread and self._thread.is_alive():
            try:
                self._thread.join(timeout=5)
            except Exception as e:
                self.logger.debug(f"等待网络线程退出失败: {e}")

        try:
            self._loop.close()
        except Exception as e:
            self.logger.debug(f"关闭事件循环失败: {e}")
