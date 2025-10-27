"""
权限管理模块
负责玩家权限的管理，包括访客权限设置和恢复
"""

from typing import Dict, Any, List


class PermissionManager:
    """权限管理器"""
    
    def __init__(self, plugin, logger):
        self.plugin = plugin
        self.logger = logger
        self.player_attachments: Dict[str, Any] = {}  # 存储玩家权限附件
        self._color_format = None  # 延迟加载ColorFormat
    
    @property
    def color_format(self):
        """延迟加载ColorFormat以避免循环依赖"""
        if self._color_format is None:
            from endstone import ColorFormat
            self._color_format = ColorFormat
        return self._color_format
    
    def is_player_visitor(self, player_name: str, player_xuid: str = None) -> bool:
        """检查玩家是否为访客权限"""
        if not self.plugin.config_manager.get_config("force_bind_qq", True):
            return False  # 如果未启用强制绑定，所有玩家都不是访客
        
        # 检查玩家是否被封禁
        if self.plugin.data_manager.is_player_banned(player_name):
            return True
        
        # 检查玩家是否已绑定QQ
        if not self.plugin.data_manager.is_player_bound(player_name, player_xuid):
            return True
        
        # 检查已绑定的玩家是否退群
        player_qq = self.plugin.data_manager.get_player_qq(player_name)
        if player_qq and hasattr(self.plugin, 'group_members') and self.plugin.group_members:
            if player_qq not in self.plugin.group_members:
                # 避免重复日志输出
                if not hasattr(self.plugin, 'logged_left_players'):
                    self.plugin.logged_left_players = set()
                
                if player_qq not in self.plugin.logged_left_players:
                    self.logger.info(f"玩家 {player_name} (QQ: {player_qq}) 已退群，设置为访客权限")
                    self.plugin.logged_left_players.add(player_qq)
                
                return True
        
        return False
    
    def get_player_visitor_reason(self, player_name: str, player_xuid: str = None) -> str:
        """获取玩家被设为访客的原因"""
        if not self.plugin.config_manager.get_config("force_bind_qq", True):
            return ""
        
        # 检查玩家是否被封禁
        if self.plugin.data_manager.is_player_banned(player_name):
            return "已被封禁"
        
        if not self.plugin.data_manager.is_player_bound(player_name, player_xuid):
            return "未绑定QQ"
        
        # 只有在启用强制绑定和退群检测时才检查群成员状态
        if (self.plugin.config_manager.get_config("force_bind_qq", True) and 
            self.plugin.config_manager.get_config("check_group_member", True)):
            player_qq = self.plugin.data_manager.get_player_qq(player_name)
            if player_qq and hasattr(self.plugin, 'group_members') and self.plugin.group_members:
                if player_qq not in self.plugin.group_members:
                    return "已退出QQ群"
        
        return ""
    
    def set_player_visitor_permissions(self, player) -> bool:
        """设置玩家为访客权限（仅限制危险操作，不影响其他插件）"""
        try:
            # 清理现有的此插件权限附件
            self._clear_plugin_attachments(player)
            
            # 创建访客权限附件 - 采用黑名单模式，只禁止特定危险操作
            visitor_attachment = player.add_attachment(self.plugin)
            
            # 确保可以使用绑定命令
            visitor_attachment.set_permission("qqsync.command.bindqq", True)
            
            # 设置专门的权限为false（用于在事件处理器中检查）
            visitor_attachment.set_permission("qqsync.chat", False)
            visitor_attachment.set_permission("qqsync.destructive", False)
            visitor_attachment.set_permission("qqsync.block_place", False)
            visitor_attachment.set_permission("qqsync.item_use", False)
            visitor_attachment.set_permission("qqsync.item_pickup_drop", False)
            visitor_attachment.set_permission("qqsync.combat", False)
            
            # 定义需要禁止的权限类别
            restricted_permissions = {
                # 聊天相关权限
                'chat': [
                    "minecraft.command.say", "minecraft.command.tell", "minecraft.command.me",
                    "minecraft.command.msg", "minecraft.command.w", "minecraft.command.whisper",
                    "endstone.command.say", "endstone.command.tell", "endstone.command.me"
                ],
                # 破坏性操作权限
                'destructive': [
                    "minecraft.command.setblock", "minecraft.command.fill", "minecraft.command.clone",
                    "minecraft.command.give", "minecraft.command.clear", "minecraft.command.kill",
                    "minecraft.command.summon", "minecraft.command.gamemode", "minecraft.command.tp",
                    "minecraft.command.teleport", "endstone.command.setblock", "endstone.command.fill",
                    "endstone.command.give", "endstone.command.clear", "endstone.command.kill",
                    "endstone.command.gamemode", "endstone.command.tp"
                ],
                # 放置方块操作权限
                'block_place': [
                    "minecraft.place", "minecraft.place.block", "minecraft.block.place",
                    "minecraft.build", "minecraft.build.place", "minecraft.world.place",
                    "endstone.place", "endstone.place.block", "endstone.block.place",
                    "endstone.build", "endstone.build.place", "endstone.world.place",
                    "place", "place.block", "block.place", "build", "build.place"
                ],
                # 使用物品权限
                'item_use': [
                    "minecraft.use", "minecraft.use.item", "minecraft.item.use",
                    "minecraft.interact", "minecraft.interact.block", "minecraft.interact.item",
                    "minecraft.rightclick", "minecraft.click", "minecraft.activate",
                    "endstone.use", "endstone.use.item", "endstone.item.use",
                    "endstone.interact", "endstone.interact.block", "endstone.interact.item",
                    "endstone.rightclick", "endstone.click", "endstone.activate",
                    "use", "use.item", "item.use", "interact", "interact.block",
                    "interact.item", "rightclick", "click", "activate"
                ],
                # 拾取和丢弃权限
                'item_pickup_drop': [
                    "minecraft.pickup", "minecraft.pickup.item", "minecraft.item.pickup",
                    "minecraft.drop", "minecraft.drop.item", "minecraft.item.drop",
                    "minecraft.collect", "minecraft.collect.item", "minecraft.item.collect",
                    "minecraft.throw", "minecraft.throw.item", "minecraft.item.throw",
                    "endstone.pickup", "endstone.pickup.item", "endstone.item.pickup",
                    "endstone.drop", "endstone.drop.item", "endstone.item.drop",
                    "endstone.collect", "endstone.collect.item", "endstone.item.collect",
                    "endstone.throw", "endstone.throw.item", "endstone.item.throw",
                    "pickup", "pickup.item", "item.pickup", "drop", "drop.item",
                    "item.drop", "collect", "collect.item", "item.collect",
                    "throw", "throw.item", "item.throw"
                ],
                # 攻击相关权限
                'combat': [
                    "minecraft.interact.entity", "minecraft.attack.entity", "minecraft.damage.entity",
                    "minecraft.hit.entity", "minecraft.pvp", "minecraft.combat", "minecraft.hurt.entity",
                    "minecraft.kill.entity", "endstone.interact.entity", "endstone.attack.entity",
                    "endstone.damage.entity", "endstone.hit.entity", "endstone.pvp", "endstone.combat",
                    "endstone.hurt.entity", "endstone.kill.entity", "attack", "damage", "combat",
                    "pvp", "entity.attack", "entity.damage", "entity.hurt"
                ]
            }
            
            # 设置禁止权限
            for category, permissions in restricted_permissions.items():
                for perm in permissions:
                    visitor_attachment.set_permission(perm, False)
            
            # 存储权限附件以便后续管理
            self.player_attachments[player.name] = visitor_attachment
            
            # 重新计算权限
            player.recalculate_permissions()
            
            self.logger.info(f"已设置玩家 {player.name} 为访客权限（限制聊天、破坏性操作、放置方块、使用物品、拾取丢弃和攻击行为）")
            return True
                
        except Exception as e:
            self.logger.error(f"设置访客权限失败: {e}")
            return False
    
    def restore_player_permissions(self, player) -> bool:
        """恢复玩家的正常权限（仅移除访客限制，不影响其他权限）"""
        try:
            # 只清理由此插件创建的权限附件，保留其他插件的权限
            self._clear_plugin_attachments(player)
            
            # 不主动设置权限，让玩家使用服务器默认权限和其他插件权限
            # 这样可以避免与其他插件的权限管理发生冲突
            
            # 只确保我们的绑定命令权限存在（以防万一）
            player_attachment = player.add_attachment(self.plugin)
            player_attachment.set_permission("qqsync.command.bindqq", True)
            
            # 确保绑定玩家拥有所有权限
            player_attachment.set_permission("qqsync.chat", True)
            player_attachment.set_permission("qqsync.destructive", True)
            player_attachment.set_permission("qqsync.block_place", True)
            player_attachment.set_permission("qqsync.item_use", True)
            player_attachment.set_permission("qqsync.item_pickup_drop", True)
            player_attachment.set_permission("qqsync.combat", True)
            
            # 存储权限附件以便后续管理
            self.player_attachments[player.name] = player_attachment
            
            # 重新计算权限
            player.recalculate_permissions()
            
            self.logger.info(f"已为玩家 {player.name} 移除访客限制，恢复默认权限")
            return True
                
        except Exception as e:
            self.logger.error(f"恢复玩家权限失败: {e}")
            return False
    
    def _clear_plugin_attachments(self, player):
        """清理由此插件创建的权限附件（不影响其他插件的权限）"""
        try:
            # 清理之前存储的权限附件
            if player.name in self.player_attachments:
                try:
                    self.player_attachments[player.name].remove()
                    del self.player_attachments[player.name]
                    self.logger.info(f"已清理玩家 {player.name} 的 qqsync 权限附件")
                except Exception as e:
                    self.logger.warning(f"清理存储的权限附件失败: {e}")
            
            # 只移除由此插件创建的权限附件，保留其他插件的权限
            attachments_to_remove = []
            for attachment_info in player.effective_permissions:
                if (hasattr(attachment_info, 'attachment') and 
                    hasattr(attachment_info.attachment, 'plugin') and 
                    attachment_info.attachment.plugin == self.plugin):
                    attachments_to_remove.append(attachment_info.attachment)
            
            for attachment in attachments_to_remove:
                try:
                    attachment.remove()
                    self.logger.info(f"已移除玩家 {player.name} 的 qqsync 权限附件")
                except Exception as e:
                    self.logger.warning(f"移除权限附件失败: {e}")
                    
        except Exception as e:
            self.logger.warning(f"清理权限附件时出错: {e}")
    
    def cleanup_player_permissions(self, player_name: str):
        """清理离线玩家的权限附件"""
        try:
            if player_name in self.player_attachments:
                del self.player_attachments[player_name]
        except Exception as e:
            self.logger.warning(f"清理玩家 {player_name} 权限时出错: {e}")
    
    def check_and_apply_permissions(self, player):
        """检查并应用权限策略"""
        if not self.plugin.is_valid_player(player):
            self.logger.warning("尝试对已失效的玩家对象应用权限，操作已跳过")
            return
            
        if not self.plugin.config_manager.get_config("force_bind_qq", True):
            return  # 如果未启用强制绑定，不进行权限控制
        
        player_name = player.name
        visitor_reason = self.get_player_visitor_reason(player_name, player.xuid)
        
        if not visitor_reason:
            # 玩家有正常权限，移除访客限制
            self.restore_player_permissions(player)
            ColorFormat = self.color_format
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] 您已绑定QQ且在群内，拥有完整游戏权限{ColorFormat.RESET}")
        else:
            # 玩家应该是访客权限，设置相应限制
            self.set_player_visitor_permissions(player)
            self._send_visitor_notification(player, visitor_reason)
    
    def _send_visitor_notification(self, player, visitor_reason: str):
        """向访客玩家发送权限限制通知"""
        ColorFormat = self.color_format
        
        if visitor_reason == "已被封禁":
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}[封禁] 您当前为访客权限（原因：已被封禁）{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}以下功能受限：{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法聊天{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法破坏/放置方块{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法使用物品{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法拾取/丢弃物品{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法与容器/机器交互{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法攻击生物或实体{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}")
        elif visitor_reason == "未绑定QQ":
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}[警告] 您当前为访客权限（原因：未绑定QQ）{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}以下功能受限：{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法聊天{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法破坏/放置方块{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法使用物品{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法拾取/丢弃物品{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法与容器/机器交互{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法攻击生物或实体{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}解决方案：使用命令 /bindqq 绑定QQ{ColorFormat.RESET}")
        elif visitor_reason == "已退出QQ群":
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}[警告] 您当前为访客权限（原因：已退出QQ群）{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}以下功能受限：{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法聊天{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法破坏/放置方块{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法使用物品{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法拾取/丢弃物品{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法与容器/机器交互{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法攻击生物或实体{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}解决方案：重新加入QQ群后权限将自动恢复{ColorFormat.RESET}")
            
            # 显示QQ群信息
            target_groups = self.plugin.config_manager.get_config("target_groups", [])
            group_names = self.plugin.config_manager.get_config("group_names", {})
            
            if target_groups:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}目标QQ群：{ColorFormat.RESET}")
                for group_id in target_groups:
                    group_name = group_names.get(str(group_id), "")
                    if group_name:
                        player.send_message(f"{ColorFormat.GRAY}  • {group_id} ({group_name}){ColorFormat.RESET}")
                    else:
                        player.send_message(f"{ColorFormat.GRAY}  • {group_id}{ColorFormat.RESET}")
    
    def send_ban_notification(self, player, ban_reason: str, ban_by: str, ban_time: int):
        """向被封禁的玩家发送封禁通知"""
        try:
            from ..utils.helpers import format_timestamp
            ColorFormat = self.color_format
            
            # 格式化封禁时间
            ban_time_str = format_timestamp(ban_time) if ban_time else "未知时间"
            
            # 发送封禁通知消息
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}==================== 封禁通知 ===================={ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您已被服务器封禁，无法绑定QQ{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}封禁时间：{ban_time_str}{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}操作者：{ban_by}{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}封禁原因：{ban_reason}{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}如有疑问请联系管理员{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}==========================================={ColorFormat.RESET}")
            
            # 记录日志
            self.logger.info(f"已向被封禁玩家 {player.name} 发送封禁通知")
            
        except Exception as e:
            self.logger.error(f"发送封禁通知失败: {e}")
