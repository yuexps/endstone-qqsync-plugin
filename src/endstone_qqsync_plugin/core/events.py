"""
游戏内事件处理器模块
"""

import asyncio
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

    def _is_admin_player(self, player_name: str, player_xuid: str | None = None) -> bool:
        """基于绑定的 QQ 号核对玩家是否为 TOML 中的超级管理员"""
        qq = self.plugin.data_manager.get_player_qq(player_name, player_xuid)
        if qq:
            return qq in self.plugin.config_manager.admins
        return False

    def _check_chat_cooldown(self, player_name: str, player_xuid: str | None = None) -> tuple[bool, str]:
        """检查玩家是否正处于刷屏禁言惩罚期"""
        if self._is_admin_player(player_name, player_xuid):
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

    def _check_and_update_spam(self, player_name: str, player_xuid: str | None = None) -> tuple[bool, str]:
        """滑窗算法检测玩家是否存在高频发言刷屏行为"""
        if self.plugin.config_manager.chat_count_limit == -1:
            return False, ""

        if self._is_admin_player(player_name, player_xuid):
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

            # 先按 XUID 归一化身份：改名时同步名称，避免后续登录写入产生同名重复记录
            self._sync_renamed_player_card(player_xuid, player_name)

            # 更新登录记录并启动在线时长累时
            self.plugin.data_manager.update_player_join(player_name, player_xuid)
            self.plugin.data_manager.start_player_timer(player_name, player_xuid)

            # 延迟 1 秒（20tick）应用权限挂载，给系统处理登录缓冲
            self._schedule_join_permission_check(player)

            # 延迟 3 秒（60tick）引导未绑定玩家弹出 UI 表单
            self._schedule_unbound_binding_form(player, player_name, player_xuid)

            # 推送登录事件到所有群组
            self._push_join_notice(player_name, player_xuid)

        except Exception as e:
            self.logger.error(f"登录事件处理发生内部错误: {e}")

    def _sync_renamed_player_card(self, player_xuid: str, player_name: str) -> None:
        """玩家改名时同步名称并更新 QQ 群名片"""
        old_name = self.plugin.data_manager.update_player_name(player_xuid, player_name)
        if old_name:
            self.logger.info(f"检测到玩家改名: {old_name} -> {player_name}")
            # 同步修改群名片
            existing = self.plugin.data_manager.get_player_by_xuid(player_xuid)
            qq = existing.get("qq")
            if (
                qq
                and self.plugin.ws_client
                and self.plugin.ws_client.is_connected
                and self.plugin.config_manager.sync_group_card
            ):
                from ..qq.commands import sync_group_card_for_all

                asyncio.run_coroutine_threadsafe(
                    sync_group_card_for_all(self.plugin.ws_client, int(qq), player_name),
                    self.plugin._loop,
                )

    def _schedule_join_permission_check(self, player: Any) -> None:
        """延迟 20tick 应用权限挂载"""
        self.plugin.server.scheduler.run_task(
            self.plugin,
            lambda: self.plugin.permission_manager.check_and_apply_permissions(player)
            if self.plugin.is_valid_player(player)
            else None,
            delay=20,
        )

    def _schedule_unbound_binding_form(self, player: Any, player_name: str, player_xuid: str) -> None:
        """延迟 60tick 向未绑定玩家弹出绑定表单"""
        if self.plugin.config_manager.force_bind_qq and not self.plugin.data_manager.is_player_bound(
            player_name, player_xuid
        ):
            self.plugin.server.scheduler.run_task(
                self.plugin,
                lambda: self.plugin.ui_manager.show_qq_binding_form(player)
                if self.plugin.is_valid_player(player)
                and not self.plugin.data_manager.is_player_banned(player_name, player_xuid)
                else None,
                delay=60,
            )

    def _broadcast_to_qq(self, text: str, only_chat_enabled: bool = False) -> None:
        """向 QQ 群广播文本消息，连接不可用时跳过"""
        if not (self.plugin.ws_client and self.plugin.ws_client.is_connected):
            return

        asyncio.run_coroutine_threadsafe(
            self.plugin.ws_client.broadcast_to_groups(text, only_chat_enabled=only_chat_enabled),
            self.plugin._loop,
        )

    def _push_join_notice(self, player_name: str, player_xuid: str) -> None:
        """推送上线消息到所有群组"""
        if self.plugin.ws_client and self.plugin.ws_client.is_connected and self.plugin.config_manager.enable_game_to_qq:
            playtime_info = self.plugin.data_manager.get_player_playtime_info(
                player_name, self.plugin.server.online_players, player_xuid
            )
            sessions = playtime_info.get("session_count", 0)
            join_msg = self._build_join_message(player_name, sessions)
            self._broadcast_to_qq(join_msg)

    def _build_join_message(self, player_name: str, sessions: int) -> str:
        """按登录次数生成上线消息"""
        if sessions == 1:
            return self._format_first_join_message(player_name)
        return self._format_return_join_message(player_name, sessions)

    def _format_first_join_message(self, player_name: str) -> str:
        """用首次加入模板格式化消息，模板异常时回退默认文案"""
        try:
            return self.plugin.config_manager.msg_first_join.format(player=player_name)
        except Exception as ex:
            self.logger.warning(f"msg_first_join 模板格式化失败，使用默认值。错误: {ex}")
            return f"[首次加入] 欢迎新玩家 {player_name} 首次进入服务器！"

    def _format_return_join_message(self, player_name: str, sessions: int) -> str:
        """用再次登录模板格式化消息，模板异常时回退默认文案"""
        try:
            return self.plugin.config_manager.msg_join.format(player=player_name, sessions=sessions)
        except Exception as ex:
            self.logger.warning(f"msg_join 模板格式化失败，使用默认值。错误: {ex}")
            return f"[+] {player_name} 上线了 (第 {sessions} 次登录)"

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent) -> None:
        """玩家退出服务器事件"""
        try:
            player = event.player
            player_name = player.name
            player_xuid = player.xuid

            self.logger.info(f"玩家 {player_name} (XUID: {player_xuid}) 离开服务器")

            # 计算并保存本次在线时长
            session_time = self._get_session_seconds(player_name, player_xuid)
            self._save_player_quit_state(player_name, player_xuid)

            # 推送退出消息到 QQ 群
            self._push_quit_notice(player_name, player_xuid, session_time)

        except Exception as e:
            self.logger.error(f"登出事件处理发生内部错误: {e}")

    def _get_session_seconds(self, player_name: str, player_xuid: str) -> int:
        """计算本次在线秒数"""
        start_time = self.plugin.data_manager.get_session_start_time(player_name, player_xuid)
        if start_time is None:
            return 0
        return int(time.time()) - start_time

    def _save_player_quit_state(self, player_name: str, player_xuid: str) -> None:
        """结算在线时长并清理玩家缓存"""
        # 结算时长落盘
        self.plugin.data_manager.stop_player_timer(player_name, player_xuid)
        self.plugin.data_manager.update_player_quit(player_name, player_xuid)

        # 清除权限附件快照缓存及验证状态，防内存泄漏
        self.plugin.permission_manager.cleanup_player_permissions(player_name)
        self.plugin.verification_manager.cleanup_player_data(player_name)
        self.player_chat_history.pop(player_name, None)
        self.player_spam_penalty.pop(player_name, None)

    def _push_quit_notice(self, player_name: str, player_xuid: str, session_time: int) -> None:
        """推送退出消息到 QQ 群"""
        if self.plugin.ws_client and self.plugin.ws_client.is_connected and self.plugin.config_manager.enable_game_to_qq:
            # 获取最新被同步的累计游玩时长
            playtime_info = self.plugin.data_manager.get_player_playtime_info(player_name, [], player_xuid)
            total_playtime = playtime_info.get("total_playtime", 0)

            # 格式化时长文本
            playtime_str = format_playtime(session_time)
            total_playtime_str = format_playtime(total_playtime)
            quit_msg = self._format_quit_message(player_name, playtime_str, total_playtime_str)
            self._broadcast_to_qq(quit_msg)

    def _format_quit_message(self, player_name: str, playtime_str: str, total_playtime_str: str) -> str:
        """用退出模板格式化消息，模板异常时回退默认文案"""
        try:
            return self.plugin.config_manager.msg_quit.format(
                player=player_name, time=playtime_str, total_time=total_playtime_str
            )
        except Exception as ex:
            self.logger.warning(f"msg_quit 模板格式化失败，使用默认值。错误: {ex}")
            return f"[-] {player_name} 下线了 ({playtime_str})"

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

            # 频控惩罚、刷屏判定与访客权限言论拦截
            if self._is_chat_blocked(event):
                return

            # 转发消息至各 QQ 群组
            self._forward_chat_to_groups(player_name, player.xuid, msg)
        except Exception as e:
            self.logger.error(f"玩家聊天转发事件发生内部错误: {e}")

    def _is_chat_blocked(self, event: PlayerChatEvent) -> bool:
        """依次校验禁言惩罚、发言频控、访客权限，命中则取消事件"""
        player = event.player
        player_name = player.name

        can_chat, err = self._check_chat_cooldown(player_name, player.xuid)
        if not can_chat:
            event.is_cancelled = True
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}{err}{ColorFormat.RESET}")
            return True

        is_spam, err = self._check_and_update_spam(player_name, player.xuid)
        if is_spam:
            event.is_cancelled = True
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}{err}{ColorFormat.RESET}")
            return True

        if self.plugin.config_manager.force_bind_qq:
            if not player.has_permission("qqsync.chat"):
                event.is_cancelled = True
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您需要绑定 QQ 号后才能在公屏发言！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请在游戏内使用 /bindqq 开始绑定流程{ColorFormat.RESET}")
                return True

        return False

    def _forward_chat_to_groups(self, player_name: str, player_xuid: str, msg: str) -> None:
        """过滤后转发公屏消息至各 QQ 群组"""
        if not (
            self.plugin.ws_client
            and self.plugin.ws_client.is_connected
            and self.plugin.config_manager.enable_game_to_qq
        ):
            return

        # 除非关闭了强制绑定，否则必须绑定才能转发
        if not self.plugin.data_manager.is_player_bound(player_name, player_xuid) and self.plugin.config_manager.force_bind_qq:
            return

        # 过消息过滤责任链中间件
        filtered_text, ctx = self.plugin.msg_pipeline.filter_message(
            text=msg,
            direction="game_to_qq",
            custom_ban_words=self.plugin.config_manager.custom_ban_words,
        )

        if ctx.get("has_sensitive"):
            self.logger.warning(f"玩家 {player_name} 消息中含有敏感词已过滤: {msg}")

        chat_payload = f"{player_name}: {filtered_text}"
        self._broadcast_to_qq(chat_payload, only_chat_enabled=True)

    @event_handler
    def on_player_death(self, event: PlayerDeathEvent) -> None:
        """玩家死亡事件"""
        try:
            player = event.player
            player_name = player.name

            if self.plugin.ws_client and self.plugin.ws_client.is_connected and self.plugin.config_manager.enable_game_to_qq:
                self._forward_death_to_groups(event, player_name)
        except Exception as e:
            self.logger.error(f"处理玩家死亡事件转发失败: {e}")

    def _forward_death_to_groups(self, event: PlayerDeathEvent, player_name: str) -> None:
        """核对绑定权限后转发中文本地化死亡消息"""
        # 核对绑定权限
        if (
            self.plugin.data_manager.is_player_bound(player_name, event.player.xuid)
            or not self.plugin.config_manager.force_bind_qq
        ):
            lang = event.player.server.language
            death_msg_raw = event.death_message
            # 获取中文本地化文本
            death_msg = lang.translate(death_msg_raw, locale="zh_CN")

            self._broadcast_to_qq(death_msg, only_chat_enabled=True)

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
            self._intercept_unbound_damage(event)
        except Exception as e:
            self.logger.error(f"处理伤害事件拦截发生错误: {e}")

    def _intercept_unbound_damage(self, event: ActorDamageEvent) -> None:
        """拦截未绑定玩家造成的伤害"""
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
            self._cancel_damage_without_permission(event, damager)

    def _cancel_damage_without_permission(self, event: ActorDamageEvent, damager: Any) -> None:
        """伤害源无战斗权限时取消事件并发送绑定提示"""
        if not damager.has_permission("qqsync.combat"):
            event.is_cancelled = True
            damager.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您当前为访客权限，无法攻击生物或玩家！{ColorFormat.RESET}")
            damager.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请在游戏内使用 /bindqq 完成QQ绑定后获取权限{ColorFormat.RESET}")
