"""
事件处理模块
负责处理游戏内的各种事件
"""

import time
from collections import defaultdict, deque
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
from endstone.lang import Language,Translatable

class EventHandlers:
    """事件处理器"""
    
    def __init__(self, plugin):
        self.plugin = plugin
        self.logger = plugin.logger
        
        # 刷屏检测配置 - 简化为两个关键参数
        self.chat_count_limit = plugin.config_manager.get_config("chat_count_limit", 20)  # 1分钟内最多发送消息数
        self.chat_ban_time = plugin.config_manager.get_config("chat_ban_time", 300)       # 刷屏后禁言时间（秒）
        self.spam_window = 60  # 固定1分钟时间窗口
        
        # 玩家聊天记录
        self.player_last_chat = {}  # 玩家最后聊天时间
        self.player_chat_history = defaultdict(deque)  # 玩家聊天历史记录
        self.player_spam_penalty = {}  # 玩家刷屏惩罚结束时间
    
    def check_chat_cooldown(self, player_name):
        """检查玩家聊天冷却 - 简化版本，主要检查刷屏惩罚"""
        current_time = time.time()
        
        # 检查是否是管理员（管理员绕过刷屏检测）
        if self._is_admin_player(player_name):
            return True, ""
        
        # 检查是否在刷屏惩罚期间
        if player_name in self.player_spam_penalty:
            penalty_end = self.player_spam_penalty[player_name]
            if current_time < penalty_end:
                remaining = int(penalty_end - current_time)
                minutes = remaining // 60
                seconds = remaining % 60
                if minutes > 0:
                    time_str = f"{minutes}分{seconds}秒"
                else:
                    time_str = f"{seconds}秒"
                return False, f"您正在刷屏惩罚中，还需等待 {time_str}"
            else:
                # 惩罚期结束，移除记录
                del self.player_spam_penalty[player_name]
        
        return True, ""
    
    def check_spam_detection(self, player_name):
        """检查刷屏行为 - 使用简化配置"""
        current_time = time.time()
        
        # 如果 chat_count_limit 为 -1，则不限制聊天频率
        if self.chat_count_limit == -1:
            return False, ""
        
        # 管理员不受刷屏限制
        if self._is_admin_player(player_name):
            return False, ""
        
        # 获取玩家聊天历史
        chat_history = self.player_chat_history[player_name]
        
        # 清理过期记录（超过1分钟的记录）
        while chat_history and current_time - chat_history[0] > self.spam_window:
            chat_history.popleft()
        
        # 添加当前聊天时间
        chat_history.append(current_time)
        
        # 检查是否超过1分钟内消息数量限制
        if len(chat_history) > self.chat_count_limit:
            # 触发刷屏惩罚
            self.player_spam_penalty[player_name] = current_time + self.chat_ban_time
            self.player_chat_history[player_name].clear()  # 清空聊天历史
            
            ban_minutes = self.chat_ban_time // 60
            self.logger.warning(f"玩家 {player_name} 触发刷屏检测，被禁言 {ban_minutes} 分钟")
            return True, f"检测到刷屏行为，您被禁言 {ban_minutes} 分钟"
        
        return False, ""
    
    def _is_admin_player(self, player_name):
        """检查玩家是否是管理员"""
        try:
            # 通过QQ绑定信息检查是否是管理员
            qq_number = self.plugin.data_manager.get_player_qq(player_name)
            if qq_number:
                admins = self.plugin.config_manager.get_config("admins", [])
                return qq_number in admins
            return False
        except Exception as e:
            self.logger.error(f"检查管理员状态失败: {e}")
            return False
    
    def update_chat_time(self, player_name):
        """更新玩家最后聊天时间"""
        self.player_last_chat[player_name] = time.time()
    
    def cleanup_player_chat_data(self, player_name):
        """清理玩家聊天相关数据"""
        if player_name in self.player_last_chat:
            del self.player_last_chat[player_name]
        if player_name in self.player_chat_history:
            del self.player_chat_history[player_name]
        if player_name in self.player_spam_penalty:
            del self.player_spam_penalty[player_name]
    
    @event_handler
    def on_player_join(self, event: PlayerJoinEvent):
        """玩家加入事件"""
        try:
            player = event.player
            player_name = player.name
            player_xuid = player.xuid
            
            self.logger.info(f"玩家 {player_name} (XUID: {player_xuid}) 加入游戏")
            
            # 记录玩家加入时间和进服次数（使用join/quit事件记录）
            self.plugin.data_manager.update_player_join(player_name, player_xuid)
            
            # 注意：在线时长累计现在由独立的计时器系统处理，不再依赖join/quit事件
            
            # 检查玩家名称是否发生变化（处理改名）
            existing_player = self.plugin.data_manager.get_player_by_xuid(player_xuid)
            if existing_player and existing_player.get("name") != player_name:
                old_name = existing_player.get("name")
                if self.plugin.data_manager.update_player_name(old_name, player_name, player_xuid):
                    # 更新QQ群昵称
                    if (hasattr(self.plugin, '_current_ws') and self.plugin._current_ws and 
                        self.plugin.config_manager.get_config("force_bind_qq", True) and 
                        self.plugin.config_manager.get_config("sync_group_card", True)):
                        
                        qq_number = existing_player.get("qq")
                        if qq_number:
                            import asyncio
                            from ..websocket.handlers import set_group_card_in_all_groups
                            asyncio.run_coroutine_threadsafe(
                                set_group_card_in_all_groups(self.plugin._current_ws, user_id=int(qq_number), card=player_name),
                                self.plugin._loop
                            )
            
            # 延迟检查和应用权限（给系统一些时间完成初始化）
            self.plugin.server.scheduler.run_task(
                self.plugin,
                lambda: self.plugin.permission_manager.check_and_apply_permissions(player) if self.plugin.is_valid_player(player) else None,
                delay=20  # 1秒延迟
            )
            
            # 检查未绑定玩家是否需要自动弹出绑定表单
            if (self.plugin.config_manager.get_config("force_bind_qq", True) and 
                not self.plugin.data_manager.is_player_bound(player_name, player_xuid)):
                
                # 延迟显示绑定表单（给玩家更多时间加载完成）
                self.plugin.server.scheduler.run_task(
                    self.plugin,
                    lambda: self._show_auto_binding_form(player) if self.plugin.is_valid_player(player) else None,
                    delay=60  # 3秒延迟，确保玩家完全加载
                )

            # 发送QQ群通知（现在为所有玩家发送通知，不再依赖绑定状态）
            if (hasattr(self.plugin, '_current_ws') and self.plugin._current_ws and 
                self.plugin.config_manager.get_config("enable_game_to_qq", True)):
                
                import asyncio
                from ..websocket.handlers import send_group_msg_to_all_groups
                
                # 获取玩家统计信息
                playtime_info = self.plugin.data_manager.get_player_playtime_info(player_name, self.plugin.server.online_players)
                session_count = playtime_info.get("session_count", 0)
                
                if session_count == 1:
                    join_msg = f"🌟 玩家 {player_name} 首次进入服务器！"
                else:
                    join_msg = f"🟢 玩家 {player_name} 加入游戏 (第{session_count}次游戏)"
                
                asyncio.run_coroutine_threadsafe(
                    send_group_msg_to_all_groups(self.plugin._current_ws, text=join_msg),
                    self.plugin._loop
                )
                
        except Exception as e:
            self.logger.error(f"处理玩家加入事件失败: {e}")
    
    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent):
        """玩家离开事件"""
        try:
            player = event.player
            player_name = player.name
            player_xuid = player.xuid
            
            self.logger.info(f"玩家 {player_name} (XUID: {player_xuid}) 离开游戏")
            
            # 记录玩家退出时间（使用join/quit事件记录）
            self.plugin.data_manager.update_player_quit(player_name)
            
            # 注意：在线时长累计现在由独立的计时器系统处理，不再依赖join/quit事件
            
            # 清理玩家相关缓存
            self.plugin.permission_manager.cleanup_player_permissions(player_name)
            if hasattr(self.plugin, 'verification_manager'):
                self.plugin.verification_manager.cleanup_player_data(player_name)
            
            # 清理聊天相关数据
            self.cleanup_player_chat_data(player_name)
            
            # 发送QQ群通知（现在为所有玩家发送通知，不再依赖绑定状态）
            if (hasattr(self.plugin, '_current_ws') and self.plugin._current_ws and 
                self.plugin.config_manager.get_config("enable_game_to_qq", True)):
                
                import asyncio
                from ..websocket.handlers import send_group_msg_to_all_groups
                
                # 获取玩家统计信息
                playtime_info = self.plugin.data_manager.get_player_playtime_info(player_name, [])  # 玩家已离线，传入空列表
                total_playtime = playtime_info.get("total_playtime", 0)                # 格式化游戏时长
                hours = total_playtime // 3600
                minutes = (total_playtime % 3600) // 60
            
                if hours > 0:
                    playtime_str = f"{hours}小时{minutes}分钟"
                else:
                    playtime_str = f"{minutes}分钟"
            
                quit_msg = f"🔴 玩家 {player_name} 离开游戏 (总游戏时长: {playtime_str})"
            
                asyncio.run_coroutine_threadsafe(
                    send_group_msg_to_all_groups(self.plugin._current_ws, text=quit_msg),
                    self.plugin._loop
                )
                
        except Exception as e:
            self.logger.error(f"处理玩家离开事件失败: {e}")
    
    @event_handler
    def on_player_chat(self, event: PlayerChatEvent):
        """玩家聊天事件"""
        try:
            player = event.player
            player_name = player.name
            message = event.message
            
            # 过滤掉命令消息，不对命令进行检查
            if message.startswith('/'):
                return
            
            # 检查刷屏冷却机制（包含惩罚检查）
            can_chat, cooldown_msg = self.check_chat_cooldown(player_name)
            if not can_chat:
                # 取消聊天事件
                event.is_cancelled = True
                
                # 发送提示消息
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}{cooldown_msg}{ColorFormat.RESET}")
                return
            
            # 检查刷屏行为
            is_spam, spam_msg = self.check_spam_detection(player_name)
            if is_spam:
                # 取消聊天事件
                event.is_cancelled = True
                
                # 发送刷屏警告消息
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}{spam_msg}{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请文明聊天，避免刷屏行为{ColorFormat.RESET}")
                return
            
            # 更新玩家最后聊天时间
            self.update_chat_time(player_name)
            
            # 检查访客权限限制
            if self.plugin.config_manager.get_config("force_bind_qq", True):
                # 检查玩家是否有聊天权限
                if not player.has_permission("qqsync.chat"):
                    # 取消聊天事件，阻止消息发送
                    event.is_cancelled = True
                    
                    # 给玩家发送提示消息
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您需要绑定QQ后才能聊天！{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请使用 /bindqq 命令进行QQ绑定{ColorFormat.RESET}")
                    return
            
            # 转发到QQ群（根据 force_bind_qq 配置决定是否必须绑定）
            if (hasattr(self.plugin, '_current_ws') and self.plugin._current_ws and 
                self.plugin.config_manager.get_config("enable_game_to_qq", True) and
                (self.plugin.data_manager.is_player_bound(player_name, player.xuid) or not self.plugin.config_manager.get_config("force_bind_qq", True))):
                
                import asyncio
                from ..websocket.handlers import send_group_msg_to_all_groups
                from ..utils.message_utils import filter_sensitive_content
                
                # 过滤敏感内容
                filtered_message, has_sensitive = filter_sensitive_content(message)
                
                # 获取玩家绑定的QQ号（如果有的话）
                player_qq = self.plugin.data_manager.get_player_qq(player_name)
                
                # 构建聊天消息
                chat_msg = f"💬 {player_name}: {filtered_message}"
                
                # 如果包含敏感内容，记录日志
                if has_sensitive:
                    self.logger.warning(f"玩家 {player_name} 发送了包含敏感内容的消息，已过滤: {message}")
                
                # 发送过滤后的消息到QQ群
                asyncio.run_coroutine_threadsafe(
                    send_group_msg_to_all_groups(self.plugin._current_ws, text=chat_msg),
                    self.plugin._loop
                )

                # 为webui写入聊天历史记录
                webui = self.plugin.server.plugin_manager.get_plugin('qqsync_webui_plugin')
                if webui:
                    try:
                        webui.on_message_sent(sender=player_name, content=message, msg_type="chat", direction="game_to_qq")
                    except Exception as e:
                        self.logger.warning(f"webui on_message_snet调用失败: {e}")
                
        except Exception as e:
            self.logger.error(f"处理玩家聊天事件失败: {e}")
    
    @event_handler
    def on_player_death(self, event: PlayerDeathEvent):
        """玩家死亡事件"""
        try:
            player = event.player
            player_name = player.name

            # 转发到QQ群（如果启用且玩家已绑定）
            if (hasattr(self.plugin, '_current_ws') and self.plugin._current_ws and 
                self.plugin.config_manager.get_config("enable_game_to_qq", True) and
                self.plugin.data_manager.is_player_bound(player_name, player.xuid)):
                
                import asyncio
                from ..websocket.handlers import send_group_msg_to_all_groups

                # 构建死亡消息
                language = event.player.server.language
                if language.locale == "zh_CN":
                    # 翻译功能有点问题无论如何怎么翻都无法翻出目的语言，服务端没指定语言就用通用亡语
                    death_msg_to_be_translate = event.death_message
                    death_msg = language.translate(death_msg_to_be_translate,language.locale)
                else:
                    death_msg = f"💀 {player_name} 死了"
                
                # 发送消息到QQ群
                asyncio.run_coroutine_threadsafe(
                    send_group_msg_to_all_groups(self.plugin._current_ws, text=death_msg),
                    self.plugin._loop
                )
                
        except Exception as e:
            self.logger.error(f"处理玩家死亡事件失败: {e}")
    
    # 其他事件处理方法可以根据需要添加
    @event_handler
    def on_block_break(self, event: BlockBreakEvent):
        """方块破坏事件 - 权限检查"""
        try:
            if not self.plugin.config_manager.get_config("force_bind_qq", True):
                return  # 未启用强制绑定，不进行权限控制
                
            player = event.player
            
            # 检查破坏性操作权限
            if not player.has_permission("qqsync.destructive"):
                # 取消事件
                event.is_cancelled = True
                
                # 发送提示消息
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您需要绑定QQ后才能破坏方块！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请使用 /bindqq 命令进行QQ绑定{ColorFormat.RESET}")
                return
                
        except Exception as e:
            self.logger.error(f"处理方块破坏事件失败: {e}")
    
    @event_handler
    def on_block_place(self, event: BlockPlaceEvent):
        """方块放置事件 - 权限检查"""
        try:
            if not self.plugin.config_manager.get_config("force_bind_qq", True):
                return  # 未启用强制绑定，不进行权限控制
                
            player = event.player
            
            # 检查方块放置权限
            if not player.has_permission("qqsync.block_place"):
                # 取消事件
                event.is_cancelled = True
                
                # 发送提示消息
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您需要绑定QQ后才能放置方块！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请使用 /bindqq 命令进行QQ绑定{ColorFormat.RESET}")
                return
                
        except Exception as e:
            self.logger.error(f"处理方块放置事件失败: {e}")
    
    @event_handler
    def on_player_interact(self, event: PlayerInteractEvent):
        """玩家交互事件 - 权限检查"""
        try:
            if not self.plugin.config_manager.get_config("force_bind_qq", True):
                return  # 未启用强制绑定，不进行权限控制
                
            player = event.player
            
            # 检查物品使用权限
            if not player.has_permission("qqsync.item_use"):
                # 取消事件
                event.is_cancelled = True
                
                # 发送提示消息
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您需要绑定QQ后才能进行该操作！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请使用 /bindqq 命令进行QQ绑定{ColorFormat.RESET}")
                return
                
        except Exception as e:
            self.logger.error(f"处理玩家交互事件失败: {e}")
    
    @event_handler
    def on_player_interact_actor(self, event: PlayerInteractActorEvent):
        """玩家与实体交互事件 - 权限检查"""
        try:
            if not self.plugin.config_manager.get_config("force_bind_qq", True):
                return  # 未启用强制绑定，不进行权限控制
                
            player = event.player
            
            # 检查攻击/交互权限
            if not player.has_permission("qqsync.combat"):
                # 取消事件
                event.is_cancelled = True
                
                # 发送提示消息
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您需要绑定QQ后才能与实体交互！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请使用 /bindqq 命令进行QQ绑定{ColorFormat.RESET}")
                return
                
        except Exception as e:
            self.logger.error(f"处理玩家与实体交互事件失败: {e}")
    
    @event_handler
    def on_actor_damage(self, event: ActorDamageEvent):
        """实体受伤事件 - 权限检查"""
        try:
            if not self.plugin.config_manager.get_config("force_bind_qq", True):
                return  # 未启用强制绑定，不进行权限控制
            
            # 检查伤害来源是否是玩家
            damage_source = event.damage_source
            
            # 只有当伤害来源是玩家时，才进行权限检查
            # 生物攻击玩家或其他实体时不受此限制
            if hasattr(damage_source, 'actor') and damage_source.actor:
                damager = damage_source.actor
                
                # 检查是否是玩家：只检查玩家特有的属性
                # 玩家会有 name、xuid 和 has_permission 方法
                if (hasattr(damager, 'name') and hasattr(damager, 'xuid') and 
                    hasattr(damager, 'has_permission') and callable(getattr(damager, 'has_permission', None))):
                    
                    # 检查攻击权限
                    if not damager.has_permission("qqsync.combat"):
                        # 取消事件，阻止玩家攻击
                        event.is_cancelled = True
                        
                        # 发送提示消息
                        damager.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您需要绑定QQ后才能攻击实体！{ColorFormat.RESET}")
                        damager.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请使用 /bindqq 命令进行QQ绑定{ColorFormat.RESET}")
                        return
                
        except Exception as e:
            self.logger.error(f"处理实体受伤事件失败: {e}")
    
    @event_handler
    def on_player_pickup_item(self, event: PlayerPickupItemEvent):
        """玩家拾取物品事件 - 权限检查"""
        try:
            if not self.plugin.config_manager.get_config("force_bind_qq", True):
                return  # 未启用强制绑定，不进行权限控制
                
            player = event.player
            
            # 检查拾取权限
            if not player.has_permission("qqsync.item_pickup_drop"):
                # 取消事件
                event.is_cancelled = True
                
                # 发送提示消息
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您需要绑定QQ后才能拾取物品！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请使用 /bindqq 命令进行QQ绑定{ColorFormat.RESET}")
                return
                
        except Exception as e:
            self.logger.error(f"处理玩家拾取物品事件失败: {e}")
    
    @event_handler
    def on_player_drop_item(self, event: PlayerDropItemEvent):
        """玩家丢弃物品事件 - 权限检查"""
        try:
            if not self.plugin.config_manager.get_config("force_bind_qq", True):
                return  # 未启用强制绑定，不进行权限控制
                
            player = event.player
            
            # 检查丢弃权限
            if not player.has_permission("qqsync.item_pickup_drop"):
                # 取消事件
                event.is_cancelled = True
                
                # 发送提示消息
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您需要绑定QQ后才能丢弃物品！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请使用 /bindqq 命令进行QQ绑定{ColorFormat.RESET}")
                return
                
        except Exception as e:
            self.logger.error(f"处理玩家丢弃物品事件失败: {e}")
    
    def _show_auto_binding_form(self, player):
        """为未绑定的玩家自动显示绑定表单"""
        try:
            
            # 再次检查玩家是否有效且未绑定
            if not self.plugin.is_valid_player(player):
                return
            
            if self.plugin.data_manager.is_player_bound(player.name, player.xuid):
                return  # 玩家已经绑定了，不需要显示表单
            
            # 检查玩家是否被封禁
            if self.plugin.data_manager.is_player_banned(player.name):
                self.logger.info(f"玩家 {player.name} 已被封禁，不显示绑定表单")
                return  # 已被封禁的玩家不需要显示绑定表单
            
            # 显示绑定表单
            self.plugin.ui_manager.show_qq_binding_form(player)
            
            self.logger.info(f"已为未绑定玩家 {player.name} 自动显示绑定表单")
            
        except Exception as e:
            self.logger.error(f"自动显示绑定表单失败: {e}")