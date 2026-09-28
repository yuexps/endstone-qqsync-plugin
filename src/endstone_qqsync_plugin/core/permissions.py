"""
玩家权限管理模块
"""

from typing import Any
from ..utils.helpers import format_timestamp


class Permissions:
    """访客权限下发、恢复与附件缓存"""

    def __init__(self, plugin: Any, logger: Any) -> None:
        self.plugin = plugin
        self.logger = logger
        # 玩家名 -> PermissionAttachment 缓存
        self.attachment_cache: dict[str, Any] = {}

    def get_player_visitor_reason(self, player_name: str, player_xuid: str | None = None) -> str:
        """获取玩家被降级为访客权限的具体原因，非访客时返回空串"""
        if not self.plugin.config_manager.force_bind_qq:
            return ""

        if self.plugin.data_manager.is_player_banned(player_name, player_xuid):
            return "已被封禁"

        if not self.plugin.data_manager.is_player_bound(player_name, player_xuid):
            return "未绑定QQ"

        if not self.plugin.config_manager.check_group_member:
            return ""

        player_qq = self.plugin.data_manager.get_player_qq(player_name, player_xuid)
        if not player_qq:
            return ""

        if self._is_player_left_all_groups(player_qq):
            self._notify_player_left_group(player_name, player_qq)
            return "已退出QQ群"
        return ""

    def _is_player_left_all_groups(self, player_qq: str) -> bool:
        """判断玩家QQ是否已不在任何启用了退群检测的群聊中"""
        has_cached_groups = False
        for group in self.plugin.config_manager.groups:
            group_id = group["id"]
            if group_id not in self.plugin.group_members:
                continue
            has_cached_groups = True
            if player_qq in self.plugin.group_members[group_id]:
                return False
        return has_cached_groups

    def _notify_player_left_group(self, player_name: str, player_qq: str) -> None:
        """对同一玩家只输出一次退群访客判定日志"""
        if player_qq not in self.plugin.logged_left_players:
            self.logger.info(f"玩家 {player_name} (QQ: {player_qq}) 不在任何配置的群聊中，判定退群限制为访客")
            self.plugin.logged_left_players.add(player_qq)

    def _get_or_create_attachment(self, player: Any) -> Any:
        """获取玩家当前缓存的权限附件，若无则新建并录入缓存"""
        player_name = player.name
        if player_name not in self.attachment_cache:
            self._create_attachment(player, player_name)
        return self.attachment_cache[player_name]

    def _create_attachment(self, player: Any, player_name: str) -> None:
        """为玩家创建权限附件并录入缓存"""
        try:
            # 移除本插件残留的权限附件，避免重复挂载
            self._clear_plugin_attachments(player)
            # 创建单例挂载点
            self.attachment_cache[player_name] = player.add_attachment(self.plugin)
        except Exception as e:
            self.logger.error(f"创建玩家 {player_name} 权限挂载附件失败: {e}")
            raise e

    def set_player_visitor_permissions(self, player: Any) -> bool:
        """将玩家降级为访客只读权限"""
        try:
            attachment = self._get_or_create_attachment(player)

            # 激活 Endstone 黑名单权限组通配继承
            attachment.set_permission("qqsync.visitor", True)

            # 关闭行为节点权限
            attachment.set_permission("qqsync.command.bindqq", True)
            attachment.set_permission("qqsync.chat", False)
            attachment.set_permission("qqsync.destructive", False)
            attachment.set_permission("qqsync.block_place", False)
            attachment.set_permission("qqsync.item_use", False)
            attachment.set_permission("qqsync.item_pickup_drop", False)
            attachment.set_permission("qqsync.combat", False)

            player.recalculate_permissions()
            return True
        except Exception as e:
            self.logger.error(f"下发访客限制权限失败: {e}")
            return False

    def restore_player_permissions(self, player: Any) -> bool:
        """恢复玩家游戏权限"""
        try:
            attachment = self._get_or_create_attachment(player)

            # 禁用访客黑名单继承
            attachment.set_permission("qqsync.visitor", False)

            # 恢复玩家操作权限
            attachment.set_permission("qqsync.command.bindqq", True)
            attachment.set_permission("qqsync.chat", True)
            attachment.set_permission("qqsync.destructive", True)
            attachment.set_permission("qqsync.block_place", True)
            attachment.set_permission("qqsync.item_use", True)
            attachment.set_permission("qqsync.item_pickup_drop", True)
            attachment.set_permission("qqsync.combat", True)

            player.recalculate_permissions()
            return True
        except Exception as e:
            self.logger.error(f"恢复玩家权限失败: {e}")
            return False

    def _clear_plugin_attachments(self, player: Any) -> None:
        """移除指定玩家名下本插件的权限附件"""
        try:
            for attachment in self._collect_plugin_attachments(player):
                self._remove_attachment(attachment)
        except Exception as e:
            self.logger.debug(f"清理玩家 {player.name} 残留权限附件失败: {e}")

    def _collect_plugin_attachments(self, player: Any) -> list[Any]:
        """筛选玩家有效权限中属于本插件的附件"""
        # 移除 effective_permissions 中本插件的残留附件
        to_remove = []
        for eff in player.effective_permissions:
            if (
                hasattr(eff, "attachment")
                and hasattr(eff.attachment, "plugin")
                and eff.attachment.plugin == self.plugin
            ):
                to_remove.append(eff.attachment)
        return to_remove

    def _remove_attachment(self, attachment: Any) -> None:
        """移除单个残留权限附件"""
        try:
            attachment.remove()
        except Exception as e:
            self.logger.debug(f"移除残留权限附件失败: {e}")

    def cleanup_player_permissions(self, player_name: str) -> None:
        """玩家离线时移除内存中缓存的权限附件"""
        if player_name not in self.attachment_cache:
            return
        self._remove_cached_attachment(player_name)
        del self.attachment_cache[player_name]

    def _remove_cached_attachment(self, player_name: str) -> None:
        """移除缓存中记录的玩家权限附件"""
        try:
            self.attachment_cache[player_name].remove()
        except Exception as e:
            self.logger.debug(f"移除玩家 {player_name} 权限附件失败: {e}")

    def check_and_apply_permissions(self, player: Any) -> None:
        """校验绑定与入群状态并应用权限组"""
        if not self.plugin.is_valid_player(player):
            return

        if not self.plugin.config_manager.force_bind_qq:
            return

        player_name = player.name
        reason = self.get_player_visitor_reason(player_name, player.xuid)
        from endstone import ColorFormat

        if not reason:
            self.restore_player_permissions(player)
            player.send_message(
                f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] 您已绑定QQ且在群内，已获得完整游戏权限{ColorFormat.RESET}"
            )
        else:
            self.set_player_visitor_permissions(player)
            self._send_visitor_notification(player, reason)

    def _send_visitor_notification(self, player: Any, reason: str) -> None:
        """向访客降级玩家下发具体的受限警告和绑定引导信息"""
        from endstone import ColorFormat

        cfg = self._build_visitor_meta().get(reason)
        if not cfg:
            return

        player.send_message(
            f"{ColorFormat.GRAY}[QQsync] {cfg['title_color']}{cfg['title']} 当前为您应用了只读的访客权限（原因: {reason}）{ColorFormat.RESET}"
        )
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}此时以下行为受限：{ColorFormat.RESET}")
        self._send_visitor_limits(player)

        player.send_message(f"{ColorFormat.GRAY}[QQsync] {cfg['solution_color']}{cfg['solution']}{ColorFormat.RESET}")

        if reason == "已退出QQ群":
            self._send_joinable_groups(player)

    def _build_visitor_meta(self) -> dict[str, dict[str, str]]:
        """构造各访客原因对应的提示文案表"""
        from endstone import ColorFormat

        return {
            "已被封禁": {
                "title": "[封禁]",
                "title_color": ColorFormat.RED,
                "solution": "如需申诉解封，请联系管理员",
                "solution_color": ColorFormat.YELLOW,
            },
            "未绑定QQ": {
                "title": "[警告]",
                "title_color": ColorFormat.YELLOW,
                "solution": "解决方案: 在游戏中使用 /bindqq 绑定QQ",
                "solution_color": ColorFormat.GREEN,
            },
            "已退出QQ群": {
                "title": "[警告]",
                "title_color": ColorFormat.YELLOW,
                "solution": "解决方案: 重新加入配置的QQ群后，系统将自动恢复权限",
                "solution_color": ColorFormat.GREEN,
            },
        }

    def _send_visitor_limits(self, player: Any) -> None:
        """下发访客受限行为清单"""
        from endstone import ColorFormat

        limits = [
            "• 无法发送公屏聊天消息",
            "• 无法破坏或放置方块",
            "• 无法使用任何工具与物品",
            "• 无法从地面拾取或丢弃物品",
            "• 无法与容器或设备交互",
            "• 无法攻击游戏内的任何实体",
        ]
        for lim in limits:
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}{lim}{ColorFormat.RESET}")

    def _send_joinable_groups(self, player: Any) -> None:
        """下发可供加入的群聊列表"""
        from endstone import ColorFormat

        groups = self.plugin.config_manager.groups
        if not groups:
            return
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}可供加入的群聊列表：{ColorFormat.RESET}")
        for g in groups:
            player.send_message(f"{ColorFormat.GRAY}  • 群号: {g['id']} ({g['name']}){ColorFormat.RESET}")

    def send_ban_notification(self, player: Any, ban_reason: str, ban_by: str, ban_time: int) -> None:
        """单独对封禁玩家下发警告"""
        if not self.plugin.is_valid_player(player):
            return
        from endstone import ColorFormat

        time_str = format_timestamp(ban_time)
        player.send_message(
            f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}==================== 封禁通知 ===================={ColorFormat.RESET}"
        )
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您已被服务器封禁，无法绑定QQ及享受群服功能{ColorFormat.RESET}")
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}封禁时间: {time_str}{ColorFormat.RESET}")
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}执行者: {ban_by}{ColorFormat.RESET}")
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}封禁原因: {ban_reason}{ColorFormat.RESET}")
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}如有疑问请联系管理员处理{ColorFormat.RESET}")
        player.send_message(
            f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}=================================================={ColorFormat.RESET}"
        )
