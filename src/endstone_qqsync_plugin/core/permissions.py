"""
玩家权限管理模块
"""

from typing import Any
from ..utils.helpers import format_timestamp


class Permissions:
    """负责玩家游戏内访客权限的下发、恢复以及附件缓存控制"""

    def __init__(self, plugin: Any, logger: Any) -> None:
        self.plugin = plugin
        self.logger = logger
        # 附件缓存，以玩家名映射其持久拥有的 PermissionAttachment 实例
        self.attachment_cache: dict[str, Any] = {}

    def is_player_visitor(self, player_name: str, player_xuid: str | None = None) -> bool:
        """判定玩家当前状态是否符合访客限制要求"""
        if not self.plugin.config_manager.force_bind_qq:
            return False

        # 已封禁玩家强制设为访客
        if self.plugin.data_manager.is_player_banned(player_name, player_xuid):
            return True

        # 未绑定QQ玩家设为访客
        if not self.plugin.data_manager.is_player_bound(player_name, player_xuid):
            return True

        # 退群检测
        if self.plugin.config_manager.check_group_member:
            player_qq = self.plugin.data_manager.get_player_qq(player_name, player_xuid)
            if player_qq:
                is_in_any_group = False
                has_cached_groups = False
                # 遍历所有启用了绑定验证/退群检测的群聊
                for group in self.plugin.config_manager.groups:
                    group_id = group["id"]
                    if group_id in self.plugin.group_members:
                        has_cached_groups = True
                        if player_qq in self.plugin.group_members[group_id]:
                            is_in_any_group = True
                            break
                # 在获取了群缓存的条件下，若玩家在任何配置的群中都找不到，判定退群
                if has_cached_groups and not is_in_any_group:
                    # 避免重复打印退群提醒日志
                    if not hasattr(self.plugin, "logged_left_players"):
                        self.plugin.logged_left_players = set()
                    if player_qq not in self.plugin.logged_left_players:
                        self.logger.info(f"玩家 {player_name} (QQ: {player_qq}) 不在任何配置的群聊中，判定退群限制为访客")
                        self.plugin.logged_left_players.add(player_qq)
                    return True
        return False

    def get_player_visitor_reason(self, player_name: str, player_xuid: str | None = None) -> str:
        """获取玩家被降级为访客权限的具体原因"""
        if not self.plugin.config_manager.force_bind_qq:
            return ""

        if self.plugin.data_manager.is_player_banned(player_name, player_xuid):
            return "已被封禁"

        if not self.plugin.data_manager.is_player_bound(player_name, player_xuid):
            return "未绑定QQ"

        if self.plugin.config_manager.check_group_member:
            player_qq = self.plugin.data_manager.get_player_qq(player_name, player_xuid)
            if player_qq:
                is_in_any_group = False
                has_cached_groups = False
                for group in self.plugin.config_manager.groups:
                    group_id = group["id"]
                    if group_id in self.plugin.group_members:
                        has_cached_groups = True
                        if player_qq in self.plugin.group_members[group_id]:
                            is_in_any_group = True
                            break
                if has_cached_groups and not is_in_any_group:
                    return "已退出QQ群"
        return ""

    def _get_or_create_attachment(self, player: Any) -> Any:
        """获取玩家当前缓存的权限附件，若无则新建并录入缓存"""
        player_name = player.name
        if player_name not in self.attachment_cache:
            try:
                # 物理删除所有挂载在当前插件下的旧垃圾附件，防止多重挂载
                self._clear_plugin_attachments(player)
                # 创建单例挂载点
                attachment = player.add_attachment(self.plugin)
                self.attachment_cache[player_name] = attachment
            except Exception as e:
                self.logger.error(f"创建玩家 {player_name} 权限挂载附件失败: {e}")
                raise e
        return self.attachment_cache[player_name]

    def set_player_visitor_permissions(self, player: Any) -> bool:
        """一键激活限制，将玩家降级为访客只读权限"""
        try:
            attachment = self._get_or_create_attachment(player)

            # 激活 Endstone 黑名单权限组通配继承
            attachment.set_permission("qqsync.visitor", True)

            # 一键关闭行为节点权限
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
        """一键解除限制，恢复玩家所有正常的游戏权限"""
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
            self.logger.error(f"恢复玩家正常权限失败: {e}")
            return False

    def _clear_plugin_attachments(self, player: Any) -> None:
        """清理指定玩家名下挂载在本插件上的所有物理附件"""
        try:
            # 移除 effective_permissions 中属于本插件的物理残留
            to_remove = []
            for eff in player.effective_permissions:
                if (
                    hasattr(eff, "attachment")
                    and hasattr(eff.attachment, "plugin")
                    and eff.attachment.plugin == self.plugin
                ):
                    to_remove.append(eff.attachment)

            for attach in to_remove:
                try:
                    attach.remove()
                except Exception:
                    pass
        except Exception:
            pass

    def cleanup_player_permissions(self, player_name: str) -> None:
        """玩家彻底离线时物理销毁其在内存中缓存的权限挂载点"""
        if player_name in self.attachment_cache:
            try:
                self.attachment_cache[player_name].remove()
            except Exception:
                pass
            del self.attachment_cache[player_name]

    def check_and_apply_permissions(self, player: Any) -> None:
        """统一校验玩家绑定及入群状态，并应用相应的权限组属性"""
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

        meta = {
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

        cfg = meta.get(reason)
        if not cfg:
            return

        player.send_message(
            f"{ColorFormat.GRAY}[QQsync] {cfg['title_color']}{cfg['title']} 当前为您应用了只读的访客权限（原因: {reason}）{ColorFormat.RESET}"
        )
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}此时以下行为受限：{ColorFormat.RESET}")
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

        player.send_message(f"{ColorFormat.GRAY}[QQsync] {cfg['solution_color']}{cfg['solution']}{ColorFormat.RESET}")

        if reason == "已退出QQ群":
            groups = self.plugin.config_manager.groups
            if groups:
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
