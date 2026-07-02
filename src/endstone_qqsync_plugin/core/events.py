"""
游戏内事件处理器模块
"""

from collections import defaultdict, deque
import time
from typing import Any
from ..utils import format_playtime
from endstone.event import (
    event_handler,
    PlayerChatEvent,
    PlayerJoinEvent,
    PlayerQuitEvent,
    PlayerDeathEvent,
    PlayerInteractEvent,
    PlayerInteractActorEvent,
    PlayerPickupItemEvent,
    PlayerDropItemEvent,
    BlockBreakEvent,
    BlockPlaceEvent,
    ActorDamageEvent,
)
from endstone import ColorFormat


class Events:
    """监听并拦截处理游戏内的核心玩家和生物事件"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.logger = plugin.logger

        # 刷屏检测临时缓存
        self.player_chat_history: dict[str, deque[float]] = defaultdict(deque)  # 玩家名 -> 消息发送时间戳双端队列
        self.player_spam_penalty: dict[str, float] = {}                         # 玩家名 -> 禁言截止时间

    def _verify_permission_or_cancel(self, event: Any, permission: str, alert_message: str) -> bool:
        """
        统一权限核验拦截器：若玩家没有指定权限，则阻断事件并发送绑定提示
        """
        if not self.plugin.config_manager.force_bind_qq:
            return True

        player = event.player
        if not player.has_permission(permission):
            event.is_cancelled = True
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}{alert_message}{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请在游戏内使用 /bindqq 绑定QQ后获取完整权限{ColorFormat.RESET}")
            return False
        return True

    def _is_admin_player(self, player_name: str) -> bool:
        """基于绑定的 QQ 号核对玩家是否为 TOML 中的超级管理员"""
        qq = self.plugin.data_manager.get_player_qq(player_name)
        if qq:
            return qq in self.plugin.config_manager.admins
        return False

    def _check_chat_cooldown(self, player_name: str) -> tuple[bool, str]:
        """检查玩家是否正处于刷屏禁言惩罚期"""
        if self._is_admin_player(player_name):
            return True, ""

        now = time.time()
        if player_name in self.player_spam_penalty:
            penalty_end = self.player_spam_penalty[player_name]
            if now < penalty_end:
                remaining = int(penalty_end - now)
                minutes = remaining // 60
                seconds = remaining % 60
                time_str = f"{minutes}分{seconds}秒" if minutes > 0 else f"{seconds}秒"
                return False, f"检测到刷屏行为，您已被系统静音，惩罚剩余时间: {time_str}"
            # 惩罚到期清理
            del self.player_spam_penalty[player_name]

        return True, ""

    def _check_and_update_spam(self, player_name: str) -> tuple[bool, str]:
        """滑窗算法检测玩家是否存在高频发言刷屏行为"""
        if self.plugin.config_manager.chat_count_limit == -1:
            return False, ""

        if self._is_admin_player(player_name):
            return False, ""

        now = time.time()
        history = self.player_chat_history[player_name]

        # 清除超过60秒滑窗的旧发言时间戳
        while history and now - history[0] > 60:
            history.popleft()

        history.append(now)

        # 超过每分钟限制，触发惩罚
        if len(history) > self.plugin.config_manager.chat_count_limit:
            ban_time = self.plugin.config_manager.chat_ban_time
            self.player_spam_penalty[player_name] = now + ban_time
            history.clear()

            self.logger.warning(f"玩家 {player_name} 触发言论频控，静音禁言 {ban_time // 60} 分钟")
            return True, f"检测到刷屏行为，您已被系统禁言 {ban_time // 60} 分钟！"

        return False, ""

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent) -> None:
        """玩家进入服务器事件"""
        try:
            player = event.player
            player_name = player.name
            player_xuid = player.xuid

            self.logger.info(f"玩家 {player_name} (XUID: {player_xuid}) 登录服务器")

            # 更新登录记录并启动在线时长累时
            self.plugin.data_manager.update_player_join(player_name, player_xuid)
            self.plugin.data_manager.start_player_timer(player_name, player_xuid)

            # 检测 XUID 映射更名
            existing = self.plugin.data_manager.get_player_by_xuid(player_xuid)
            if existing and existing.get("name") != player_name:
                old_name = existing["name"]
                if self.plugin.data_manager.update_player_name(old_name, player_name, player_xuid):
                    # 同步修改群名片
                    qq = existing.get("qq")
                    if (
                        qq
                        and self.plugin.ws_client
                        and self.plugin.ws_client.is_connected
                        and self.plugin.config_manager.sync_group_card
                    ):
                        import asyncio
                        from ..qq.commands import sync_group_card_for_all

                        asyncio.run_coroutine_threadsafe(
                            sync_group_card_for_all(self.plugin.ws_client, int(qq), player_name),
                            self.plugin._loop,
                        )

            # 延迟 1 秒（20tick）应用权限挂载，给系统处理登录缓冲
            self.plugin.server.scheduler.run_task(
                self.plugin,
                lambda: self.plugin.permission_manager.check_and_apply_permissions(player)
                if self.plugin.is_valid_player(player)
                else None,
                delay=20,
            )

            # 延迟 3 秒（60tick）引导未绑定玩家弹出 UI 表单
            if self.plugin.config_manager.force_bind_qq and not self.plugin.data_manager.is_player_bound(
                player_name, player_xuid
            ):
                self.plugin.server.scheduler.run_task(
                    self.plugin,
                    lambda: self.plugin.ui_manager.show_qq_binding_form(player)
                    if self.plugin.is_valid_player(player)
                    and not self.plugin.data_manager.is_player_banned(player_name)
                    else None,
                    delay=60,
                )

            # 推送登录事件到所有群组
            if self.plugin.ws_client and self.plugin.ws_client.is_connected and self.plugin.config_manager.enable_game_to_qq:
                playtime_info = self.plugin.data_manager.get_player_playtime_info(
                    player_name, self.plugin.server.online_players
                )
                sessions = playtime_info.get("session_count", 0)

                if sessions == 1:
                    try:
                        join_msg = self.plugin.config_manager.msg_first_join.format(player=player_name)
                    except Exception as ex:
                        self.logger.warning(f"msg_first_join 模板格式化失败，使用默认值。错误: {ex}")
                        join_msg = f"[首次加入] 欢迎新玩家 {player_name} 首次进入服务器！"
                else:
                    try:
                        join_msg = self.plugin.config_manager.msg_join.format(
                            player=player_name, sessions=sessions
                        )
                    except Exception as ex:
                        self.logger.warning(f"msg_join 模板格式化失败，使用默认值。错误: {ex}")
                        join_msg = f"[+] {player_name} 上线了 (第 {sessions} 次登录)"

                import asyncio

                asyncio.run_coroutine_threadsafe(
                    self.plugin.ws_client.broadcast_to_groups(join_msg),
                    self.plugin._loop,
                )

        except Exception as e:
            self.logger.error(f"登录事件处理发生内部错误: {e}")

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent) -> None:
        """玩家退出服务器事件"""
        try:
            player = event.player
            player_name = player.name

            self.logger.info(f"玩家 {player_name} (XUID: {player.xuid}) 离开服务器")

            # 计算并保存本次在线时长
            session_time = 0
            if player_name in self.plugin.data_manager._session_start_times:
                start_time = self.plugin.data_manager._session_start_times[player_name]
                session_time = int(time.time()) - start_time

            # 正常执行时长结算落盘（会自动同步更新内存 binding_data）
            self.plugin.data_manager.stop_player_timer(player_name)
            self.plugin.data_manager.update_player_quit(player_name)

            # 清除权限附件快照缓存及验证状态，防内存泄漏
            self.plugin.permission_manager.cleanup_player_permissions(player_name)
            self.plugin.verification_manager.cleanup_player_data(player_name)
            self.player_chat_history.pop(player_name, None)
            self.player_spam_penalty.pop(player_name, None)

            # 推送退出消息到 QQ 群
            if self.plugin.ws_client and self.plugin.ws_client.is_connected and self.plugin.config_manager.enable_game_to_qq:
                # 获取最新被同步的累计游玩时长
                playtime_info = self.plugin.data_manager.get_player_playtime_info(
                    player_name, []
                )
                total_playtime = playtime_info.get("total_playtime", 0)

                # 统一调用通用辅助函数格式化时长
                playtime_str = format_playtime(session_time)
                total_playtime_str = format_playtime(total_playtime)

                try:
                    quit_msg = self.plugin.config_manager.msg_quit.format(
                        player=player_name, time=playtime_str, total_time=total_playtime_str
                    )
                except Exception as ex:
                    self.logger.warning(f"msg_quit 模板格式化失败，使用默认值。错误: {ex}")
                    quit_msg = f"[-] {player_name} 下线了 ({playtime_str})"

                import asyncio

                asyncio.run_coroutine_threadsafe(
                    self.plugin.ws_client.broadcast_to_groups(quit_msg),
                    self.plugin._loop,
                )

        except Exception as e:
            self.logger.error(f"登出事件处理发生内部错误: {e}")

    @event_handler
    def on_player_chat(self, event: PlayerChatEvent) -> None:
        """公屏聊天事件"""
        try:
            player = event.player
            player_name = player.name
            msg = event.message

            # 忽略游戏内斜杠命令消息，不予拦截
            if msg.startswith("/"):
                return

            # 1. 频控惩罚校验
            can_chat, err = self._check_chat_cooldown(player_name)
            if not can_chat:
                event.is_cancelled = True
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}{err}{ColorFormat.RESET}")
                return

            # 2. 发发言频控判定
            is_spam, err = self._check_and_update_spam(player_name)
            if is_spam:
                event.is_cancelled = True
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}{err}{ColorFormat.RESET}")
                return

            # 3. 访客权限言论拦截
            if self.plugin.config_manager.force_bind_qq:
                if not player.has_permission("qqsync.chat"):
                    event.is_cancelled = True
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您需要绑定 QQ 号后才能在公屏发言！{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请在游戏内使用 /bindqq 开始绑定流程{ColorFormat.RESET}")
                    return

            # 4. 转发消息至各 QQ 群组
            if self.plugin.ws_client and self.plugin.ws_client.is_connected and self.plugin.config_manager.enable_game_to_qq:
                # 除非关闭了强制绑定，否则必须绑定才能转发
                if (
                    self.plugin.data_manager.is_player_bound(player_name, player.xuid)
                    or not self.plugin.config_manager.force_bind_qq
                ):
                    import asyncio

                    # 过消息过滤责任链中间件
                    filtered_text, ctx = self.plugin.msg_pipeline.filter_message(
                        text=msg,
                        direction="game_to_qq",
                        custom_ban_words=self.plugin.config_manager.custom_ban_words,
                    )

                    if ctx.get("has_sensitive"):
                        self.logger.warning(f"玩家 {player_name} 消息中含有敏感词已过滤: {msg}")

                    chat_payload = f"{player_name}: {filtered_text}"

                    asyncio.run_coroutine_threadsafe(
                        self.plugin.ws_client.broadcast_to_groups(chat_payload, only_chat_enabled=True),
                        self.plugin._loop,
                    )
        except Exception as e:
            self.logger.error(f"玩家聊天转发事件发生内部错误: {e}")

    @event_handler
    def on_player_death(self, event: PlayerDeathEvent) -> None:
        """玩家死亡事件"""
        try:
            player = event.player
            player_name = player.name

            if self.plugin.ws_client and self.plugin.ws_client.is_connected and self.plugin.config_manager.enable_game_to_qq:
                # 核对绑定权限
                if (
                    self.plugin.data_manager.is_player_bound(player_name, player.xuid)
                    or not self.plugin.config_manager.force_bind_qq
                ):
                    lang = event.player.server.language
                    death_msg_raw = event.death_message
                    # 获取中文本地化文本
                    death_msg = lang.translate(death_msg_raw, locale="zh_CN")

                    import asyncio

                    asyncio.run_coroutine_threadsafe(
                        self.plugin.ws_client.broadcast_to_groups(death_msg, only_chat_enabled=True),
                        self.plugin._loop,
                    )
        except Exception as e:
            self.logger.error(f"处理玩家死亡事件转发失败: {e}")

    @event_handler
    def on_block_break(self, event: BlockBreakEvent) -> None:
        """方块破坏"""
        self._verify_permission_or_cancel(event, "qqsync.destructive", "您当前为访客权限，无法破坏方块！")

    @event_handler
    def on_block_place(self, event: BlockPlaceEvent) -> None:
        """方块放置"""
        self._verify_permission_or_cancel(event, "qqsync.block_place", "您当前为访客权限，无法放置方块！")

    @event_handler
    def on_player_interact(self, event: PlayerInteractEvent) -> None:
        """与世界交互/使用物品"""
        self._verify_permission_or_cancel(
            event, "qqsync.item_use", "您当前为访客权限，使用工具/操作机械的行为已被阻断！"
        )

    @event_handler
    def on_player_interact_actor(self, event: PlayerInteractActorEvent) -> None:
        """与实体（NPC等）交互"""
        self._verify_permission_or_cancel(event, "qqsync.combat", "您当前为访客权限，与实体交互的行为已被阻断！")

    @event_handler
    def on_player_pickup_item(self, event: PlayerPickupItemEvent) -> None:
        """拾取地面物品"""
        self._verify_permission_or_cancel(event, "qqsync.item_pickup_drop", "您当前为访客权限，无法拾取地面上的物品！")

    @event_handler
    def on_player_drop_item(self, event: PlayerDropItemEvent) -> None:
        """丢弃背包物品"""
        self._verify_permission_or_cancel(event, "qqsync.item_pickup_drop", "您当前为访客权限，无法丢弃物品！")

    @event_handler
    def on_actor_damage(self, event: ActorDamageEvent) -> None:
        """攻击伤害行为"""
        try:
            if not self.plugin.config_manager.force_bind_qq:
                return

            damager = event.damage_source.actor
            # 仅在伤害源确实是玩家的情况下触发校验
            if (
                damager
                and hasattr(damager, "name")
                and hasattr(damager, "xuid")
                and hasattr(damager, "has_permission")
            ):
                if not damager.has_permission("qqsync.combat"):
                    event.is_cancelled = True
                    damager.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您当前为访客权限，无法攻击生物或玩家！{ColorFormat.RESET}")
                    damager.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请在游戏内使用 /bindqq 完成QQ绑定后获取权限{ColorFormat.RESET}")
        except Exception as e:
            self.logger.error(f"处理伤害事件拦截发生错误: {e}")
