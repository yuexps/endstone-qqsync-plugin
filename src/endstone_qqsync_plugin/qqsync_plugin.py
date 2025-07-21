from endstone.plugin import Plugin
from endstone import ColorFormat
from endstone.event import (
    event_handler,
    PlayerChatEvent,
    PlayerJoinEvent,
    PlayerQuitEvent,
    PlayerDeathEvent,
    PlayerInteractEvent,
    PlayerInteractActorEvent,
    BlockBreakEvent,
    BlockPlaceEvent,
    ActorDamageEvent,
)
from endstone.form import (
    ModalForm,
    Label,
    TextInput,
)

import asyncio
import threading
import json
import random
import time
import os
import sys
import re
import datetime
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))
import websockets

_current_ws = None
_plugin_instance = None
# QQ绑定相关的全局变量
_pending_verifications = {}  # 存储待验证的信息: {player_name: {"qq": qq_number, "code": verification_code, "timestamp": time}}
_verification_codes = {}     # 存储验证码: {qq_number: {"code": code, "timestamp": time, "player_name": name}}

class qqsync(Plugin):

    api_version = "0.6"
    
    # 定义命令
    commands = {
        "bindqq": {
            "description": "QQ绑定相关命令",
            "usages": ["/bindqq"],
            "aliases": ["qq"],
            "permissions": ["qqsync.command.bindqq"]
        }
    }
    
    # 定义权限
    permissions = {
        "qqsync.command.bindqq": {
            "description": "允许使用 /bindqq 命令",
            "default": True
        }
    }
    
    def on_load(self) -> None:
        self.logger.info(f"{ColorFormat.BLUE}qqsync_plugin {ColorFormat.WHITE}正在加载...{ColorFormat.RESET}")
    
    def on_enable(self) -> None:
        global _plugin_instance
        _plugin_instance = self
        
        self._init_config()
        self._init_bindqq_data()
        
        # 初始化权限附件存储
        self._player_attachments = {}
        
        # 初始化群成员缓存
        self._group_members = set()  # 存储群成员QQ号的集合
        
        # 初始化日志缓存（避免重复日志输出）
        self._logged_left_players = set()  # 存储已记录退群日志的QQ号
        
        # 初始化文件写入优化
        self._pending_data_save = False  # 是否有待保存的数据
        self._last_save_time = 0  # 上次保存时间
        self._save_interval = 0.02  # 保存间隔（20毫秒）
        self._force_save_interval = 0.1  # 强制保存间隔（100毫秒）
        
        # 实时保存优化
        self._auto_save_enabled = True  # 是否启用自动保存
        self._save_on_change = True  # 数据变更时立即保存
        self._max_pending_changes = 5  # 最大待保存变更数量
        self._pending_changes_count = 0  # 当前待保存变更计数
        
        # 初始化多玩家处理优化
        self._verification_queue = {}  # 验证码发送队列：{qq: 发送时间}
        self._binding_rate_limit = {}  # 绑定频率限制：{qq: 上次绑定时间}
        self._form_display_cache = {}  # 表单显示缓存：{player_name: 显示时间}
        self._form_display_count = {}  # 表单显示次数计数：{player_name: 显示次数}
        self._pending_qq_confirmations = {}  # 待确认的QQ信息：{player_name: {qq: str, nickname: str, timestamp: float}}
        self._concurrent_bindings = set()  # 当前正在进行绑定的玩家
        self._max_concurrent_bindings = 25  # 最大并发绑定数量
        self._verification_rate_limit = 15  # 每分钟最多发送验证码数量
        self._binding_cooldown = 15  # 绑定失败后的冷却时间（秒）（减少等待时间）
        
        # 40人服务器专用：智能队列管理
        self._binding_queue = []  # 绑定队列：[(player_name, qq_number, request_time)]
        self._queue_notification_sent = set()  # 已发送排队通知的玩家

        #注册事件
        self.register_events(self)
        
        # 创建专用事件循环
        self._loop = asyncio.new_event_loop()
        
        # 在新线程里启动该循环
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        # 把协程提交到该循环
        future = asyncio.run_coroutine_threadsafe(connect_forever(), self._loop)
        self._task = future

        # 启动定时清理任务（每5分钟执行一次）
        self.server.scheduler.run_task(
            self,
            self._schedule_cleanup,
            delay=6000,  # 首次执行延迟5分钟
            period=6000  # 每5分钟执行一次
        )
        
        # 启动群成员检查任务（更早开始，每10分钟执行一次）
        self.server.scheduler.run_task(
            self,
            self._schedule_group_check,
            delay=200,   # 首次执行延迟10秒，更早获取群成员列表
            period=12000  # 每10分钟执行一次
        )

        # 启动周期性清理任务（多玩家优化）
        self.server.scheduler.run_task(
            self,
            self._schedule_multi_player_cleanup,
            delay=30,    # 30秒后首次执行
            period=300   # 每5分钟执行一次清理
        )

        # 启动绑定队列处理任务
        self.server.scheduler.run_task(
            self,
            self._process_binding_queue,
            delay=5,     # 5秒后首次执行
            period=20    # 每1秒检查一次队列
        )

        startup_msg = f"{ColorFormat.GREEN}qqsync_plugin {ColorFormat.YELLOW}已启用{ColorFormat.RESET}"
        self.logger.info(startup_msg)
        welcome_msg = f"{ColorFormat.BLUE}欢迎使用QQsync群服互通插件，{ColorFormat.YELLOW}作者：yuexps{ColorFormat.RESET}"
        self.logger.info(welcome_msg)
        
    def _init_config(self):
        """初始化配置文件"""
        # 配置文件路径
        self.config_file = Path(self.data_folder) / "config.json"
        
        # 默认配置
        self.default_config = {
            "napcat_ws": "ws://localhost:3001",
            "access_token": "",
            "target_group": 712523104,
            "admins": ["2899659758"],
            "enable_qq_to_game": True,
            "enable_game_to_qq": True,
            "force_bind_qq": True,
            "sync_group_card": True,
            "check_group_member": True  # 是否启用退群检测
        }
        
        # 如果配置文件不存在，创建默认配置
        if not self.config_file.exists():
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.default_config, f, indent=2, ensure_ascii=False)
            self.logger.info(f"已创建默认配置文件: {self.config_file}")
        
        # 读取配置
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self._config = json.load(f)
        except Exception as e:
            self.logger.error(f"读取配置文件失败: {e}")
            self._config = self.default_config.copy()
        
        # 检查并合并新的配置项
        config_updated = False
        for key, value in self.default_config.items():
            if key not in self._config:
                self._config[key] = value
                config_updated = True
                self.logger.info(f"添加新配置项: {key}")
            elif key == "help_msg":
                # help_msg现在是动态生成的，不需要从默认配置更新
                continue
        
        # 生成动态帮助信息
        self._config["help_msg"] = self._generate_help_message()
        
        # 如果有新配置项，保存到文件
        if config_updated:
            try:
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(self._config, f, indent=2, ensure_ascii=False)
                self.logger.info("配置文件已更新并保存")
            except Exception as e:
                self.logger.error(f"保存更新的配置文件失败: {e}")
        
        self.logger.info(f"{ColorFormat.AQUA}配置文件已加载{ColorFormat.RESET}")
        self.logger.info(f"{ColorFormat.GOLD}NapCat WebSocket: {ColorFormat.WHITE}{self._config.get('napcat_ws')}{ColorFormat.RESET}")
        self.logger.info(f"{ColorFormat.GOLD}目标QQ群: {ColorFormat.WHITE}{self._config.get('target_group')}{ColorFormat.RESET}")
        self.logger.info(f"{ColorFormat.GOLD}管理员列表: {ColorFormat.WHITE}{self._config.get('admins')}{ColorFormat.RESET}")
        self.logger.info(f"{ColorFormat.GOLD}QQ消息转发: {ColorFormat.WHITE}{'启用' if self._config.get('enable_qq_to_game', True) else '禁用'}{ColorFormat.RESET}")
        self.logger.info(f"{ColorFormat.GOLD}游戏消息转发: {ColorFormat.WHITE}{'启用' if self._config.get('enable_game_to_qq', True) else '禁用'}{ColorFormat.RESET}")
        
        force_bind_enabled = self._config.get('force_bind_qq', True)
        sync_card_enabled = self._config.get('sync_group_card', True)
        check_group_enabled = self._config.get('check_group_member', True)
        
        if force_bind_enabled:
            self.logger.info(f"{ColorFormat.GOLD}强制QQ绑定: {ColorFormat.WHITE}启用 {ColorFormat.YELLOW}(未绑定玩家将限制为访客权限){ColorFormat.RESET}")
            
            if sync_card_enabled:
                self.logger.info(f"{ColorFormat.GOLD}同步群昵称: {ColorFormat.WHITE}启用 {ColorFormat.YELLOW}(绑定成功后自动设置群昵称){ColorFormat.RESET}")
            else:
                self.logger.info(f"{ColorFormat.GOLD}同步群昵称: {ColorFormat.WHITE}禁用{ColorFormat.RESET}")
            
            if check_group_enabled:
                self.logger.info(f"{ColorFormat.GOLD}退群检测: {ColorFormat.WHITE}启用 {ColorFormat.YELLOW}(退群玩家将自动设为访客权限){ColorFormat.RESET}")
            else:
                self.logger.info(f"{ColorFormat.GOLD}退群检测: {ColorFormat.WHITE}禁用{ColorFormat.RESET}")
        else:
            self.logger.info(f"{ColorFormat.GOLD}强制QQ绑定: {ColorFormat.WHITE}禁用 {ColorFormat.YELLOW}(所有玩家享有完整权限){ColorFormat.RESET}")
            self.logger.info(f"{ColorFormat.GOLD}同步群昵称: {ColorFormat.WHITE}禁用 {ColorFormat.GRAY}(依赖强制QQ绑定){ColorFormat.RESET}")
            self.logger.info(f"{ColorFormat.GOLD}退群检测: {ColorFormat.WHITE}禁用 {ColorFormat.GRAY}(依赖强制QQ绑定){ColorFormat.RESET}")
    
    def _generate_help_message(self):
        """根据当前配置动态生成帮助信息"""
        force_bind_enabled = self.get_config("force_bind_qq", True)
        
        if force_bind_enabled:
            # 如果启用强制绑定，显示完整的绑定相关功能
            return "QQsync群服互通 - 命令：\n\n[查询命令]（所有用户可用）：\n/help — 显示本帮助信息\n/list — 查看在线玩家列表\n/tps — 查看服务器性能指标\n/info — 查看服务器综合信息\n/bindqq — 查看QQ绑定状态\n/verify <验证码> — 验证QQ绑定\n\n[管理命令]（仅管理员可用）：\n/cmd <命令> — 执行服务器命令\n/who <玩家名|QQ号> — 查询玩家详细信息（绑定状态、游戏统计、权限状态等）\n/unbindqq <玩家名|QQ号> — 解绑玩家的QQ绑定\n/ban <玩家名> [原因] — 封禁玩家，禁止QQ绑定\n/unban <玩家名> — 解除玩家封禁\n/banlist — 查看封禁列表\n/tog_qq — 切换QQ消息转发开关 \n/tog_game — 切换游戏转发开关\n/reload — 重新加载配置文件"
        else:
            # 如果未启用强制绑定，不显示绑定和封禁相关功能
            return "QQsync群服互通 - 命令：\n\n[查询命令]（所有用户可用）：\n/help — 显示本帮助信息\n/list — 查看在线玩家列表\n/tps — 查看服务器性能指标\n/info — 查看服务器综合信息\n\n[管理命令]（仅管理员可用）：\n/cmd <命令> — 执行服务器命令\n/who <玩家名> — 查询玩家详细信息（游戏统计等）\n/tog_qq — 切换QQ消息转发开关 \n/tog_game — 切换游戏转发开关\n/reload — 重新加载配置文件"
        
    def _init_bindqq_data(self):
        """初始化QQ绑定数据文件"""
        self.binding_file = Path(self.data_folder) / "data.json"
        
        # 如果绑定数据文件不存在，创建空数据
        if not self.binding_file.exists():
            self.binding_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.binding_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=2, ensure_ascii=False)
            self.logger.info(f"已创建QQ绑定数据文件: {self.binding_file}")
        
        # 读取绑定数据
        try:
            with open(self.binding_file, 'r', encoding='utf-8') as f:
                self._binding_data = json.load(f)
        except Exception as e:
            self.logger.error(f"读取QQ绑定数据失败: {e}")
            self._binding_data = {}
        
        # 更新旧数据结构兼容性
        data_updated = False
        invalid_bindings = []  # 存储无效绑定的玩家名
        
        for player_name, data in self._binding_data.items():
            if "total_playtime" not in data:
                data["total_playtime"] = 0
                data_updated = True
            if "last_join_time" not in data:
                data["last_join_time"] = None
                data_updated = True
            if "last_quit_time" not in data:
                data["last_quit_time"] = None
                data_updated = True
            if "session_count" not in data:
                data["session_count"] = 0
                data_updated = True
            
            # 检查并清理无效的QQ绑定（QQ号为空且没有绑定时间的记录）
            qq_number = data.get("qq", "")
            bind_time = data.get("bind_time", 0)
            
            # 只清理那些QQ为空且没有绑定时间的记录（这些是真正的无效数据）
            # 保留那些有绑定时间但QQ为空的记录（这些是管理员解绑的）
            if (not qq_number or not qq_number.strip()) and not bind_time:
                invalid_bindings.append(player_name)
                self.logger.warning(f"发现无效绑定：玩家 {player_name} 的QQ号为空且无绑定历史，将被清理")
        
        # 清理无效绑定
        for player_name in invalid_bindings:
            del self._binding_data[player_name]
            data_updated = True
        
        if invalid_bindings:
            self.logger.info(f"已清理 {len(invalid_bindings)} 个无效的QQ绑定")
        
        if data_updated:
            self._trigger_realtime_save("初始化数据结构更新")
            if invalid_bindings:
                self.logger.info("已更新绑定数据结构并清理无效绑定")
            else:
                self.logger.info("已更新绑定数据结构以支持在线时间统计")
        
        self.logger.info(f"{ColorFormat.AQUA}QQ绑定数据已加载，已绑定玩家: {len(self._binding_data)}{ColorFormat.RESET}")
    
    def _clear_plugin_attachments(self, player):
        """清理由此插件创建的权限附件（不影响其他插件的权限）"""
        try:
            # 清理之前存储的权限附件
            if hasattr(self, '_player_attachments') and player.name in self._player_attachments:
                try:
                    self._player_attachments[player.name].remove()
                    del self._player_attachments[player.name]
                    self.logger.debug(f"已清理玩家 {player.name} 的 qqsync 权限附件")
                except Exception as e:
                    self.logger.warning(f"清理存储的权限附件失败: {e}")
            
            # 只移除由此插件创建的权限附件，保留其他插件的权限
            attachments_to_remove = []
            for attachment_info in player.effective_permissions:
                if (hasattr(attachment_info, 'attachment') and 
                    hasattr(attachment_info.attachment, 'plugin') and 
                    attachment_info.attachment.plugin == self):
                    attachments_to_remove.append(attachment_info.attachment)
            
            for attachment in attachments_to_remove:
                try:
                    attachment.remove()
                    self.logger.debug(f"已移除玩家 {player.name} 的 qqsync 权限附件")
                except Exception as e:
                    self.logger.warning(f"移除权限附件失败: {e}")
                    
        except Exception as e:
            self.logger.warning(f"清理权限附件时出错: {e}")
    
    def set_player_visitor_permissions(self, player):
        """设置玩家为访客权限（仅限制危险操作，不影响其他插件）"""
        try:
            # 清理现有的此插件权限附件
            self._clear_plugin_attachments(player)
            
            # 创建访客权限附件 - 采用黑名单模式，只禁止特定危险操作
            visitor_attachment = player.add_attachment(self)
            
            # 确保可以使用绑定命令
            visitor_attachment.set_permission("qqsync.command.bindqq", True)
            
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
            if not hasattr(self, '_player_attachments'):
                self._player_attachments = {}
            self._player_attachments[player.name] = visitor_attachment
            
            # 重新计算权限
            player.recalculate_permissions()
            
            self.logger.info(f"已设置玩家 {player.name} 为访客权限（限制聊天、破坏性操作和攻击行为，保留其他插件权限）")
            return True
                
        except Exception as e:
            self.logger.error(f"设置访客权限失败: {e}")
            return False
    
    def restore_player_permissions(self, player):
        """恢复玩家的正常权限（仅移除访客限制，不影响其他权限）"""
        try:
            # 只清理由此插件创建的权限附件，保留其他插件的权限
            self._clear_plugin_attachments(player)
            
            # 不主动设置权限，让玩家使用服务器默认权限和其他插件权限
            # 这样可以避免与其他插件的权限管理发生冲突
            
            # 只确保我们的绑定命令权限存在（以防万一）
            player_attachment = player.add_attachment(self)
            player_attachment.set_permission("qqsync.command.bindqq", True)
            
            # 存储权限附件以便后续管理
            if not hasattr(self, '_player_attachments'):
                self._player_attachments = {}
            self._player_attachments[player.name] = player_attachment
            
            # 重新计算权限
            player.recalculate_permissions()
            
            self.logger.info(f"已为玩家 {player.name} 移除访客限制，恢复默认权限")
            return True
                
        except Exception as e:
            self.logger.error(f"恢复玩家权限失败: {e}")
            return False
    
    def check_and_apply_permissions(self, player):
        """检查并应用权限策略"""
        if not self.get_config("force_bind_qq", True):
            return  # 如果未启用强制绑定，不进行权限控制
        
        player_name = player.name
        visitor_reason = self.get_player_visitor_reason(player_name, player.xuid)
        
        if not visitor_reason:
            # 玩家有正常权限，移除访客限制
            self.restore_player_permissions(player)
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] 您已绑定QQ且在群内，拥有完整游戏权限{ColorFormat.RESET}")
        else:
            # 玩家应该是访客权限，设置相应限制
            self.set_player_visitor_permissions(player)
            
            if visitor_reason == "已被封禁":
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}[封禁] 您当前为访客权限（原因：已被封禁）{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}以下功能受限：{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法聊天{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法破坏/放置方块{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法与容器/机器交互{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法攻击生物或实体{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}其他插件功能不受影响{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}")
            elif visitor_reason == "未绑定QQ":
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}[警告] 您当前为访客权限（原因：未绑定QQ）{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}以下功能受限：{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法聊天{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法破坏/放置方块{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法与容器/机器交互{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法攻击生物或实体{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}其他插件功能不受影响{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}解决方案：使用命令 /bindqq 绑定QQ{ColorFormat.RESET}")
            elif visitor_reason == "已退出QQ群":
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}[警告] 您当前为访客权限（原因：已退出QQ群）{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}以下功能受限：{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法聊天{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法破坏/放置方块{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法与容器/机器交互{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法攻击生物或实体{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}其他插件功能不受影响{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}解决方案：重新加入QQ群后权限将自动恢复{ColorFormat.RESET}")
                
                # 显示QQ群信息
                target_group = self.get_config("target_group")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}目标QQ群：{target_group}{ColorFormat.RESET}")
    
    def cleanup_player_permissions(self, player_name):
        """清理离线玩家的权限附件"""
        try:
            if hasattr(self, '_player_attachments') and player_name in self._player_attachments:
                del self._player_attachments[player_name]
        except Exception as e:
            self.logger.warning(f"清理玩家 {player_name} 权限时出错: {e}")
    
    def _cleanup_player_caches(self, player_name):
        """清理离线玩家的相关缓存（修复异常断线后无法正常绑定的问题）"""
        try:
            cache_cleaned = []
            
            # 清理表单显示缓存
            if hasattr(self, '_form_display_cache') and player_name in self._form_display_cache:
                del self._form_display_cache[player_name]
                cache_cleaned.append("表单显示缓存")
            
            # 清理表单显示次数计数
            if hasattr(self, '_form_display_count') and player_name in self._form_display_count:
                del self._form_display_count[player_name]
                cache_cleaned.append("表单显示次数")
            
            # 清理待确认QQ信息
            if hasattr(self, '_pending_qq_confirmations') and player_name in self._pending_qq_confirmations:
                del self._pending_qq_confirmations[player_name]
                cache_cleaned.append("待确认QQ信息")
            
            # 清理并发绑定集合
            if hasattr(self, '_concurrent_bindings') and player_name in self._concurrent_bindings:
                self._concurrent_bindings.discard(player_name)
                cache_cleaned.append("并发绑定缓存")
            
            # 清理绑定队列
            if hasattr(self, '_binding_queue'):
                original_queue_length = len(self._binding_queue)
                self._binding_queue = [(p, q, t) for p, q, t in self._binding_queue if p != player_name]
                if len(self._binding_queue) < original_queue_length:
                    cache_cleaned.append("绑定队列")
            
            # 清理队列通知记录
            if hasattr(self, '_queue_notification_sent') and player_name in self._queue_notification_sent:
                self._queue_notification_sent.discard(player_name)
                cache_cleaned.append("队列通知缓存")
            
            # 清理待验证数据（如果玩家在验证过程中离线）
            global _pending_verifications
            if player_name in _pending_verifications:
                qq_number = _pending_verifications[player_name].get("qq")
                del _pending_verifications[player_name]
                
                # 同时清理对应的验证码
                global _verification_codes
                if qq_number and qq_number in _verification_codes:
                    del _verification_codes[qq_number]
                
                cache_cleaned.append("验证数据缓存")
            
            if cache_cleaned:
                self.logger.debug(f"已清理玩家 {player_name} 的缓存：{', '.join(cache_cleaned)}")
                
        except Exception as e:
            self.logger.warning(f"清理玩家 {player_name} 缓存时出错: {e}")
    
    def is_player_visitor(self, player_name: str, player_xuid: str = None) -> bool:
        """检查玩家是否为访客权限"""
        if not self.get_config("force_bind_qq", True):
            return False  # 如果未启用强制绑定，所有玩家都不是访客
        
        # 检查玩家是否被封禁
        if self.is_player_banned(player_name):
            return True  # 被封禁的玩家是访客
        
        # 检查玩家是否已绑定QQ（使用完整检查）
        if not self.is_player_bound(player_name, player_xuid):
            return True  # 未绑定QQ的玩家是访客
        
        # 检查已绑定的玩家是否退群
        player_qq = self.get_player_qq(player_name)
        if player_qq and hasattr(self, '_group_members') and self._group_members:
            # 如果玩家QQ不在群成员列表中，则视为退群
            if player_qq not in self._group_members:
                # 避免重复日志输出，只在第一次检测到时记录
                if not hasattr(self, '_logged_left_players'):
                    self._logged_left_players = set()
                
                if player_qq not in self._logged_left_players:
                    self.logger.info(f"玩家 {player_name} (QQ: {player_qq}) 已退群，设置为访客权限")
                    self._logged_left_players.add(player_qq)
                
                return True
        
        return False  # 已绑定且在群内的玩家不是访客
    
    def get_player_visitor_reason(self, player_name: str, player_xuid: str = None) -> str:
        """获取玩家被设为访客的原因"""
        if not self.get_config("force_bind_qq", True):
            return ""
        
        # 检查玩家是否被封禁
        if self.is_player_banned(player_name):
            return "已被封禁"
        
        if not self.is_player_bound(player_name, player_xuid):
            return "未绑定QQ"
        
        # 只有在启用强制绑定和退群检测时才检查群成员状态
        if (self.get_config("force_bind_qq", True) and 
            self.get_config("check_group_member", True)):
            player_qq = self.get_player_qq(player_name)
            if player_qq and hasattr(self, '_group_members') and self._group_members:
                if player_qq not in self._group_members:
                    return "已退出QQ群"
        
        return ""
    
    def update_group_members_cache(self):
        """更新群成员缓存"""
        # 只有在启用强制绑定和退群检测时才更新群成员缓存
        if not (self.get_config("force_bind_qq", True) and 
                self.get_config("check_group_member", True)):
            return
            
        if _current_ws:
            target_group = self.get_config("target_group")
            asyncio.run_coroutine_threadsafe(
                get_group_member_list(_current_ws, target_group),
                self._loop
            )
        else:
            self.logger.warning("WebSocket未连接，无法更新群成员缓存")
            # 如果WebSocket断开，暂时不进行退群检测
            # 避免因网络问题误判退群
    
    def check_qq_in_group_immediate(self, qq_number: str) -> bool:
        """立即检查QQ号是否在群内（通过API实时查询）"""
        # 只有在启用强制绑定和退群检测时才进行检查
        if not (self.get_config("force_bind_qq", True) and 
                self.get_config("check_group_member", True)):
            return True  # 如果未启用相关功能，默认允许
            
        if not _current_ws:
            self.logger.warning("WebSocket未连接，无法实时检查群成员")
            return True  # 连接断开时默认允许
        
        try:
            target_group = self.get_config("target_group")
            
            # 发送群成员信息查询请求
            payload = {
                "action": "get_group_member_info",
                "params": {
                    "group_id": target_group,
                    "user_id": int(qq_number),
                    "no_cache": True
                }
            }
            
            # 这里只发送请求，实际的响应处理在WebSocket消息处理中
            # 由于这是同步方法，我们依然使用缓存作为主要判断依据
            asyncio.run_coroutine_threadsafe(
                self._send_group_member_check(_current_ws, payload),
                self._loop
            )
            
            # 返回基于缓存的结果
            if hasattr(self, '_group_members') and self._group_members:
                return qq_number in self._group_members
            else:
                # 如果缓存为空，允许绑定但记录警告
                self.logger.warning(f"群成员缓存为空，无法验证QQ {qq_number} 是否在群内")
                return True
                
        except Exception as e:
            self.logger.error(f"检查QQ {qq_number} 是否在群内时出错: {e}")
            return True  # 出错时默认允许
    
    async def _send_group_member_check(self, ws, payload):
        """发送群成员检查请求"""
        try:
            await ws.send(json.dumps(payload))
        except Exception as e:
            self.logger.error(f"发送群成员检查请求失败: {e}")
    
    def on_command(self, sender, command, args):
        """处理插件命令"""
        if command.name == "bindqq":
            return self._handle_bindqq_command(sender, command, args)
        return False
    
    def _handle_bindqq_command(self, sender, command, args):
        """处理 /bindqq 命令"""
        try:
            # 检查发送者是否为玩家
            if not hasattr(sender, 'name') or not hasattr(sender, 'xuid'):
                sender.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}此命令只能由玩家使用！{ColorFormat.RESET}")
                return True
            
            player = sender
            player_name = player.name
            
            # 检查玩家是否已绑定QQ
            if self.is_player_bound(player_name, player.xuid):
                player_qq = self.get_player_qq(player_name)
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}您的QQ绑定状态：{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}已绑定QQ: {player_qq}{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如需重新绑定，请联系管理员{ColorFormat.RESET}")
            else:
                # 玩家未绑定，显示绑定表单
                if self.get_config("force_bind_qq", True):
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}您尚未绑定QQ，正在为您显示绑定表单...{ColorFormat.RESET}")
                    # 延迟显示表单，确保消息先发送
                    self.server.scheduler.run_task(
                        self,
                        lambda: self.show_qq_binding_form(player),
                        delay=5  # 0.25秒延迟
                    )
                else:
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}QQ绑定功能当前未启用{ColorFormat.RESET}")
            
            return True
        except Exception as e:
            self.logger.error(f"处理 /bindqq 命令失败: {e}")
            if hasattr(sender, 'send_message'):
                sender.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}命令执行出错，请重试！{ColorFormat.RESET}")
            return False
        
    def get_config(self, key: str, default=None):
        """获取配置项"""
        return self._config.get(key, default)
        
    def save_config(self):
        """保存配置到文件"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            self.logger.info("配置已保存")
        except Exception as e:
            self.logger.error(f"保存配置失败: {e}")
            
    def reload_config(self):
        """重新加载配置文件"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self._config = json.load(f)
            
            # 重新生成动态帮助信息
            self._config["help_msg"] = self._generate_help_message()
            
            reload_msg = f"{ColorFormat.GREEN}配置已重新加载{ColorFormat.RESET}"
            self.logger.info(reload_msg)
            return True
        except Exception as e:
            self.logger.error(f"重新加载配置失败: {e}")
            return False
    
    def _trigger_realtime_save(self, reason: str = "数据变更"):
        """触发实时保存（简单直接保存）"""
        if not self._auto_save_enabled:
            return
        
        self.save_binding_data()
        # 记录详细保存日志（简化版本，避免API兼容性问题）
        self.logger.debug(f"数据保存: {reason}")
    
    def save_binding_data(self):
        """保存QQ绑定数据到文件（简单同步保存）"""
        try:
            # 创建临时文件，避免写入过程中的数据损坏
            temp_file = self.binding_file.with_suffix('.tmp')
            
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self._binding_data, f, indent=2, ensure_ascii=False)
            
            # 原子性替换文件
            temp_file.replace(self.binding_file)
            
        except Exception as e:
            self.logger.error(f"保存QQ绑定数据失败: {e}")
            # 如果临时文件存在，清理它
            temp_file = self.binding_file.with_suffix('.tmp')
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except:
                    pass
    
    def is_player_bound(self, player_name: str, player_xuid: str = None) -> bool:
        """检查玩家是否已绑定QQ（完整检查，包括XUID验证）- 从内存读取"""
        # 从内存读取绑定数据
        binding_data = self._binding_data
        
        # 如果提供了XUID，优先通过XUID查找玩家数据
        if player_xuid:
            player_data = self._get_player_by_xuid_from_data(player_xuid, binding_data)
            if player_data:
                # 通过XUID找到了玩家数据，检查QQ字段
                qq_number = player_data.get("qq", "")
                return bool(qq_number and qq_number.strip())
            else:
                # 通过XUID没有找到玩家数据，继续用玩家名查找（向后兼容）
                if player_name in binding_data:
                    qq_number = binding_data[player_name].get("qq", "")
                    return bool(qq_number and qq_number.strip())
                return False
        
        # 仅基于玩家名的检查（向后兼容）
        if player_name not in binding_data:
            return False
        
        # 检查QQ号是否有效（不为空）
        qq_number = binding_data[player_name].get("qq", "")
        return bool(qq_number and qq_number.strip())
    
    def _get_player_by_xuid_from_data(self, xuid: str, binding_data: dict) -> dict:
        """从指定数据中根据XUID获取玩家绑定信息"""
        for name, data in binding_data.items():
            if data.get("xuid") == xuid:
                return data
        return {}
    
    def is_player_bound_by_xuid(self, player_xuid: str) -> bool:
        """基于XUID检查玩家是否已绑定QQ - 从内存读取"""
        # 从内存读取绑定数据
        binding_data = self._binding_data
        player_data = self._get_player_by_xuid_from_data(player_xuid, binding_data)
        if not player_data:
            return False
        
        # 检查QQ号是否有效（不为空）
        qq_number = player_data.get("qq", "")
        return bool(qq_number and qq_number.strip())
    
    def get_complete_player_binding_status(self, player_name: str, player_xuid: str) -> dict:
        """获取玩家完整的绑定状态信息 - 从内存读取"""
        # 从内存读取绑定数据
        binding_data = self._binding_data
        
        result = {
            "is_bound": False,
            "qq_number": "",
            "binding_source": "",  # "name" 或 "xuid" 或 "both" 或 "none"
            "data_consistent": True,  # 数据是否一致
            "issues": []  # 发现的问题列表
        }
        
        # 检查基于玩家名的绑定
        name_bound = False
        name_qq = ""
        if player_name in binding_data:
            name_qq = binding_data[player_name].get("qq", "")
            name_bound = bool(name_qq and name_qq.strip())
        
        # 检查基于XUID的绑定
        xuid_data = self._get_player_by_xuid_from_data(player_xuid, binding_data)
        xuid_bound = False
        xuid_qq = ""
        if xuid_data:
            xuid_qq = xuid_data.get("qq", "")
            xuid_bound = bool(xuid_qq and xuid_qq.strip())
        
        # 分析绑定状态
        if name_bound and xuid_bound:
            if name_qq == xuid_qq:
                result["is_bound"] = True
                result["qq_number"] = name_qq
                result["binding_source"] = "both"
            else:
                result["is_bound"] = False
                result["data_consistent"] = False
                result["issues"].append(f"QQ号不一致: 玩家名对应{name_qq}, XUID对应{xuid_qq}")
        elif name_bound and not xuid_bound:
            result["is_bound"] = name_bound
            result["qq_number"] = name_qq
            result["binding_source"] = "name"
            result["issues"].append("仅玩家名有绑定记录，XUID无对应数据")
        elif not name_bound and xuid_bound:
            result["is_bound"] = xuid_bound
            result["qq_number"] = xuid_qq
            result["binding_source"] = "xuid"
            result["issues"].append("仅XUID有绑定记录，当前玩家名无对应数据")
        else:
            result["is_bound"] = False
            result["binding_source"] = "none"
        
        return result
    
    def get_player_qq(self, player_name: str) -> str:
        """获取玩家绑定的QQ号 - 从内存读取"""
        # 从内存读取绑定数据
        binding_data = self._binding_data
        return binding_data.get(player_name, {}).get("qq", "")
    
    def get_qq_player(self, qq_number: str) -> str:
        """根据QQ号获取绑定的玩家名 - 从内存读取"""
        # 从内存读取绑定数据
        binding_data = self._binding_data
        
        for name, data in binding_data.items():
            # 只检查当前有效的QQ绑定
            current_qq = data.get("qq", "")
            if current_qq and current_qq.strip() == qq_number:
                return name
        
        return ""
    
    def get_qq_player_history(self, qq_number: str) -> str:
        """根据QQ号获取历史绑定的玩家名（包括已解绑的）- 从内存读取"""
        # 从内存读取绑定数据
        binding_data = self._binding_data
        
        for name, data in binding_data.items():
            # 首先检查当前绑定的QQ号
            if data.get("qq") == qq_number:
                return name
            # 检查原QQ号（用于被解绑或封禁的玩家历史查询）
            if data.get("original_qq") == qq_number:
                return name
        return ""
    
    def get_player_by_xuid(self, xuid: str) -> dict:
        """根据XUID获取玩家绑定信息 - 从内存读取"""
        # 从内存读取绑定数据
        binding_data = self._binding_data
        return self._get_player_by_xuid_from_data(xuid, binding_data)
    
    def update_player_name(self, old_name: str, new_name: str, xuid: str):
        """更新玩家名称（处理改名情况）"""
        if old_name in self._binding_data:
            # 保存原有数据
            player_data = self._binding_data[old_name].copy()
            # 更新名称
            player_data["name"] = new_name
            player_data["last_name_update"] = int(time.time())
            
            # 删除旧记录，添加新记录
            del self._binding_data[old_name]
            self._binding_data[new_name] = player_data
            
            self._trigger_realtime_save(f"玩家改名: {old_name} → {new_name}")
            self.logger.info(f"玩家改名: {old_name} → {new_name} (XUID: {xuid})")
            
            # 更新QQ群昵称（仅在启用QQ绑定且同步群昵称时）
            if (_current_ws and 
                self.get_config("force_bind_qq", True) and 
                self.get_config("sync_group_card", True)):
                target_group = self.get_config("target_group")
                qq_number = player_data.get("qq")
                if qq_number:
                    asyncio.run_coroutine_threadsafe(
                        set_group_card(_current_ws, group_id=target_group, user_id=int(qq_number), card=new_name),
                        self._loop
                    )
            
            return True
        return False
    
    def unbind_player_qq(self, player_name: str, admin_name: str = "system") -> bool:
        """解绑玩家QQ（保留游戏数据）"""
        if player_name not in self._binding_data:
            return False
        
        player_data = self._binding_data[player_name]
        original_qq = player_data.get("qq", "")
        
        if not original_qq or not original_qq.strip():
            return False  # 玩家本来就没有绑定QQ
        
        # 保留所有游戏数据，只清空QQ相关信息
        player_data["qq"] = ""  # 清空QQ号
        player_data["unbind_time"] = int(time.time())  # 记录解绑时间
        player_data["unbind_by"] = admin_name  # 记录解绑操作者
        player_data["original_qq"] = original_qq  # 保留原QQ号（用于历史记录）
        
        self._trigger_realtime_save(f"解绑QQ: {player_name} (原QQ: {original_qq})")
        self.logger.info(f"玩家 {player_name} 的QQ绑定已被 {admin_name} 解除 (原QQ: {original_qq})，游戏数据已保留")
        return True
    
    def bind_player_qq(self, player_name: str, player_xuid: str, qq_number: str):
        """绑定玩家QQ（简化版本）"""
        # 验证QQ号不为空且为数字
        if not qq_number or not qq_number.strip():
            self.logger.error(f"尝试绑定空QQ号给玩家 {player_name}，操作被拒绝")
            return False
        
        # 验证QQ号格式（5-11位数字）
        qq_clean = qq_number.strip()
        if not qq_clean.isdigit() or len(qq_clean) < 5 or len(qq_clean) > 11:
            self.logger.error(f"尝试绑定无效QQ号 {qq_clean} 给玩家 {player_name}，QQ号必须是5-11位数字")
            return False
        
        # 验证玩家名不为空
        if not player_name or not player_name.strip():
            self.logger.error(f"尝试绑定QQ {qq_clean} 给空玩家名，操作被拒绝")
            return False
        
        # 检查是否已有该玩家的数据（重新绑定的情况）
        if player_name in self._binding_data:
            # 保留现有的游戏数据，更新绑定信息
            player_data = self._binding_data[player_name]
            old_qq = player_data.get("qq", "")
            
            # 更新绑定信息
            player_data["qq"] = qq_clean
            player_data["xuid"] = player_xuid
            
            if old_qq:
                # 重新绑定
                player_data["rebind_time"] = int(time.time())
                player_data["previous_qq"] = old_qq
                self.logger.info(f"玩家 {player_name} 重新绑定QQ: {old_qq} → {qq_clean}")
            else:
                # 首次绑定或解绑后重新绑定
                if "unbind_time" in player_data:
                    player_data["rebind_time"] = int(time.time())
                    self.logger.info(f"玩家 {player_name} 解绑后重新绑定QQ: {qq_clean}")
                else:
                    player_data["bind_time"] = int(time.time())
                    self.logger.info(f"玩家 {player_name} 首次绑定QQ: {qq_clean}")
        else:
            # 全新的玩家数据
            self._binding_data[player_name] = {
                "name": player_name,
                "xuid": player_xuid,
                "qq": qq_clean,
                "bind_time": int(time.time()),
                "total_playtime": 0,  # 总在线时间（秒）
                "last_join_time": None,  # 最后加入时间
                "last_quit_time": None,  # 最后离开时间
                "session_count": 0  # 游戏会话次数
            }
            self.logger.info(f"玩家 {player_name} 已绑定QQ: {qq_clean}")
        
        self._trigger_realtime_save(f"绑定QQ: {player_name} → {qq_clean}")
        
        return True
    
    def update_player_join(self, player_name: str, player_xuid: str = None):
        """更新玩家加入时间（仅限已绑定QQ的玩家）"""
        # 只有已绑定QQ的玩家才统计数据
        if player_name not in self._binding_data:
            return  # 未绑定QQ的玩家不创建统计记录
        
        # 检查玩家是否已绑定QQ
        if not self._binding_data[player_name].get("qq", "").strip():
            return  # 未绑定QQ的玩家不更新统计
        
        current_time = int(time.time())
        self._binding_data[player_name]["last_join_time"] = current_time
        self._binding_data[player_name]["session_count"] = self._binding_data[player_name].get("session_count", 0) + 1
        
        # 更新XUID（如果提供了新的XUID）
        if player_xuid and not self._binding_data[player_name].get("xuid"):
            self._binding_data[player_name]["xuid"] = player_xuid
        
        self._trigger_realtime_save(f"玩家加入: {player_name}")
    
    def update_player_quit(self, player_name: str):
        """更新玩家离开时间和总在线时间（仅限已绑定QQ的玩家）"""
        # 只有已绑定QQ的玩家才统计数据
        if player_name not in self._binding_data:
            return  # 未绑定QQ的玩家不创建统计记录
        
        # 检查玩家是否已绑定QQ
        if not self._binding_data[player_name].get("qq", "").strip():
            return  # 未绑定QQ的玩家不更新统计
        
        current_time = int(time.time())
        last_join = self._binding_data[player_name].get("last_join_time")
        
        if last_join:
            # 计算本次会话时间
            session_time = current_time - last_join
            if session_time > 0:  # 确保时间有效
                self._binding_data[player_name]["total_playtime"] = self._binding_data[player_name].get("total_playtime", 0) + session_time
        
        self._binding_data[player_name]["last_quit_time"] = current_time
        self._trigger_realtime_save(f"玩家离开: {player_name}")
    
    def get_player_binding_history(self, player_name: str) -> dict:
        """获取玩家绑定历史信息 - 从内存读取"""
        # 从内存读取绑定数据
        binding_data = self._binding_data
        if player_name not in binding_data:
            return {}
        
        data = binding_data[player_name]
        
        history = {
            "current_qq": data.get("qq", ""),
            "is_bound": bool(data.get("qq", "").strip()),
            "bind_time": data.get("bind_time"),
            "unbind_time": data.get("unbind_time"),
            "rebind_time": data.get("rebind_time"),
            "unbind_by": data.get("unbind_by"),
            "original_qq": data.get("original_qq"),
            "previous_qq": data.get("previous_qq"),
            "total_playtime": data.get("total_playtime", 0),
            "session_count": data.get("session_count", 0),
        }
        
        # 计算绑定状态
        if history["is_bound"]:
            if history["rebind_time"]:
                history["status"] = "重新绑定"
            else:
                history["status"] = "已绑定"
        else:
            if history["unbind_time"]:
                history["status"] = "已解绑"
            else:
                history["status"] = "从未绑定"
        
        return history

    def get_player_playtime_info(self, player_name: str) -> dict:
        """获取玩家在线时间信息（仅限已绑定QQ的玩家）- 从内存读取"""
        # 从内存读取绑定数据
        binding_data = self._binding_data
        if player_name not in binding_data:
            return {}
        
        data = binding_data[player_name]
        
        # 检查玩家是否已绑定QQ
        if not data.get("qq", "").strip():
            return {}  # 未绑定QQ的玩家不返回统计信息
        
        current_time = int(time.time())
        
        # 计算总在线时间
        total_playtime = data.get("total_playtime", 0)
        last_join = data.get("last_join_time")
        
        # 如果玩家当前在线，加上当前会话时间
        is_online = False
        current_session_time = 0
        if last_join:
            # 检查玩家是否在线
            for player in self.server.online_players:
                if player.name == player_name:
                    is_online = True
                    current_session_time = current_time - last_join
                    break
        
        if is_online:
            total_with_current = total_playtime + current_session_time
        else:
            total_with_current = total_playtime
        
        return {
            "total_playtime": total_with_current,
            "session_count": data.get("session_count", 0),
            "last_join_time": last_join,
            "last_quit_time": data.get("last_quit_time"),
            "is_online": is_online,
            "current_session_time": current_session_time if is_online else 0,
            "bind_time": data.get("bind_time")
        }
    
    def is_player_banned(self, player_name: str) -> bool:
        """检查玩家是否被封禁 - 从内存读取"""
        # 如果未启用强制绑定QQ，封禁功能也被禁用
        if not self.get_config("force_bind_qq", True):
            return False
        
        # 从内存读取绑定数据
        binding_data = self._binding_data
        if player_name not in binding_data:
            return False
        return binding_data[player_name].get("is_banned", False)
    
    def ban_player(self, player_name: str, admin_name: str = "system", reason: str = "") -> bool:
        """封禁玩家，禁止QQ绑定"""
        # 如果未启用强制绑定QQ，封禁功能被禁用
        if not self.get_config("force_bind_qq", True):
            self.logger.warning(f"封禁功能已禁用：强制QQ绑定功能未启用，无法封禁玩家 {player_name}")
            return False
            
        # 确保玩家数据存在
        if player_name not in self._binding_data:
            # 创建新的玩家数据
            self._binding_data[player_name] = {
                "name": player_name,
                "xuid": "",  # 将在玩家加入时更新
                "qq": "",
                "total_playtime": 0,
                "last_join_time": None,
                "last_quit_time": None,
                "session_count": 0
            }
        
        # 设置封禁状态
        player_data = self._binding_data[player_name]
        player_data["is_banned"] = True
        player_data["ban_time"] = int(time.time())
        player_data["ban_by"] = admin_name
        player_data["ban_reason"] = reason or "管理员封禁"
        
        # 如果玩家已绑定QQ，解除绑定
        if player_data.get("qq"):
            original_qq = player_data["qq"]
            player_data["qq"] = ""
            player_data["unbind_time"] = int(time.time())
            player_data["unbind_by"] = admin_name
            player_data["unbind_reason"] = "封禁时自动解绑"
            player_data["original_qq"] = original_qq
            self.logger.info(f"玩家 {player_name} 被封禁时自动解除QQ绑定 (原QQ: {original_qq})")
        
        self._trigger_realtime_save(f"封禁玩家: {player_name} (原因: {reason or '管理员封禁'})")
        self.logger.info(f"玩家 {player_name} 已被 {admin_name} 封禁，原因：{reason or '管理员封禁'}")
        return True
    
    def unban_player(self, player_name: str, admin_name: str = "system") -> bool:
        """解封玩家"""
        # 如果未启用强制绑定QQ，封禁功能被禁用
        if not self.get_config("force_bind_qq", True):
            self.logger.warning(f"解封功能已禁用：强制QQ绑定功能未启用，无法解封玩家 {player_name}")
            return False
            
        if player_name not in self._binding_data:
            return False
        
        player_data = self._binding_data[player_name]
        if not player_data.get("is_banned", False):
            return False  # 玩家本来就没有被封禁
        
        # 解除封禁
        player_data["is_banned"] = False
        player_data["unban_time"] = int(time.time())
        player_data["unban_by"] = admin_name
        
        self._trigger_realtime_save(f"解封玩家: {player_name}")
        self.logger.info(f"玩家 {player_name} 已被 {admin_name} 解封")
        return True
    
    def get_banned_players(self) -> list:
        """获取所有被封禁的玩家列表 - 从内存读取"""
        # 如果未启用强制绑定QQ，返回空列表
        if not self.get_config("force_bind_qq", True):
            return []
        
        binding_data = self._binding_data
        banned_players = [
            {
                "name": player_name,
                "ban_time": data.get("ban_time"),
                "ban_by": data.get("ban_by", "unknown"),
                "ban_reason": data.get("ban_reason", "无原因")
            }
            for player_name, data in binding_data.items()
            if data.get("is_banned", False)
        ]
        return banned_players
    
    def _send_ban_notification(self, player, ban_reason: str, ban_by: str, ban_time: int):
        """向被封禁的玩家发送封禁通知"""
        try:
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
    
    def show_qq_binding_form(self, player):
        """显示QQ绑定表单"""
        try:
            # 多玩家优化：检查是否可以显示绑定表单（每个玩家最多3次机会）
            if not self._can_show_form(player.name):
                # 检查是否已达到最大显示次数
                display_count = self._form_display_count.get(player.name, 0)
                if display_count >= 3:
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}绑定表单显示次数已达上限(3次)，请使用命令 /bindqq 进行绑定{ColorFormat.RESET}")
                else:
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}绑定表单显示过于频繁，请稍后再试{ColorFormat.RESET}")
                return
            
            # 根据是否启用强制绑定显示不同的提示信息
            if self.get_config("force_bind_qq", True):
                form_labels = [
                    Label("欢迎来到服务器！"),
                    Label("为了更好的游戏体验，请绑定您的QQ号。"),
                    Label("绑定后可以与QQ群聊天互通。")
                ]
            else:
                form_labels = [
                    Label("QQ群服互通 - 可选绑定"),
                    Label("绑定QQ号后可享受群服互通功能。"),
                    Label("您也可以选择不绑定，不影响正常游戏。")
                ]
            
            form = ModalForm(
                title="QQsync群服互通 - 身份验证",
                controls=form_labels + [
                    TextInput(
                        label="请输入您的QQ号",
                        placeholder="例如: 2899659758",
                        default_value=""
                    )
                ],
                submit_button="下一步"
            )
            
            form.on_submit = lambda player, form_data: self._handle_qq_form_submit(player, form_data)
            close_message = f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}您可以稍后通过命令 /bindqq 进行QQ绑定{ColorFormat.RESET}"
            if not self.get_config("force_bind_qq", True):
                close_message = f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}QQ绑定已取消，您可以正常游戏{ColorFormat.RESET}"
            form.on_close = lambda player: player.send_message(close_message)
            
            player.send_form(form)
            
        except Exception as e:
            self.logger.error(f"显示QQ绑定表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}绑定表单加载失败，请使用命令 /bindqq{ColorFormat.RESET}")
    
    def _handle_qq_form_submit(self, player, form_data):
        """处理QQ绑定表单提交"""
        global _pending_verifications, _verification_codes
        
        try:
            try:
                # 尝试解析JSON格式
                data_list = json.loads(form_data)
                qq_input = data_list[3] if len(data_list) > 3 else ""
            except (json.JSONDecodeError, IndexError):
                # 如果不是JSON，按逗号分割或其他方式解析
                data_parts = form_data.split(',') if form_data else []
                qq_input = data_parts[3].strip() if len(data_parts) > 3 else ""
            
            if not qq_input or not qq_input.isdigit():
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}请输入有效的QQ号（5-11位数字）！{ColorFormat.RESET}")
                # 只有在启用强制绑定时才重新显示表单
                if self.get_config("force_bind_qq", True):
                    self.server.scheduler.run_task(
                        self,
                        lambda: self.show_qq_binding_form(player),
                        delay=20
                    )
                return
            
            # 验证QQ号长度
            if len(qq_input) < 5 or len(qq_input) > 11:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}QQ号长度无效！请输入5-11位数字{ColorFormat.RESET}")
                # 只有在启用强制绑定时才重新显示表单
                if self.get_config("force_bind_qq", True):
                    self.server.scheduler.run_task(
                        self,
                        lambda: self.show_qq_binding_form(player),
                        delay=20
                    )
                return
            
            # 检查玩家是否被封禁
            if self.is_player_banned(player.name):
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}[拒绝] 您已被封禁，无法绑定QQ！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}")
                return
            
            # 检查QQ号是否已被其他玩家绑定
            existing_player = self.get_qq_player(qq_input)
            if existing_player:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}该QQ号已被玩家 {existing_player} 绑定！{ColorFormat.RESET}")
                return
            
            # 检查QQ号是否在群内（仅在启用强制绑定和退群检测时）
            if (self.get_config("force_bind_qq", True) and 
                self.get_config("check_group_member", True)):
                if hasattr(self, '_group_members') and self._group_members:
                    if qq_input not in self._group_members:
                        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}该QQ号不在目标群内，无法绑定！{ColorFormat.RESET}")
                        target_group = self.get_config("target_group")
                        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}请先加入QQ群：{target_group}{ColorFormat.RESET}")
                        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}加入群后请等待几分钟，让系统更新群成员列表{ColorFormat.RESET}")
                        return
                else:
                    # 如果群成员缓存为空，警告用户但允许继续（可能是首次启动）
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}[警告] 无法验证QQ是否在群内（群成员列表未加载）{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请确保您已加入目标QQ群，否则绑定后可能被限制权限{ColorFormat.RESET}")
                    target_group = self.get_config("target_group")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}目标QQ群：{target_group}{ColorFormat.RESET}")
            
            # 检查是否可以发送验证码
            can_send, error_msg = self._can_send_verification(qq_input, player.name)
            if not can_send:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}[限制] {error_msg}{ColorFormat.RESET}")
                return
            
            # 先获取QQ昵称，然后显示确认表单
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}正在获取QQ昵称信息...{ColorFormat.RESET}")
            
            # 临时存储待确认的QQ信息（简化版本）
            self._pending_qq_confirmations[player.name] = {
                "qq": qq_input,
                "nickname": "获取中...",
                "timestamp": time.time()
            }
            
            # 异步获取QQ昵称
            if _current_ws:
                asyncio.run_coroutine_threadsafe(
                    self._get_qq_nickname_and_confirm(_current_ws, player, qq_input),
                    self._loop
                )
            else:
                # 如果WebSocket未连接，直接显示确认表单（无昵称）
                self._show_qq_confirmation_form(player, qq_input, "未知昵称")
            
        except Exception as e:
            self.logger.error(f"处理QQ绑定表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}绑定过程出错，请重试！{ColorFormat.RESET}")
    
    async def _get_qq_nickname_and_confirm(self, ws, player, qq_number):
        """异步获取QQ昵称并显示确认表单"""
        try:
            # 发送获取用户信息请求
            payload = {
                "action": "get_stranger_info",
                "params": {
                    "user_id": int(qq_number),
                    "no_cache": True
                }
            }
            
            # 尝试获取QQ昵称，失败则使用默认昵称
            nickname = "未知昵称"
            try:
                await ws.send(json.dumps(payload))
                # 等待短时间让响应到达
                await asyncio.sleep(1)
                # 检查是否通过WebSocket响应更新了昵称
                if player.name in self._pending_qq_confirmations:
                    nickname = self._pending_qq_confirmations[player.name].get("nickname", "未知昵称")
            except Exception as e:
                self.logger.warning(f"获取QQ昵称失败: {e}")
            
            # 在主线程中显示确认表单
            def show_form():
                try:
                    self._show_qq_confirmation_form(player, qq_number, nickname)
                except Exception as e:
                    self.logger.error(f"显示QQ确认表单失败: {e}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}显示确认表单失败，请重试！{ColorFormat.RESET}")
            
            # 使用调度器在主线程执行
            self.server.scheduler.run_task(self, show_form, delay=1)
            
        except Exception as e:
            self.logger.error(f"获取QQ昵称过程出错: {e}")
            # 显示默认确认表单
            def show_form():
                self._show_qq_confirmation_form(player, qq_number, "未知昵称")
            self.server.scheduler.run_task(self, show_form, delay=1)
    
    def _show_qq_confirmation_form(self, player, qq_number, nickname):
        """显示QQ信息确认表单"""
        try:
            form = ModalForm(
                title="确认QQ账号信息",
                controls=[
                    Label("请确认以下QQ账号信息是否正确："),
                    Label(""),
                    Label(f"QQ号: {qq_number}"),
                    Label(f"昵称: {nickname}"),
                    Label(""),
                    Label("如果信息正确，点击'确认'继续绑定；"),
                    Label("如果信息错误，点击'取消'重新输入。")
                ],
                submit_button="确认绑定",
                cancel_button="取消"
            )
            
            # 简化处理：直接传递QQ号和昵称
            form.on_submit = lambda player, form_data: self._handle_qq_confirmation(player, True, qq_number, nickname)
            form.on_close = lambda player: self._handle_qq_confirmation(player, False, qq_number, nickname)
            
            player.send_form(form)
            
        except Exception as e:
            self.logger.error(f"显示QQ确认表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}确认表单加载失败，请重试！{ColorFormat.RESET}")
    
    def _handle_qq_confirmation(self, player, confirmed, qq_number=None, nickname=None):
        """处理QQ信息确认结果"""
        global _pending_verifications, _verification_codes
        
        try:
            # 清理待确认信息
            if player.name in self._pending_qq_confirmations:
                # 如果没有传递qq_number，从存储中获取
                if not qq_number:
                    qq_info = self._pending_qq_confirmations[player.name]
                    qq_number = qq_info["qq"]
                    nickname = qq_info.get("nickname", "未知昵称")
                del self._pending_qq_confirmations[player.name]
            
            if not confirmed:
                # 用户取消了绑定
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}QQ绑定已取消{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您可以使用命令 /bindqq 重新开始绑定{ColorFormat.RESET}")
                return
            
            if not qq_number:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}确认信息已过期，请重新开始绑定{ColorFormat.RESET}")
                return
            
            # 用户确认了QQ信息，开始验证码流程
            # 注册验证码发送尝试
            self._register_verification_attempt(qq_number, player.name)
            
            # 生成验证码
            verification_code = str(random.randint(100000, 999999))
            
            # 存储待验证信息（不需要存储昵称）
            _pending_verifications[player.name] = {
                "qq": qq_number,
                "code": verification_code,
                "timestamp": time.time()
            }
            
            _verification_codes[qq_number] = {
                "code": verification_code,
                "timestamp": time.time(),
                "player_name": player.name
            }
            
            # 发送验证码到QQ
            if _current_ws:
                try:
                    # 计算当前系统负载信息
                    current_concurrent = len(self._concurrent_bindings)
                    queue_length = len(self._binding_queue)
                    
                    # 发送验证码消息（包含昵称用于确认）
                    verification_text = f"🎮 QQsync-群服互通 绑定验证\n\n玩家名: {player.name}\n验证码: {verification_code}\n\n✅ 请在游戏中输入此验证码完成绑定\n📱 或QQ群输入: /verify {verification_code}\n⏰ 验证码5分钟内有效\n\n📊 系统状态: {current_concurrent}/{self._max_concurrent_bindings} 并发 | {queue_length} 排队"
                    
                    asyncio.run_coroutine_threadsafe(
                        send_private_msg(_current_ws, user_id=int(qq_number), text=verification_text),
                        self._loop
                    )
                    
                    # 通知玩家验证码已发送
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}验证码已发送到QQ {qq_number} ({nickname})！{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请检查QQ私聊消息{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}当前绑定队列：{len(self._concurrent_bindings)}/{self._max_concurrent_bindings}{ColorFormat.RESET}")
                    
                    # 延迟显示验证码输入表单
                    self.server.scheduler.run_task(
                        self,
                        lambda: self.show_verification_form(player),
                        delay=20  # 1秒延迟
                    )
                    
                except Exception as e:
                    self.logger.error(f"发送验证码失败: {e}")
                    self._unregister_verification_attempt(qq_number, player.name, False)
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}发送验证码失败，请稍后重试！{ColorFormat.RESET}")
                    # 清理验证信息
                    if player.name in _pending_verifications:
                        del _pending_verifications[player.name]
                    if qq_number in _verification_codes:
                        del _verification_codes[qq_number]
                    return
            else:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}服务器未连接到QQ，无法发送验证码！{ColorFormat.RESET}")
                self._unregister_verification_attempt(qq_number, player.name, False)
                return
            
        except Exception as e:
            self.logger.error(f"处理QQ确认失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}确认过程出错，请重试！{ColorFormat.RESET}")
    
    def show_verification_form(self, player):
        """显示验证码输入表单"""
        try:
            form = ModalForm(
                title="QQ验证码确认",
                controls=[
                    Label("验证码已发送到您的QQ！"),
                    Label("请检查QQ私聊消息并输入收到的验证码。"),
                    Label("验证码5分钟内有效。"),
                    TextInput(
                        label="验证码",
                        placeholder="请输入6位验证码",
                        default_value=""
                    )
                ],
                submit_button="确认绑定"
            )
            
            form.on_submit = lambda player, form_data: self._handle_verification_submit(player, form_data)
            form.on_close = lambda player: self._handle_verification_close(player)
            
            player.send_form(form)
            
        except Exception as e:
            self.logger.error(f"显示验证码表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证表单加载失败！{ColorFormat.RESET}")
    
    def _handle_verification_close(self, player):
        """处理验证表单关闭"""
        global _pending_verifications, _verification_codes
        
        # 清理待验证数据
        if player.name in _pending_verifications:
            qq_number = _pending_verifications[player.name]["qq"]
            del _pending_verifications[player.name]
            if qq_number in _verification_codes:
                del _verification_codes[qq_number]
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}QQ绑定已取消{ColorFormat.RESET}")
    
    def _handle_verification_submit(self, player, form_data):
        """处理验证码提交"""
        global _pending_verifications, _verification_codes
        
        try:
            # 首先检查验证表单状态
            can_verify, error_msg = self._can_show_verification_form(player.name)
            if not can_verify:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}{error_msg}{ColorFormat.RESET}")
                if "过期" in error_msg:
                    self._cleanup_expired_verification(player.name)
                return
            
            # 解析form_data获取验证码
            try:
                # 尝试解析JSON格式
                data_list = json.loads(form_data)
                verification_input = data_list[3] if len(data_list) > 3 else ""
            except (json.JSONDecodeError, IndexError):
                # 如果不是JSON，按逗号分割或其他方式解析
                data_parts = form_data.split(',') if form_data else []
                verification_input = data_parts[3].strip() if len(data_parts) > 3 else ""
            
            # 验证码格式检查
            if not verification_input or not verification_input.isdigit() or len(verification_input) != 6:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}请输入有效的6位数字验证码！{ColorFormat.RESET}")
                # 重新显示验证表单
                self.server.scheduler.run_task(
                    self,
                    lambda: self.show_verification_form(player),
                    delay=10
                )
                return
            
            # 获取待验证信息
            pending_info = _pending_verifications[player.name]
            
            # 验证验证码
            if verification_input == pending_info["code"]:
                # 绑定成功
                self.bind_player_qq(player.name, player.xuid, pending_info["qq"])
                
                # 清理验证数据
                del _pending_verifications[player.name]
                if pending_info["qq"] in _verification_codes:
                    del _verification_codes[pending_info["qq"]]
                
                # 发送成功消息
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] QQ绑定成功！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您的QQ {pending_info['qq']} 已与游戏账号绑定{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] 已解除访客限制，现在可以：{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}• 自由聊天{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}• 破坏/放置方块{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}• 与所有方块交互{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}• 攻击生物和实体{ColorFormat.RESET}")
                
                # 恢复玩家权限（从访客权限升级为正常权限）
                if self.get_config("force_bind_qq", True):
                    # 使用异步任务避免阻塞
                    self.server.scheduler.run_task(
                        self,
                        lambda: self.restore_player_permissions(player),
                        delay=2  # 短暂延迟确保绑定数据操作完成
                    )
                
                # 异步设置QQ群昵称为玩家名（不阻塞主流程）
                if (_current_ws and 
                    self.get_config("force_bind_qq", True) and 
                    self.get_config("sync_group_card", True)):
                    target_group = self.get_config("target_group")
                    asyncio.run_coroutine_threadsafe(
                        set_group_card(_current_ws, group_id=target_group, user_id=int(pending_info["qq"]), card=player.name),
                        self._loop
                    )
                
                # 异步通知QQ群（不阻塞主流程）
                if _current_ws:
                    asyncio.run_coroutine_threadsafe(
                        send_group_msg(_current_ws, group_id=self.get_config("target_group"), text=f"🎉 玩家 {player.name} 已完成QQ绑定！"),
                        self._loop
                    )
            else:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证码错误，请重试！{ColorFormat.RESET}")
                # 重新显示验证表单
                self.server.scheduler.run_task(
                    self,
                    lambda: self.show_verification_form(player),
                    delay=10
                )
            
        except Exception as e:
            self.logger.error(f"处理验证码提交失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证过程出错，请重试！{ColorFormat.RESET}")
    
    def cleanup_expired_verifications(self):
        """清理过期的验证码"""
        global _pending_verifications, _verification_codes
        
        current_time = time.time()
        expired_players = []
        expired_qq = []
        
        # 清理过期的待验证信息
        for player_name, info in _pending_verifications.items():
            if current_time - info["timestamp"] > 300:  # 5分钟过期
                expired_players.append(player_name)
        
        for player_name in expired_players:
            del _pending_verifications[player_name]
        
        # 清理过期的验证码
        for qq_number, info in _verification_codes.items():
            if current_time - info["timestamp"] > 300:  # 5分钟过期
                expired_qq.append(qq_number)
        
        for qq_number in expired_qq:
            del _verification_codes[qq_number]
        
        if expired_players or expired_qq:
            self.logger.info(f"清理过期验证码: {len(expired_players)} 个待验证玩家, {len(expired_qq)} 个验证码")
        
        # 清理过期的多玩家处理缓存
        self._cleanup_expired_caches()
    
    def _cleanup_expired_caches(self):
        """清理过期的多玩家处理缓存"""
        current_time = time.time()
        
        # 清理验证码发送队列中的过期记录（1分钟过期）
        expired_queue = [qq for qq, send_time in self._verification_queue.items() 
                        if current_time - send_time > 60]
        for qq in expired_queue:
            del self._verification_queue[qq]
        
        # 清理绑定频率限制中的过期记录
        expired_bindings = [qq for qq, bind_time in self._binding_rate_limit.items() 
                           if current_time - bind_time > self._binding_cooldown]
        for qq in expired_bindings:
            del self._binding_rate_limit[qq]
        
        # 清理表单显示缓存中的过期记录或离线玩家记录
        online_player_names = {player.name for player in self.server.online_players}
        expired_forms = []
        
        for player_name, display_time in self._form_display_cache.items():
            # 清理过期记录（5分钟）或离线玩家记录
            if (current_time - display_time > 300 or 
                player_name not in online_player_names):
                expired_forms.append(player_name)
        
        for player_name in expired_forms:
            del self._form_display_cache[player_name]
        
        # 清理离线玩家的并发绑定记录
        offline_concurrent_players = [player_name for player_name in self._concurrent_bindings 
                                     if player_name not in online_player_names]
        for player_name in offline_concurrent_players:
            self._concurrent_bindings.discard(player_name)
    
    def _can_send_verification(self, qq_number: str, player_name: str = None) -> tuple[bool, str]:
        """检查是否可以发送验证码）"""
        current_time = time.time()
        
        # 先进行快速检查：该QQ号个人冷却
        if qq_number in self._binding_rate_limit:
            cooldown_remaining = self._binding_cooldown - (current_time - self._binding_rate_limit[qq_number])
            if cooldown_remaining > 0:
                return False, f"请等待{int(cooldown_remaining)}秒后再次尝试"
        
        # 优化的验证码频率检查：使用时间窗口清理+计数
        # 清理过期记录，避免重复遍历
        expired_qq = [qq for qq, send_time in self._verification_queue.items() 
                     if current_time - send_time > 60]
        for qq in expired_qq:
            del self._verification_queue[qq]
        
        # 现在计算当前有效的验证码发送数量
        current_verification_count = len(self._verification_queue)
        concurrent_count = len(self._concurrent_bindings)
        
        # 检查是否可以立即处理
        if (current_verification_count < self._verification_rate_limit and 
            concurrent_count < self._max_concurrent_bindings):
            return True, ""
        
        # 如果无法立即处理，加入智能队列
        if player_name:
            # 检查是否已在队列中
            if not any(p_name == player_name for p_name, _, _ in self._binding_queue):
                self._binding_queue.append((player_name, qq_number, current_time))
                # 清理过期的队列项（超过5分钟）
                self._binding_queue = [(p, q, t) for p, q, t in self._binding_queue 
                                     if current_time - t < 300]
            
            # 计算队列位置
            queue_position = next((i + 1 for i, (p, _, _) in enumerate(self._binding_queue) 
                                 if p == player_name), 0)
            
            if queue_position > 0:
                estimated_wait = max(1, (queue_position - 1) * 4)  # 每4秒处理一个
                return False, f"排队中，您是第{queue_position}位，预计等待{estimated_wait}秒"
        
        # 备用消息
        if current_verification_count >= self._verification_rate_limit:
            return False, f"系统繁忙，请稍后再试（每分钟限制{self._verification_rate_limit}个验证码）"
        else:
            return False, f"当前绑定请求较多，请稍后再试（{concurrent_count}/{self._max_concurrent_bindings}）"
    
    def _register_verification_attempt(self, qq_number: str, player_name: str):
        """注册验证码发送尝试"""
        current_time = time.time()
        self._verification_queue[qq_number] = current_time
        self._concurrent_bindings.add(player_name)
    
    def _unregister_verification_attempt(self, qq_number: str, player_name: str, success: bool = True):
        """注销验证码发送尝试"""
        if not success:
            # 绑定失败，记录冷却时间
            self._binding_rate_limit[qq_number] = time.time()
        
        # 从并发绑定集合中移除
        self._concurrent_bindings.discard(player_name)
    
    def _can_show_form(self, player_name: str) -> bool:
        """检查是否可以显示绑定表单（每个玩家最多2次机会）"""
        current_time = time.time()
        
        # 检查玩家是否在线，如果在线则检查冷却时间，如果离线则清理缓存并允许显示
        player_online = any(player.name == player_name for player in self.server.online_players)
        
        if player_name in self._form_display_cache:
            last_display_time = self._form_display_cache[player_name]
            
            # 如果玩家不在线，清理缓存记录（处理异常断线情况）
            if not player_online:
                del self._form_display_cache[player_name]
                # 清理次数计数，给重新登录的玩家新的机会
                if player_name in self._form_display_count:
                    del self._form_display_count[player_name]
                self.logger.debug(f"清理离线玩家 {player_name} 的表单显示缓存和计数")
            else:
                # 玩家在线时检查冷却时间（防止短时间内重复显示）
                if current_time - last_display_time < 60:  # 缩短到1分钟冷却
                    return False
        
        # 检查显示次数是否超过限制（每个玩家最多2次机会）
        display_count = self._form_display_count.get(player_name, 0)
        if display_count >= 2:
            return False
        
        # 更新缓存和计数
        self._form_display_cache[player_name] = current_time
        self._form_display_count[player_name] = display_count + 1
        
        self.logger.debug(f"玩家 {player_name} 表单显示次数: {display_count + 1}/2")
        return True
    
    def _schedule_cleanup(self):
        """定时清理任务"""
        try:
            # 执行清理
            self.cleanup_expired_verifications()
        except Exception as e:
            self.logger.error(f"定时清理任务出错: {e}")
    
    def _schedule_group_check(self):
        """定时群成员检查任务"""
        try:
            # 更新群成员缓存
            self.update_group_members_cache()
            
            # 检查已绑定玩家是否退群
            self._check_bound_players_in_group()
        except Exception as e:
            self.logger.error(f"群成员检查任务出错: {e}")
    
    def _check_bound_players_in_group(self):
        """检查已绑定的玩家是否仍在群中（批量处理优化）"""
        if not self.get_config("force_bind_qq", True) or not self.get_config("check_group_member", True):
            return  # 如果未启用强制绑定或退群检测，不检查
        
        # 简单直接检查在线玩家
        for player in self.server.online_players:
            if self.is_player_bound(player.name, player.xuid):
                player_qq = self.get_player_qq(player.name)
                if player_qq and player_qq not in self._group_members:
                    try:
                        self.logger.info(f"检测到玩家 {player.name} (QQ: {player_qq}) 已退群，应用访客权限")
                        self._handle_player_left_group(player)
                    except Exception as e:
                        self.logger.error(f"处理退群玩家 {player.name} 时出错: {e}")
    
    def _handle_player_left_group(self, player):
        """处理玩家退群"""
        try:
            # 设置为访客权限
            self.set_player_visitor_permissions(player)
            
            # 发送退群提醒
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}[警告] 检测到您已退出QQ群{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}您现在只有访客权限，无法使用完整功能{ColorFormat.RESET}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}重新加入QQ群后权限将自动恢复{ColorFormat.RESET}")
            
            self.logger.info(f"已将退群玩家 {player.name} 设置为访客权限")
            
        except Exception as e:
            self.logger.error(f"处理退群玩家 {player.name} 时出错: {e}")
                    
    def _schedule_multi_player_cleanup(self):
        """多玩家优化的周期清理任务（简化版本）"""
        try:
            self._perform_cleanup_tasks_sync()
        except Exception as e:
            self.logger.error(f"清理任务失败: {e}")
    
    def _perform_cleanup_tasks_sync(self):
        """同步版本的清理任务（回退方案）"""
        try:
            current_time = time.time()
            
            # 清理过期的验证码发送记录
            expired_queue = []
            for qq_number, timestamp in self._verification_queue.items():
                if current_time - timestamp > 600:  # 10分钟过期
                    expired_queue.append(qq_number)
            for qq_number in expired_queue:
                del self._verification_queue[qq_number]
            
            # 清理过期的冷却时间记录
            expired_rate_limit = [qq for qq, timestamp in self._binding_rate_limit.items() 
                                if current_time - timestamp > 300]
            for qq_number in expired_rate_limit:
                del self._binding_rate_limit[qq_number]
            
            # 清理过期的表单显示缓存
            expired_form_cache = [player for player, timestamp in self._form_display_cache.items() 
                                if current_time - timestamp > 600]
            for player_name in expired_form_cache:
                del self._form_display_cache[player_name]
            
            # 清理过期的表单显示次数计数（对于已离线玩家）
            online_players = {player.name for player in self.server.online_players}
            expired_count_cache = [player for player in self._form_display_count.keys() 
                                 if player not in online_players]
            for player_name in expired_count_cache:
                del self._form_display_count[player_name]
            
            # 清理过期的待确认QQ信息（5分钟过期或玩家离线）
            expired_confirmations = []
            for player_name, qq_info in self._pending_qq_confirmations.items():
                if (current_time - qq_info["timestamp"] > 300 or  # 5分钟过期
                    player_name not in online_players):  # 玩家离线
                    expired_confirmations.append(player_name)
            for player_name in expired_confirmations:
                del self._pending_qq_confirmations[player_name]
            
            # 清理已离线玩家的并发绑定记录
            online_players = {player.name for player in self.server.online_players}
            offline_bindings = self._concurrent_bindings - online_players
            self._concurrent_bindings -= offline_bindings
            
            # 执行过期验证码清理
            self.cleanup_expired_verifications()
            
            # 记录清理日志（仅在有清理内容时）
            cleanup_total = len(expired_rate_limit) + len(expired_form_cache) + len(offline_bindings)
            if cleanup_total > 0:
                self.logger.debug(f"清理完成: 冷却记录:{len(expired_rate_limit)}, 表单缓存:{len(expired_form_cache)}, 离线绑定:{len(offline_bindings)}")
            
        except Exception as e:
            self.logger.error(f"清理任务失败: {e}")
    
    def _process_binding_queue(self):
        """处理绑定队列"""
        try:
            if not self._binding_queue:
                return
            
            current_time = time.time()
            processed_count = 0
            
            # 清理过期的队列项（超过5分钟）
            original_length = len(self._binding_queue)
            self._binding_queue = [(p, q, t) for p, q, t in self._binding_queue 
                                 if current_time - t < 300]
            expired_count = original_length - len(self._binding_queue)
            
            # 处理队列中的玩家
            while self._binding_queue and processed_count < 3:  # 每次最多处理3个
                player_name, qq_number, request_time = self._binding_queue[0]
                
                # 检查玩家是否仍在线
                online_player = None
                for player in self.server.online_players:
                    if player.name == player_name:
                        online_player = player
                        break
                
                if not online_player:
                    # 玩家离线，从队列中移除
                    self._binding_queue.pop(0)
                    self._queue_notification_sent.discard(player_name)
                    continue
                
                # 检查是否可以处理
                can_send, error_msg = self._can_send_verification(qq_number)
                if can_send:
                    # 可以处理，从队列中移除并开始绑定流程
                    self._binding_queue.pop(0)
                    self._queue_notification_sent.discard(player_name)
                    
                    # 通知玩家开始绑定
                    online_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}🎉 排队完成！开始为您处理QQ绑定{ColorFormat.RESET}")
                    
                    # 触发绑定流程
                    self.server.scheduler.run_task(
                        self,
                        lambda p=online_player: self.show_qq_binding_form(p),
                        delay=5  # 短暂延迟确保消息送达
                    )
                    
                    processed_count += 1
                else:
                    # 仍无法处理，发送队列状态更新（如果需要）
                    if player_name not in self._queue_notification_sent:
                        queue_position = next((i + 1 for i, (p, _, _) in enumerate(self._binding_queue) 
                                             if p == player_name), 0)
                        if queue_position <= 5:  # 只对前5名发送通知，避免spam
                            online_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.CYAN}⏳ 排队状态更新：您是第{queue_position}位{ColorFormat.RESET}")
                            self._queue_notification_sent.add(player_name)
                    break  # 如果第一个都无法处理，后面的也不行
            
            # 记录处理日志
            if processed_count > 0 or expired_count > 0:
                self.logger.debug(f"队列处理完成: 处理{processed_count}个请求, 清理{expired_count}个过期项, 剩余{len(self._binding_queue)}个")
                
        except Exception as e:
            self.logger.error(f"队列处理失败: {e}")
    
    def _run_loop(self):
        """在新线程中运行事件循环"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def on_disable(self) -> None:
        shutdown_msg = f"{ColorFormat.RED}qqsync_plugin {ColorFormat.RED}卸载{ColorFormat.RESET}"
        self.logger.info(shutdown_msg)
        
        # 保存所有待保存的数据
        if hasattr(self, '_auto_save_enabled') and self._auto_save_enabled:
            self.logger.info("插件关闭前保存绑定数据...")
            self.save_binding_data()
        
        # 清理所有权限附件
        if hasattr(self, '_player_attachments'):
            for player_name, attachment in self._player_attachments.items():
                try:
                    attachment.remove()
                    self.logger.info(f"已清理玩家 {player_name} 的权限附件")
                except Exception as e:
                    self.logger.warning(f"清理玩家 {player_name} 权限附件失败: {e}")
            self._player_attachments.clear()
        
        # 优雅关闭WebSocket连接和事件循环
        if hasattr(self, "_task"):
            self._task.cancel()
        if hasattr(self, "_loop"):
            self._loop.call_soon_threadsafe(self._loop.stop)
        if hasattr(self, "_thread"):
            self._thread.join(timeout=2)

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent) -> None:
        player = event.player
        player_name = player.name
        player_xuid = player.xuid
        
        # 检查玩家是否改名（通过XUID查找原有绑定）
        existing_data = self.get_player_by_xuid(player_xuid)
        if existing_data and existing_data.get("name") != player_name:
            # 玩家改名了，更新绑定数据
            old_name = existing_data.get("name")
            self.update_player_name(old_name, player_name, player_xuid)
            
            # 通知QQ群玩家改名
            if self.get_config("enable_game_to_qq", True):
                target_group = self.get_config("target_group")
                if _current_ws:
                    asyncio.run_coroutine_threadsafe(
                        send_group_msg(_current_ws, group_id=target_group, text=f"🔄 玩家 {old_name} 改名为 {player_name}"),
                        self._loop
                    )
        
        # 检查是否有临时XUID需要更新（通过QQ群验证绑定的情况）
        elif self.is_player_bound(player_name):
            player_data = self._binding_data.get(player_name, {})
            current_xuid = player_data.get("xuid", "")
            
            # 如果当前存储的是临时XUID，更新为真实XUID
            if current_xuid.startswith("temp_"):
                player_data["xuid"] = player_xuid
                player_data["last_xuid_update"] = int(time.time())
                self._binding_data[player_name] = player_data
                self._trigger_realtime_save(f"XUID更新: {player_name}")
                self.logger.info(f"已更新玩家 {player_name} 的XUID: {current_xuid} → {player_xuid}")
                
                # 发送绑定完成消息给玩家
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] 欢迎回来！您的QQ绑定已完成并更新{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您的QQ {player_data.get('qq')} 已与游戏账号完全绑定{ColorFormat.RESET}")
                
                # 确保玩家有正常权限（临时XUID更新后）
                if self.get_config("force_bind_qq", True):
                    self.server.scheduler.run_task(
                        self,
                        lambda: self.restore_player_permissions(player),
                        delay=5
                    )
        
        # 检查是否启用强制绑定QQ
        force_binding = self.get_config("force_bind_qq", True)
        
        # 检查玩家是否已绑定QQ
        if force_binding and not self.is_player_bound(player_name, player_xuid):
            # 先检查玩家是否被封禁
            if self.is_player_banned(player_name):
                # 被封禁的玩家直接告知封禁信息，不显示绑定表单
                player_data = self._binding_data.get(player_name, {})
                ban_reason = player_data.get("ban_reason", "管理员封禁")
                ban_by = player_data.get("ban_by", "系统")
                ban_time = player_data.get("ban_time")
                
                # 延迟发送封禁通知，确保玩家完全加载
                self.server.scheduler.run_task(
                    self,
                    lambda: self._send_ban_notification(player, ban_reason, ban_by, ban_time),
                    delay=10  # 0.5秒延迟
                )
            else:
                # 未被封禁的玩家显示绑定表单
                self.server.scheduler.run_task(
                    self, 
                    lambda: self.show_qq_binding_form(player),
                    delay=10  # 1秒 = 20 ticks
                )
        
        # 更新玩家加入时间（仅限已绑定QQ的玩家）
        self.update_player_join(player_name, player_xuid)
        
        # 应用权限策略（强制绑定模式下的权限控制）
        # 延迟应用权限，确保玩家完全加载
        self.server.scheduler.run_task(
            self,
            lambda: self.check_and_apply_permissions(player),
            delay=20  # 1秒延迟，确保玩家权限系统就绪
        )
        
        # 发送加入消息到QQ群（只有已绑定QQ的玩家或未启用强制绑定时才发送）
        if self.get_config("enable_game_to_qq", True):
            # 如果启用强制绑定QQ，只有已绑定QQ的玩家加入才发送到QQ群
            should_send_join_msg = True
            if self.get_config("force_bind_qq", True):
                should_send_join_msg = self.is_player_bound(player_name, player_xuid)
            
            if should_send_join_msg:
                target_group = self.get_config("target_group")
                if _current_ws:
                    asyncio.run_coroutine_threadsafe(
                        send_group_msg(_current_ws, group_id=target_group, text=f"🟢 {player_name} 加入了服务器"),
                        self._loop
                    )
                else:
                    warning_msg = f"{ColorFormat.YELLOW}WebSocket未连接，无法发送玩家加入消息{ColorFormat.RESET}"
                    self.logger.warning(warning_msg)

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent) -> None:
        player_name = event.player.name
        
        # 更新玩家离开时间（仅限已绑定QQ的玩家）
        self.update_player_quit(player_name)
        
        # 清理权限附件
        self.cleanup_player_permissions(player_name)
        
        # 清理玩家相关缓存（修复异常断线后无法正常绑定的问题）
        self._cleanup_player_caches(player_name)
        
        # 发送离开消息到QQ群（只有已绑定QQ的玩家或未启用强制绑定时才发送）
        if self.get_config("enable_game_to_qq", True):
            # 如果启用强制绑定QQ，只有已绑定QQ的玩家离开才发送到QQ群
            should_send_quit_msg = True
            if self.get_config("force_bind_qq", True):
                should_send_quit_msg = self.is_player_bound(player_name)
            
            if should_send_quit_msg:
                target_group = self.get_config("target_group")
                if _current_ws:
                    asyncio.run_coroutine_threadsafe(
                        send_group_msg(_current_ws, group_id=target_group, text=f"🔴 {player_name} 离开了服务器"),
                        self._loop
                    )
                else:
                    warning_msg = f"{ColorFormat.YELLOW}WebSocket未连接，无法发送玩家离开消息{ColorFormat.RESET}"
                    self.logger.warning(warning_msg)

    @event_handler
    def on_player_chat(self, event: PlayerChatEvent) -> None:
        player_name = event.player.name
        
        # 检查访客权限 - 如果是访客则阻止聊天
        if self.is_player_visitor(player_name, event.player.xuid):
            event.is_cancelled = True
            visitor_reason = self.get_player_visitor_reason(player_name, event.player.xuid)
            
            if visitor_reason == "已被封禁":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法使用聊天功能（原因：已被封禁）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}")
            elif visitor_reason == "未绑定QQ":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法使用聊天功能（原因：未绑定QQ）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 绑定QQ获得完整权限{ColorFormat.RESET}")
            elif visitor_reason == "已退出QQ群":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法使用聊天功能（原因：已退出QQ群）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}重新加入QQ群后权限将自动恢复{ColorFormat.RESET}")
                target_group = self.get_config("target_group")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}目标QQ群：{target_group}{ColorFormat.RESET}")
            else:
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法使用聊天功能{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 开始绑定流程{ColorFormat.RESET}")
            return
        
        # 正常玩家的聊天转发到QQ群
        if not self.get_config("enable_game_to_qq", True):
            return
        
        # 如果启用强制绑定QQ，只有已绑定QQ的玩家聊天才转发到QQ群
        if self.get_config("force_bind_qq", True):
            if not self.is_player_bound(player_name, event.player.xuid):
                return  # 未绑定QQ的玩家聊天不转发到QQ群
        
        message = event.message
        target_group = self.get_config("target_group")
        
        # 直接使用玩家名称，不显示QQ号
        if _current_ws:
            asyncio.run_coroutine_threadsafe(
                send_group_msg(_current_ws, group_id=target_group, text=f"💬 {player_name}: {message}"),
                self._loop
            )
        else:
            warning_msg = f"{ColorFormat.YELLOW}WebSocket未连接，无法发送聊天消息{ColorFormat.RESET}"
            self.logger.warning(warning_msg)

    @event_handler
    def on_player_death(self, event: PlayerDeathEvent) -> None:
        if not self.get_config("enable_game_to_qq", True):
            return
            
        # 如果启用强制绑定QQ，只有已绑定QQ的玩家死亡消息才转发到QQ群
        if self.get_config("force_bind_qq", True):
            if not self.is_player_bound(event.player.name, event.player.xuid):
                return  # 未绑定QQ的玩家死亡消息不转发到QQ群
        
        target_group = self.get_config("target_group")
        if _current_ws:
            asyncio.run_coroutine_threadsafe(
                send_group_msg(_current_ws, group_id=target_group, text=f"💀 {event.death_message}"),
                self._loop
            )

    @event_handler
    def on_block_break(self, event: BlockBreakEvent) -> None:
        """阻止访客破坏方块"""
        player_name = event.player.name
        
        if self.is_player_visitor(player_name, event.player.xuid):
            event.is_cancelled = True
            visitor_reason = self.get_player_visitor_reason(player_name, event.player.xuid)
            
            if visitor_reason == "已被封禁":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法破坏方块（原因：已被封禁）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}")
            elif visitor_reason == "未绑定QQ":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法破坏方块（原因：未绑定QQ）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 绑定QQ获得完整权限{ColorFormat.RESET}")
            elif visitor_reason == "已退出QQ群":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法破坏方块（原因：已退出QQ群）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}重新加入QQ群后权限将自动恢复{ColorFormat.RESET}")
            else:
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法破坏方块{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 开始绑定流程{ColorFormat.RESET}")

    @event_handler
    def on_block_place(self, event: BlockPlaceEvent) -> None:
        """阻止访客放置方块"""
        player_name = event.player.name
        
        if self.is_player_visitor(player_name, event.player.xuid):
            event.is_cancelled = True
            visitor_reason = self.get_player_visitor_reason(player_name, event.player.xuid)
            
            if visitor_reason == "已被封禁":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法放置方块（原因：已被封禁）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}")
            elif visitor_reason == "未绑定QQ":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法放置方块（原因：未绑定QQ）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 绑定QQ获得完整权限{ColorFormat.RESET}")
            elif visitor_reason == "已退出QQ群":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法放置方块（原因：已退出QQ群）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}重新加入QQ群后权限将自动恢复{ColorFormat.RESET}")
            else:
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法放置方块{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 开始绑定流程{ColorFormat.RESET}")

    @event_handler
    def on_player_interact(self, event: PlayerInteractEvent) -> None:
        """阻止访客与方块/物品交互"""
        player_name = event.player.name
        
        if self.is_player_visitor(player_name, event.player.xuid):
            # 允许右键空手（查看）和使用命令方块，但阻止其他交互
            if hasattr(event, 'block') and event.block:
                # 获取方块类型
                block_type = str(event.block.type).lower()
                
                # 允许查看某些信息方块，但禁止操作功能方块
                interactive_blocks = [
                    'chest', 'barrel', 'shulker_box', 'hopper',
                    'furnace', 'blast_furnace', 'smoker',
                    'crafting_table', 'anvil', 'enchanting_table',
                    'brewing_stand', 'cauldron',
                    'door', 'trapdoor', 'gate',
                    'button', 'lever', 'pressure_plate',
                    'dispenser', 'dropper', 'piston',
                    'bed', 'note_block', 'jukebox'
                ]
                
                # 检查是否为交互方块
                is_interactive = any(block_name in block_type for block_name in interactive_blocks)
                
                if is_interactive:
                    event.is_cancelled = True
                    visitor_reason = self.get_player_visitor_reason(player_name, event.player.xuid)
                    
                    if visitor_reason == "已被封禁":
                        event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法与此方块交互（原因：已被封禁）{ColorFormat.RESET}")
                        event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}")
                    elif visitor_reason == "未绑定QQ":
                        event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法与此方块交互（原因：未绑定QQ）{ColorFormat.RESET}")
                        event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 绑定QQ获得完整权限{ColorFormat.RESET}")
                    elif visitor_reason == "已退出QQ群":
                        event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法与此方块交互（原因：已退出QQ群）{ColorFormat.RESET}")
                        event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}重新加入QQ群后权限将自动恢复{ColorFormat.RESET}")
                    else:
                        event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法与此方块交互{ColorFormat.RESET}")
                        event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 开始绑定流程{ColorFormat.RESET}")

    @event_handler  
    def on_player_interact_actor(self, event: PlayerInteractActorEvent) -> None:
        """阻止访客与实体交互（包括攻击）"""
        player_name = event.player.name
        
        if self.is_player_visitor(player_name, event.player.xuid):
            event.is_cancelled = True
            visitor_reason = self.get_player_visitor_reason(player_name, event.player.xuid)
            
            # 检查是否为攻击行为
            action_type = "交互"
            if hasattr(event, 'action') and event.action:
                action_str = str(event.action).lower()
                if 'attack' in action_str or 'damage' in action_str or 'hit' in action_str:
                    action_type = "攻击"
            
            if visitor_reason == "已被封禁":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法与实体{action_type}（原因：已被封禁）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}")
            elif visitor_reason == "未绑定QQ":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法与实体{action_type}（原因：未绑定QQ）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 绑定QQ获得完整权限{ColorFormat.RESET}")
            elif visitor_reason == "已退出QQ群":
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法与实体{action_type}（原因：已退出QQ群）{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}重新加入QQ群后权限将自动恢复{ColorFormat.RESET}")
            else:
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法与实体{action_type}{ColorFormat.RESET}")
                event.player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 开始绑定流程{ColorFormat.RESET}")
    
    @event_handler
    def on_actor_damage(self, event: ActorDamageEvent) -> None:
        """阻止访客攻击实体"""
        damage_source = event.damage_source
        attacking_player = None
        
        # 尝试获取攻击的玩家
        if hasattr(damage_source, 'actor') and damage_source.actor:
            source_actor = damage_source.actor
            if hasattr(source_actor, 'name') and hasattr(source_actor, 'xuid'):
                attacking_player = source_actor
        
        # 备用方法：通过 damage_source.entity
        if not attacking_player and hasattr(damage_source, 'entity') and damage_source.entity:
            source_entity = damage_source.entity
            if hasattr(source_entity, 'name') and hasattr(source_entity, 'xuid'):
                attacking_player = source_entity
        
        # 如果找到攻击者，检查权限
        if attacking_player:
            player_name = attacking_player.name
            
            if self.is_player_visitor(player_name, attacking_player.xuid):
                event.is_cancelled = True
                visitor_reason = self.get_player_visitor_reason(player_name, attacking_player.xuid)
                
                if visitor_reason == "已被封禁":
                    attacking_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法攻击实体（原因：已被封禁）{ColorFormat.RESET}")
                    attacking_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}")
                elif visitor_reason == "未绑定QQ":
                    attacking_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法攻击实体（原因：未绑定QQ）{ColorFormat.RESET}")
                    attacking_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 绑定QQ获得完整权限{ColorFormat.RESET}")
                elif visitor_reason == "已退出QQ群":
                    attacking_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法攻击实体（原因：已退出QQ群）{ColorFormat.RESET}")
                    attacking_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}重新加入QQ群后权限将自动恢复{ColorFormat.RESET}")
                else:
                    attacking_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}访客无法攻击实体{ColorFormat.RESET}")
                    attacking_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 开始绑定流程{ColorFormat.RESET}")
                
                # 记录阻止信息
                self.logger.info(f"通过 ActorDamageEvent 阻止访客 {player_name} 攻击实体 (原因: {visitor_reason})")


# ========= 工具 =========
def format_time_duration(seconds):
    """格式化时间长度"""
    if seconds <= 0:
        return "0秒"
    
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0:
        parts.append(f"{hours}小时")
    if minutes > 0:
        parts.append(f"{minutes}分钟")
    if secs > 0 or not parts:  # 如果没有其他单位，至少显示秒
        parts.append(f"{secs}秒")
    
    return "".join(parts)

def format_timestamp(timestamp):
    """格式化时间戳"""
    if not timestamp:
        return "无记录"
    
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def parse_qq_message(message_data):
    """
    解析QQ消息，将非文本内容转换为对应的标识符
    """
    
    # 获取原始消息文本
    raw_message = message_data.get("raw_message", "")
    
    if raw_message:
        # 使用正则表达式解析CQ码
        def replace_cq_code(match):
            cq_type = match.group(1)
            if cq_type == "image":
                return "[图片]"
            elif cq_type == "video":
                return "[视频]"
            elif cq_type == "record":
                return "[语音]"
            elif cq_type == "face":
                return "[表情]"
            elif cq_type == "at":
                # 提取@的QQ号
                params = match.group(2)
                if "qq=all" in params:
                    return "@全体成员"
                else:
                    qq_match = re.search(r'qq=(\d+)', params)
                    if qq_match:
                        return f"@{qq_match.group(1)}"
                    return "@某人"
            elif cq_type == "reply":
                return "[回复]"
            elif cq_type == "forward":
                return "[转发]"
            elif cq_type == "file":
                return "[文件]"
            elif cq_type == "share":
                return "[分享]"
            elif cq_type == "location":
                return "[位置]"
            elif cq_type == "music":
                return "[音乐]"
            elif cq_type == "xml" or cq_type == "json":
                return "[卡片]"
            else:
                return "[非文本]"
        
        # 匹配CQ码格式: [CQ:type,param1=value1,param2=value2]
        cq_pattern = r'\[CQ:([^,\]]+)(?:,([^\]]*))?\]'
        processed_message = re.sub(cq_pattern, replace_cq_code, raw_message)
        
        # 如果处理后的消息不为空，返回处理结果
        if processed_message.strip():
            return processed_message.strip()
    
    # 如果都没有内容，返回空消息标识
    return "[空消息]"

async def send_group_msg(ws, group_id: int, text: str):
    payload = {
        "action": "send_group_msg",
        "params": {"group_id": group_id, "message": text.strip()},
    }
    try:
        await ws.send(json.dumps(payload))
    except Exception as e:
        if _plugin_instance:
            error_msg = f"{ColorFormat.RED}消息发送失败: {ColorFormat.YELLOW}{e}{ColorFormat.RESET}"
            _plugin_instance.logger.error(error_msg)

async def send_private_msg(ws, user_id: int, text: str):
    """发送私聊消息"""
    payload = {
        "action": "send_private_msg",
        "params": {"user_id": user_id, "message": text.strip()},
    }
    try:
        await ws.send(json.dumps(payload))
        if _plugin_instance:
            _plugin_instance.logger.info(f"{ColorFormat.GREEN}验证码已发送到QQ: {user_id}{ColorFormat.RESET}")
    except Exception as e:
        if _plugin_instance:
            error_msg = f"{ColorFormat.RED}私聊消息发送失败: {ColorFormat.YELLOW}{e}{ColorFormat.RESET}"
            _plugin_instance.logger.error(error_msg)

async def set_group_card(ws, group_id: int, user_id: int, card: str):
    """设置群昵称"""
    payload = {
        "action": "set_group_card",
        "params": {
            "group_id": group_id,
            "user_id": user_id,
            "card": card.strip()
        },
    }
    try:
        await ws.send(json.dumps(payload))
        if _plugin_instance:
            _plugin_instance.logger.info(f"{ColorFormat.GREEN}已设置QQ {user_id} 的群昵称为: {card}{ColorFormat.RESET}")
        return True
    except Exception as e:
        if _plugin_instance:
            error_msg = f"{ColorFormat.RED}设置群昵称失败: {ColorFormat.YELLOW}{e}{ColorFormat.RESET}"
            _plugin_instance.logger.error(error_msg)
        return False

async def get_group_member_list(ws, group_id: int):
    """获取群成员列表"""
    payload = {
        "action": "get_group_member_list",
        "params": {
            "group_id": group_id,
            "no_cache": True
        }
    }
    try:
        await ws.send(json.dumps(payload))
        if _plugin_instance:
            _plugin_instance.logger.debug(f"已请求群 {group_id} 的成员列表")
        return True
    except Exception as e:
        if _plugin_instance:
            error_msg = f"{ColorFormat.RED}获取群成员列表失败: {ColorFormat.YELLOW}{e}{ColorFormat.RESET}"
            _plugin_instance.logger.error(error_msg)
        return False

# ========= 业务处理 =========
async def handle_message(ws, data: dict):
    group_id = data.get("group_id")
    user_id = str(data.get("user_id"))
    nickname = data.get("sender", {}).get("nickname", user_id)
    
    # 解析消息内容，处理非文本消息
    processed_msg = parse_qq_message(data)

    if _plugin_instance:
        msg_log = f"{ColorFormat.LIGHT_PURPLE}QQ消息: {ColorFormat.AQUA}{nickname}{ColorFormat.GRAY}({user_id}){ColorFormat.WHITE} -> {ColorFormat.YELLOW}{processed_msg}{ColorFormat.RESET}"
        _plugin_instance.logger.info(msg_log)
    
    # 如果不是命令，则转发消息到游戏内
    if not processed_msg.startswith("/"):
        if _plugin_instance and processed_msg and _plugin_instance.get_config("enable_qq_to_game", True):
            # 检查发送者是否绑定了游戏角色
            bound_player = _plugin_instance.get_qq_player(user_id)
            display_name = bound_player if bound_player else nickname
            
            # 格式化消息
            game_msg = f"{ColorFormat.LIGHT_PURPLE}[QQ群]{ColorFormat.RESET} {ColorFormat.AQUA}{display_name}{ColorFormat.RESET}: {processed_msg}"
            
            # 在主线程中广播消息
            def broadcast_qq_message():
                # 如果启用强制绑定QQ，只向已绑定QQ的玩家发送消息
                # 如果未启用强制绑定QQ，向所有玩家广播消息
                if _plugin_instance.get_config("force_bind_qq", True):
                    # 只向已绑定QQ的玩家发送消息
                    for player in _plugin_instance.server.online_players:
                        if _plugin_instance.is_player_bound(player.name, player.xuid):
                            player.send_message(game_msg)
                else:
                    # 向所有玩家广播消息（原有行为）
                    _plugin_instance.server.broadcast_message(game_msg)
            
            # 使用调度器在主线程中执行广播
            _plugin_instance.server.scheduler.run_task(
                _plugin_instance, 
                broadcast_qq_message
            )
        return

    # 命令处理仍然使用原始消息
    cmd_parts = processed_msg[1:].split()
    if not cmd_parts:
        return
    
    cmd = cmd_parts[0]
    args = cmd_parts[1:] if len(cmd_parts) > 1 else []
    reply = ""

    if cmd == "help" and len(cmd_parts) == 1:
        # 检查发送者是否为管理员
        admins = _plugin_instance.get_config("admins", [])
        is_admin = bool(admins and str(user_id) in admins)
        
        # 动态生成帮助信息，根据force_bind_qq状态和管理员身份决定显示内容
        force_bind_enabled = _plugin_instance.get_config("force_bind_qq", True)
        
        if force_bind_enabled:
            # 如果启用强制绑定，显示完整的绑定相关功能
            base_help = "QQsync群服互通 - 命令：\n\n[查询命令]：\n/help — 显示本帮助信息\n/list — 查看在线玩家列表\n/tps — 查看服务器性能指标\n/info — 查看服务器综合信息\n/bindqq — 查看您的详细账户信息\n/verify <验证码> — 验证QQ绑定"
            
            if is_admin:
                # 管理员可以看到管理命令
                admin_commands = "\n\n[管理命令]：\n/cmd <命令> — 执行服务器命令\n/who <玩家名|QQ号> — 查询玩家详细信息\n/unbindqq <玩家名|QQ号> — 解绑玩家的QQ绑定"
                ban_commands = "\n/ban <玩家名> [原因] — 封禁玩家，禁止QQ绑定\n/unban <玩家名> — 解除玩家封禁\n/banlist — 查看封禁列表"
                other_commands = "\n/tog_qq — 切换QQ消息转发开关 \n/tog_game — 切换游戏转发开关\n/reload — 重新加载配置文件"
            else:
                # 普通用户不显示管理命令
                admin_commands = ""
                ban_commands = ""
                other_commands = ""
        else:
            # 如果未启用强制绑定，不显示绑定相关功能
            base_help = "QQsync群服互通 - 命令：\n\n[查询命令]：\n/help — 显示本帮助信息\n/list — 查看在线玩家列表\n/tps — 查看服务器性能指标\n/info — 查看服务器综合信息"
            
            if is_admin:
                # 管理员可以看到管理命令
                admin_commands = "\n\n[管理命令]：\n/cmd <命令> — 执行服务器命令\n/who <玩家名> — 查询玩家详细信息"
                ban_commands = ""
                other_commands = "\n/tog_qq — 切换QQ消息转发开关 \n/tog_game — 切换游戏转发开关\n/reload — 重新加载配置文件"
            else:
                # 普通用户不显示管理命令
                admin_commands = ""
                ban_commands = ""
                other_commands = ""
        
        reply = base_help + admin_commands + ban_commands + other_commands

    elif cmd == "list" and len(cmd_parts) == 1:
        if _plugin_instance:
            try:
                all_players = _plugin_instance.server.online_players
                players = []
                for player in all_players:
                    if player and hasattr(player, 'name') and player.name is not None:
                        players.append(player.name)
                    else:
                        _plugin_instance.logger.warning(f"发现无效玩家对象: {player}")
                
                player_count = len(players)
                max_players = _plugin_instance.server.max_players
                if players:
                    reply = f"在线玩家 ({player_count}/{max_players})：\n" + "\n".join(players)
                else:
                    reply = f"当前没有在线玩家 (0/{max_players})"
            except Exception as e:
                _plugin_instance.logger.error(f"获取玩家列表时出错: {e}")
                reply = f"获取玩家列表失败: {e}"
        else:
            reply = "插件未正确初始化"

    elif cmd == "tps" and len(cmd_parts) == 1:
        if _plugin_instance:
            current_tps = _plugin_instance.server.current_tps
            average_tps = _plugin_instance.server.average_tps
            current_mspt = _plugin_instance.server.current_mspt
            average_mspt = _plugin_instance.server.average_mspt
            reply = f"服务器性能：\n当前 TPS: {current_tps:.2f}\n平均 TPS: {average_tps:.2f}\n当前 MSPT: {current_mspt:.2f}ms\n平均 MSPT: {average_mspt:.2f}ms"
        else:
            reply = "插件未正确初始化"

    elif cmd == "info" and len(cmd_parts) == 1:
        if _plugin_instance:
            try:
                player_count = len(_plugin_instance.server.online_players)
                max_players = _plugin_instance.server.max_players
                server_name = _plugin_instance.server.name
                port = _plugin_instance.server.port
                server_version = _plugin_instance.server.version
                minecraft_version = _plugin_instance.server.minecraft_version
                online_mode = "在线模式" if _plugin_instance.server.online_mode else "离线模式"
                current_tps = _plugin_instance.server.current_tps
                
                reply = f"服务器信息：\n名称: {server_name}\n端口: {port}\n版本: Endstone {server_version} (Minecraft {minecraft_version})\n模式: {online_mode}\n玩家: {player_count}/{max_players}\nTPS: {current_tps:.1f}"
            except Exception as e:
                _plugin_instance.logger.error(f"获取服务器信息时出错: {e}")
                reply = f"获取服务器信息失败: {e}"
        else:
            reply = "插件未正确初始化"

    elif cmd == "cmd" and len(args) >= 1:
        admins = _plugin_instance.get_config("admins", [])
        if not admins or str(user_id) in admins:
            server_cmd = " ".join(args)
            try:
                # 直接执行命令，不捕获输出
                _plugin_instance.server.scheduler.run_task(
                    _plugin_instance,
                    lambda: _plugin_instance.server.dispatch_command(_plugin_instance.server.command_sender, server_cmd)
                )
                reply = f"[成功] 命令已执行: {server_cmd}"
            except Exception as e:
                reply = f"[失败] 执行出错：{e}"
        else:
            reply = "该命令仅限管理员使用"
    
    elif cmd == "cmd" and len(args) == 0:
        reply = "用法：/cmd <服务器命令>"

    elif cmd == "reload" and len(cmd_parts) == 1:
        admins = _plugin_instance.get_config("admins", [])
        if not admins or str(user_id) in admins:
            if _plugin_instance.reload_config():
                reply = "[成功] 配置文件已重新加载"
            else:
                reply = "[失败] 配置文件重新加载失败"
        else:
            reply = "该命令仅限管理员使用"

    elif cmd == "tog_qq" and len(cmd_parts) == 1:
        admins = _plugin_instance.get_config("admins", [])
        if not admins or str(user_id) in admins:
            current_state = _plugin_instance.get_config("enable_qq_to_game", True)
            _plugin_instance._config["enable_qq_to_game"] = not current_state
            _plugin_instance.save_config()
            status = "启用" if not current_state else "禁用"
            icon = "[开启]" if not current_state else "[关闭]"
            reply = f"{icon} QQ消息转发已{status}"
        else:
            reply = "该命令仅限管理员使用"

    elif cmd == "tog_game" and len(cmd_parts) == 1:
        admins = _plugin_instance.get_config("admins", [])
        if not admins or str(user_id) in admins:
            current_state = _plugin_instance.get_config("enable_game_to_qq", True)
            _plugin_instance._config["enable_game_to_qq"] = not current_state
            _plugin_instance.save_config()
            status = "启用" if not current_state else "禁用"
            icon = "[开启]" if not current_state else "[关闭]"
            reply = f"{icon} 游戏消息转发已{status}"
        else:
            reply = "该命令仅限管理员使用"

    elif cmd == "bindqq" and len(cmd_parts) == 1:
        # 查看绑定状态（详细信息）
        bound_player = _plugin_instance.get_qq_player(user_id)
        if bound_player:
            # 获取玩家详细数据
            player_data = _plugin_instance._binding_data.get(bound_player, {})
            
            # 构建详细信息
            reply_parts = [f"=== 您的账户详细信息 ==="]
            reply_parts.append(f"绑定角色: {bound_player}")
            reply_parts.append(f"绑定QQ: {user_id}")
            
            # XUID信息
            xuid = player_data.get("xuid", "")
            if xuid:
                reply_parts.append(f"XUID: {xuid}")
            
            # 检查在线状态和权限
            is_online = False
            is_visitor = False
            visitor_reason = ""
            
            for player in _plugin_instance.server.online_players:
                if player.name == bound_player:
                    is_online = True
                    is_visitor = _plugin_instance.is_player_visitor(bound_player, player.xuid)
                    visitor_reason = _plugin_instance.get_player_visitor_reason(bound_player, player.xuid)
                    break
            
            # 当前状态
            if is_online:
                reply_parts.append("当前状态: 在线")
            else:
                reply_parts.append("当前状态: 离线")
            
            # 权限状态
            if is_visitor:
                reply_parts.append(f"权限状态: 访客权限 ({visitor_reason})")
            else:
                reply_parts.append("权限状态: 正常权限")
            
            # 群状态检查
            if (_plugin_instance.get_config("force_bind_qq", True) and 
                _plugin_instance.get_config("check_group_member", True)):
                if (hasattr(_plugin_instance, '_group_members') and 
                    _plugin_instance._group_members and 
                    user_id in _plugin_instance._group_members):
                    reply_parts.append("群状态: 在群内")
                else:
                    reply_parts.append("群状态: 已退群或未加群")
            
            # 封禁状态
            is_banned = _plugin_instance.is_player_banned(bound_player)
            if is_banned:
                ban_time = format_timestamp(player_data.get("ban_time"))
                ban_by = player_data.get("ban_by", "未知")
                ban_reason = player_data.get("ban_reason", "无原因")
                reply_parts.append(f"封禁状态: 已封禁")
                reply_parts.append(f"封禁时间: {ban_time}")
                reply_parts.append(f"封禁操作者: {ban_by}")
                reply_parts.append(f"封禁原因: {ban_reason}")
            else:
                reply_parts.append("封禁状态: 正常")
            
            # 绑定历史
            bind_history = _plugin_instance.get_player_binding_history(bound_player)
            if bind_history:
                reply_parts.append(f"绑定状态: {bind_history.get('status', '未知')}")
                
                if bind_history.get("bind_time"):
                    reply_parts.append(f"首次绑定: {format_timestamp(bind_history['bind_time'])}")
                
                if bind_history.get("unbind_time"):
                    reply_parts.append(f"解绑时间: {format_timestamp(bind_history['unbind_time'])}")
                    reply_parts.append(f"解绑操作者: {bind_history.get('unbind_by', '未知')}")
                
                if bind_history.get("rebind_time"):
                    reply_parts.append(f"重新绑定: {format_timestamp(bind_history['rebind_time'])}")
                
                if bind_history.get("original_qq") and bind_history.get("original_qq") != user_id:
                    reply_parts.append(f"原QQ号: {bind_history['original_qq']}")
                
                if bind_history.get("previous_qq") and bind_history.get("previous_qq") != user_id:
                    reply_parts.append(f"前一个QQ: {bind_history['previous_qq']}")
            
            # 游戏统计
            playtime_info = _plugin_instance.get_player_playtime_info(bound_player)
            if playtime_info:
                total_time = format_time_duration(playtime_info["total_playtime"])
                session_count = playtime_info["session_count"]
                
                reply_parts.append(f"总在线时间: {total_time}")
                reply_parts.append(f"游戏次数: {session_count}次")
                
                if playtime_info["is_online"]:
                    current_session = format_time_duration(playtime_info["current_session_time"])
                    last_join = format_timestamp(playtime_info["last_join_time"])
                    reply_parts.append(f"本次加入: {last_join}")
                    reply_parts.append(f"本次在线: {current_session}")
                else:
                    last_join = format_timestamp(playtime_info["last_join_time"])
                    last_quit = format_timestamp(playtime_info["last_quit_time"])
                    reply_parts.append(f"最后加入: {last_join}")
                    reply_parts.append(f"最后离开: {last_quit}")
            
            # 改名历史
            if player_data.get("last_name_update"):
                last_update = format_timestamp(player_data["last_name_update"])
                reply_parts.append(f"最后改名: {last_update}")
            
            reply = "\n".join(reply_parts)
        else:
            reply = "您的QQ尚未绑定游戏角色\n请在游戏中完成绑定流程"

    elif cmd == "who" and len(args) == 1:
        # 查询指定玩家的绑定QQ（仅管理员）
        admins = _plugin_instance.get_config("admins", [])
        if not admins or str(user_id) in admins:
            search_input = args[0]
            target_player = None
            player_data = None
            
            # 判断输入是QQ号还是玩家名
            if search_input.isdigit():
                # 输入的是QQ号，查找对应的玩家（包括历史绑定）
                target_player = _plugin_instance.get_qq_player_history(search_input)
                if not target_player:
                    reply = f"未找到绑定QQ号 {search_input} 的玩家"
                else:
                    player_data = _plugin_instance._binding_data.get(target_player, {})
            else:
                # 输入的是玩家名
                target_player = search_input
                if target_player not in _plugin_instance._binding_data:
                    reply = f"玩家 {target_player} 未找到任何记录"
                else:
                    player_data = _plugin_instance._binding_data[target_player]
            
            # 如果找到了玩家数据，构建详细信息
            if target_player and player_data:
                player_qq = player_data.get("qq", "")
                
                # 构建详细信息
                reply_parts = [f"=== 玩家 {target_player} 详细信息 ==="]
                
                # 基础绑定信息
                if player_qq:
                    reply_parts.append(f"绑定QQ: {player_qq}")
                    
                    # 检查是否在群内（如果启用了退群检测）
                    if (_plugin_instance.get_config("force_bind_qq", True) and 
                        _plugin_instance.get_config("check_group_member", True)):
                        if (hasattr(_plugin_instance, '_group_members') and 
                            _plugin_instance._group_members and 
                            player_qq in _plugin_instance._group_members):
                            reply_parts.append("群状态: 在群内")
                        else:
                            reply_parts.append("群状态: 已退群或未加群")
                    else:
                        reply_parts.append("群状态: 未启用检测")
                else:
                    reply_parts.append("绑定QQ: 未绑定")
                
                # XUID信息
                xuid = player_data.get("xuid", "")
                if xuid:
                    reply_parts.append(f"XUID: {xuid}")
                else:
                    reply_parts.append("XUID: 未记录")
                
                # 权限状态
                is_visitor = False
                visitor_reason = ""
                for player in _plugin_instance.server.online_players:
                    if player.name == target_player:
                        is_visitor = _plugin_instance.is_player_visitor(target_player, player.xuid)
                        visitor_reason = _plugin_instance.get_player_visitor_reason(target_player, player.xuid)
                        break
                
                if is_visitor:
                    reply_parts.append(f"权限状态: 访客权限 ({visitor_reason})")
                else:
                    reply_parts.append("权限状态: 正常权限")
                
                # 封禁状态
                is_banned = _plugin_instance.is_player_banned(target_player)
                if is_banned:
                    ban_time = format_timestamp(player_data.get("ban_time"))
                    ban_by = player_data.get("ban_by", "未知")
                    ban_reason = player_data.get("ban_reason", "无原因")
                    reply_parts.append(f"封禁状态: 已封禁")
                    reply_parts.append(f"封禁时间: {ban_time}")
                    reply_parts.append(f"封禁操作者: {ban_by}")
                    reply_parts.append(f"封禁原因: {ban_reason}")
                else:
                    reply_parts.append("封禁状态: 正常")
                
                # 绑定历史
                bind_history = _plugin_instance.get_player_binding_history(target_player)
                if bind_history:
                    reply_parts.append(f"绑定状态: {bind_history.get('status', '未知')}")
                    
                    if bind_history.get("bind_time"):
                        reply_parts.append(f"首次绑定: {format_timestamp(bind_history['bind_time'])}")
                    
                    if bind_history.get("unbind_time"):
                        reply_parts.append(f"解绑时间: {format_timestamp(bind_history['unbind_time'])}")
                        reply_parts.append(f"解绑操作者: {bind_history.get('unbind_by', '未知')}")
                    
                    if bind_history.get("rebind_time"):
                        reply_parts.append(f"重新绑定: {format_timestamp(bind_history['rebind_time'])}")
                    
                    if bind_history.get("original_qq"):
                        reply_parts.append(f"原QQ号: {bind_history['original_qq']}")
                    
                    if bind_history.get("previous_qq"):
                        reply_parts.append(f"前一个QQ: {bind_history['previous_qq']}")
                
                # 游戏统计
                playtime_info = _plugin_instance.get_player_playtime_info(target_player)
                if playtime_info:
                    total_time = format_time_duration(playtime_info["total_playtime"])
                    session_count = playtime_info["session_count"]
                    
                    reply_parts.append(f"总在线时间: {total_time}")
                    reply_parts.append(f"游戏次数: {session_count}次")
                    
                    if playtime_info["is_online"]:
                        current_session = format_time_duration(playtime_info["current_session_time"])
                        last_join = format_timestamp(playtime_info["last_join_time"])
                        reply_parts.append(f"当前状态: 在线")
                        reply_parts.append(f"本次加入: {last_join}")
                        reply_parts.append(f"本次在线: {current_session}")
                    else:
                        last_join = format_timestamp(playtime_info["last_join_time"])
                        last_quit = format_timestamp(playtime_info["last_quit_time"])
                        reply_parts.append(f"当前状态: 离线")
                        reply_parts.append(f"最后加入: {last_join}")
                        reply_parts.append(f"最后离开: {last_quit}")
                
                # 改名历史
                if player_data.get("last_name_update"):
                    last_update = format_timestamp(player_data["last_name_update"])
                    reply_parts.append(f"最后改名: {last_update}")
                
                reply = "\n".join(reply_parts)
        else:
            reply = "该命令仅限管理员使用"

    elif cmd == "who" and len(args) == 0:
        reply = "用法：/who <玩家名|QQ号> - 查询玩家详细信息（绑定状态、游戏统计、权限状态等）"

    elif cmd == "verify" and len(args) == 1:
        # 通过QQ群验证（输入验证码）- 多玩家优化版本
        verification_code = args[0]
        
        # 多玩家优化：检查验证码验证频率限制
        current_time = time.time()
        if user_id in _plugin_instance._binding_rate_limit:
            last_attempt = _plugin_instance._binding_rate_limit[user_id]
            if current_time - last_attempt < 10:  # 10秒冷却时间
                reply = f"验证过于频繁，请等待 {int(10 - (current_time - last_attempt))} 秒后再试"
                return reply
        
        if user_id in _verification_codes:
            code_info = _verification_codes[user_id]
            if time.time() - code_info["timestamp"] > 300:  # 5分钟过期
                del _verification_codes[user_id]
                # 同时清理对应的待验证数据
                player_name = code_info.get("player_name")
                if player_name and player_name in _pending_verifications:
                    del _pending_verifications[player_name]
                reply = "验证码已过期，请重新在游戏中申请绑定"
            elif verification_code == code_info["code"]:
                # 验证成功，直接完成绑定
                player_name = code_info["player_name"]
                
                # 检查玩家是否在线以获取XUID
                online_player = None
                if _plugin_instance:
                    for player in _plugin_instance.server.online_players:
                        if player.name == player_name:
                            online_player = player
                            break
                
                if online_player:
                    # 玩家在线，直接绑定
                    _plugin_instance.bind_player_qq(player_name, online_player.xuid, user_id)
                    
                    # 设置QQ群昵称为玩家名（仅在启用QQ绑定且同步群昵称时）
                    if (_current_ws and 
                        _plugin_instance.get_config("force_bind_qq", True) and 
                        _plugin_instance.get_config("sync_group_card", True)):
                        target_group = _plugin_instance.get_config("target_group")
                        asyncio.run_coroutine_threadsafe(
                            set_group_card(_current_ws, group_id=target_group, user_id=int(user_id), card=player_name),
                            _plugin_instance._loop
                        )
                    
                    # 发送成功消息给玩家（如果在线）
                    online_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}🎉 QQ绑定成功！{ColorFormat.RESET}")
                    online_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您的QQ {user_id} 已与游戏账号绑定{ColorFormat.RESET}")
                    
                    # 恢复玩家权限（从访客权限升级为正常权限）
                    if _plugin_instance.get_config("force_bind_qq", True):
                        _plugin_instance.server.scheduler.run_task(
                            _plugin_instance,
                            lambda: _plugin_instance.restore_player_permissions(online_player),
                            delay=5  # 短暂延迟确保绑定数据已保存
                        )
                    
                    reply = f"🎉 绑定成功！\n玩家 {player_name} 已与您的QQ绑定"
                else:
                    # 玩家不在线，从待验证数据中获取信息并完成绑定
                    if player_name in _pending_verifications:
                        pending_info = _pending_verifications[player_name]
                        # 使用临时XUID（玩家下次上线时会更新）
                        temp_xuid = f"temp_{int(time.time())}"
                        _plugin_instance.bind_player_qq(player_name, temp_xuid, user_id)
                        
                        # 设置QQ群昵称为玩家名（仅在启用QQ绑定且同步群昵称时）
                        if (_current_ws and 
                            _plugin_instance.get_config("force_bind_qq", True) and 
                            _plugin_instance.get_config("sync_group_card", True)):
                            target_group = _plugin_instance.get_config("target_group")
                            asyncio.run_coroutine_threadsafe(
                                set_group_card(_current_ws, group_id=target_group, user_id=int(user_id), card=player_name),
                                _plugin_instance._loop
                            )
                        
                        reply = f"🎉 绑定成功！\n玩家 {player_name} 已与您的QQ绑定\n下次登录游戏时将自动更新绑定信息"
                    else:
                        reply = f"验证码正确！但无法找到玩家 {player_name} 的绑定信息\n请重新在游戏中申请绑定"
                
                # 清理验证数据
                del _verification_codes[user_id]
                if player_name in _pending_verifications:
                    del _pending_verifications[player_name]
                
                # 通知QQ群
                if _current_ws and reply.startswith("🎉"):
                    asyncio.run_coroutine_threadsafe(
                        send_group_msg(_current_ws, group_id=group_id, text=f"🎮 玩家 {player_name} 已完成QQ绑定"),
                        _plugin_instance._loop
                    )
            else:
                # 验证失败，记录冷却时间（多玩家优化）
                _plugin_instance._binding_rate_limit[user_id] = time.time()
                reply = "验证码错误，请检查后重试"
        else:
            # 未找到验证码，记录冷却时间（多玩家优化）
            _plugin_instance._binding_rate_limit[user_id] = time.time()
            reply = "未找到您的验证码，请先在游戏中申请绑定"

    elif cmd == "verify" and len(args) == 0:
        reply = "用法：/verify <验证码> - 验证QQ绑定"

    elif cmd == "unbindqq" and len(args) == 1:
        # 解绑玩家QQ（仅管理员）
        admins = _plugin_instance.get_config("admins", [])
        if not admins or str(user_id) in admins:
            target_input = args[0]
            target_player = None
            original_qq = None
            
            # 判断输入是QQ号还是玩家名
            if target_input.isdigit():
                # 输入的是QQ号，查找对应的玩家
                target_player = _plugin_instance.get_qq_player(target_input)
                original_qq = target_input
                if not target_player:
                    reply = f"❌ 未找到绑定QQ号 {target_input} 的玩家"
                else:
                    # 找到了玩家，进行解绑
                    pass
            else:
                # 输入的是玩家名
                target_player = target_input
                original_qq = _plugin_instance.get_player_qq(target_player)
                if not original_qq:
                    reply = f"[失败] 玩家 {target_player} 未绑定QQ或不存在"
            
            # 如果找到了有效的玩家和QQ，执行解绑
            if target_player and original_qq and target_player in _plugin_instance._binding_data:
                # 使用专门的解绑方法
                success = _plugin_instance.unbind_player_qq(target_player, f"QQ管理员({user_id})")
                
                if success:
                    # 查找在线玩家并设置为访客权限
                    for player in _plugin_instance.server.online_players:
                        if player.name == target_player:
                            _plugin_instance.logger.info(f"管理员解绑玩家 {target_player} (QQ: {original_qq}) 的绑定，设置为访客权限")
                            
                            # 在主线程中设置访客权限
                            _plugin_instance.server.scheduler.run_task(
                                _plugin_instance,
                                lambda p=player: _plugin_instance.set_player_visitor_permissions(p),
                                delay=1
                            )
                            
                            # 通知玩家
                            _plugin_instance.server.scheduler.run_task(
                                _plugin_instance,
                                lambda p=player: p.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}[警告] 您的QQ绑定已被管理员解除{ColorFormat.RESET}"),
                                delay=5
                            )
                            _plugin_instance.server.scheduler.run_task(
                                _plugin_instance,
                                lambda p=player: p.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}已切换为访客权限{ColorFormat.RESET}"),
                                delay=10
                            )
                            _plugin_instance.server.scheduler.run_task(
                                _plugin_instance,
                                lambda p=player: p.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 重新绑定QQ{ColorFormat.RESET}"),
                                delay=15
                            )
                            break
                    
                    reply = f"[成功] 已解绑玩家 {target_player} 的QQ绑定 (原QQ: {original_qq})，游戏数据已保留"
                else:
                    reply = f"[失败] 解绑失败：操作过程中出现错误"
            elif not reply:  # 如果还没有设置错误消息
                reply = f"[失败] 解绑失败：未找到有效的绑定记录"
        else:
            reply = "该命令仅限管理员使用"

    elif cmd == "unbindqq" and len(args) == 0:
        reply = "用法：/unbindqq <玩家名|QQ号> - 解绑玩家的QQ绑定（仅管理员）"

    elif cmd == "ban" and len(args) >= 1:
        # 封禁玩家（仅管理员）
        admins = _plugin_instance.get_config("admins", [])
        if not admins or str(user_id) in admins:
            # 检查是否启用强制绑定QQ
            if not _plugin_instance.get_config("force_bind_qq", True):
                reply = "[失败] 封禁功能已禁用：强制QQ绑定功能未启用"
            else:
                target_player = args[0]
                ban_reason = " ".join(args[1:]) if len(args) > 1 else "管理员封禁"
                
                # 检查玩家是否已经被封禁
                if _plugin_instance.is_player_banned(target_player):
                    reply = f"[失败] 玩家 {target_player} 已经被封禁"
                else:
                    # 执行封禁
                    success = _plugin_instance.ban_player(target_player, f"QQ管理员({user_id})", ban_reason)
                
                if success:
                    # 查找在线玩家并踢出或设置访客权限
                    for player in _plugin_instance.server.online_players:
                        if player.name == target_player:
                            # 通知玩家
                            _plugin_instance.server.scheduler.run_task(
                                _plugin_instance,
                                lambda p=player: p.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}[封禁] 您已被封禁，禁止绑定QQ{ColorFormat.RESET}"),
                                delay=5
                            )
                            _plugin_instance.server.scheduler.run_task(
                                _plugin_instance,
                                lambda p=player: p.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}封禁原因：{ban_reason}{ColorFormat.RESET}"),
                                delay=10
                            )
                            _plugin_instance.server.scheduler.run_task(
                                _plugin_instance,
                                lambda p=player: p.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}"),
                                delay=15
                            )
                            
                            # 设置为访客权限
                            _plugin_instance.server.scheduler.run_task(
                                _plugin_instance,
                                lambda p=player: _plugin_instance.set_player_visitor_permissions(p),
                                delay=1
                            )
                            break
                    
                    reply = f"✅ 已封禁玩家 {target_player}\n原因：{ban_reason}"
                else:
                    reply = f"❌ 封禁失败：操作过程中出现错误"
        else:
            reply = "该命令仅限管理员使用"

    elif cmd == "ban" and len(args) == 0:
        # 检查是否启用强制绑定QQ
        if not _plugin_instance.get_config("force_bind_qq", True):
            reply = "❌ 封禁功能已禁用：强制QQ绑定功能未启用"
        else:
            reply = "用法：/ban <玩家名> [原因] - 封禁玩家，禁止QQ绑定（仅管理员）"

    elif cmd == "unban" and len(args) == 1:
        # 解封玩家（仅管理员）
        admins = _plugin_instance.get_config("admins", [])
        if not admins or str(user_id) in admins:
            # 检查是否启用强制绑定QQ
            if not _plugin_instance.get_config("force_bind_qq", True):
                reply = "❌ 解封功能已禁用：强制QQ绑定功能未启用"
            else:
                target_player = args[0]
                
                # 检查玩家是否被封禁
                if not _plugin_instance.is_player_banned(target_player):
                    reply = f"❌ 玩家 {target_player} 没有被封禁或不存在"
                else:
                    # 执行解封
                    success = _plugin_instance.unban_player(target_player, f"QQ管理员({user_id})")
                
                if success:
                    # 查找在线玩家并通知
                    for player in _plugin_instance.server.online_players:
                        if player.name == target_player:
                            _plugin_instance.server.scheduler.run_task(
                                _plugin_instance,
                                lambda p=player: p.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}✅ 您已被解封，现在可以绑定QQ{ColorFormat.RESET}"),
                                delay=5
                            )
                            _plugin_instance.server.scheduler.run_task(
                                _plugin_instance,
                                lambda p=player: p.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}使用命令 /bindqq 开始绑定流程{ColorFormat.RESET}"),
                                delay=10
                            )
                            break
                    
                    reply = f"✅ 已解封玩家 {target_player}"
                else:
                    reply = f"❌ 解封失败：操作过程中出现错误"
        else:
            reply = "该命令仅限管理员使用"

    elif cmd == "unban" and len(args) == 0:
        # 检查是否启用强制绑定QQ
        if not _plugin_instance.get_config("force_bind_qq", True):
            reply = "❌ 解封功能已禁用：强制QQ绑定功能未启用"
        else:
            reply = "用法：/unban <玩家名> - 解封玩家（仅管理员）"

    elif cmd == "banlist" and len(cmd_parts) == 1:
        # 查看封禁列表（仅管理员）
        admins = _plugin_instance.get_config("admins", [])
        if not admins or str(user_id) in admins:
            # 检查是否启用强制绑定QQ
            if not _plugin_instance.get_config("force_bind_qq", True):
                reply = "❌ 封禁列表功能已禁用：强制QQ绑定功能未启用"
            else:
                banned_players = _plugin_instance.get_banned_players()
                
                if not banned_players:
                    reply = "📋 当前没有被封禁的玩家"
                else:
                    reply_parts = [f"📋 封禁列表 ({len(banned_players)}个玩家)："]
                    
                    for ban_info in banned_players:
                        player_name = ban_info["name"]
                        ban_time = format_timestamp(ban_info["ban_time"])
                        ban_by = ban_info["ban_by"]
                        ban_reason = ban_info["ban_reason"]
                    
                    reply_parts.append(f"• {player_name}")
                    reply_parts.append(f"  时间: {ban_time}")
                    reply_parts.append(f"  操作者: {ban_by}")
                    reply_parts.append(f"  原因: {ban_reason}")
                
                reply = "\n".join(reply_parts)
        else:
            reply = "该命令仅限管理员使用"

    else:
        reply = f"未知命令 /{cmd}，输入 /help 查看可用命令"

    if reply:
        await send_group_msg(ws, group_id=group_id, text=reply)

# ========= 连接逻辑 =========
async def connect_forever():
    global _current_ws
    
    # 确保插件实例已初始化
    if not _plugin_instance:
        # 如果插件实例未初始化，暂时打印到控制台
        print("插件实例未初始化，无法获取配置")
        return
        
    access_token = _plugin_instance.get_config("access_token", "")
    napcat_ws = _plugin_instance.get_config("napcat_ws", "ws://localhost:3001")
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    delay = 1
    startup_msg = f"{ColorFormat.BLUE}QQsync 启动，准备连接 {ColorFormat.YELLOW}NapCat WS{ColorFormat.BLUE}…{ColorFormat.RESET}"
    _plugin_instance.logger.info(startup_msg)
    connect_msg = f"{ColorFormat.GOLD}连接地址: {ColorFormat.WHITE}{napcat_ws}{ColorFormat.RESET}"
    _plugin_instance.logger.info(connect_msg)
    
    while True:
        try:
            async with websockets.connect(napcat_ws, additional_headers=headers) as ws:
                _current_ws = ws  # 设置全局websocket变量
                success_msg = f"{ColorFormat.GREEN}✅ 已连接 {ColorFormat.YELLOW}NapCat WS{ColorFormat.RESET}"
                _plugin_instance.logger.info(success_msg)
                
                # 连接成功后立即获取群成员列表
                await get_group_member_list(ws, _plugin_instance.get_config("target_group"))
                
                await asyncio.gather(
                    heartbeat(ws),
                    message_loop(ws)
                )
        except Exception as e:
            _current_ws = None  # 连接断开时清空
            error_msg = f"{ColorFormat.RED}❌ 连接断开：{ColorFormat.YELLOW}{e}{ColorFormat.RED}，{ColorFormat.WHITE}{delay}s {ColorFormat.RED}后重连{ColorFormat.RESET}"
            _plugin_instance.logger.error(error_msg)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
        else:
            delay = 1

async def heartbeat(ws):
    while True:
        await ws.send("{}")
        await asyncio.sleep(30)

async def handle_api_response(data: dict):
    """处理API响应"""
    try:
        # 处理群成员列表响应
        if data.get("data") and isinstance(data["data"], list):
            # 假设这是群成员列表响应
            member_list = data["data"]
            if _plugin_instance and hasattr(_plugin_instance, '_group_members'):
                # 记录更新前的成员数量
                old_count = len(_plugin_instance._group_members)
                
                # 更新群成员缓存
                _plugin_instance._group_members.clear()
                for member in member_list:
                    if isinstance(member, dict) and "user_id" in member:
                        _plugin_instance._group_members.add(str(member["user_id"]))
                
                new_count = len(_plugin_instance._group_members)
                
                # 只在成员数量发生变化时才显示日志
                if old_count != new_count:
                    if old_count == 0:
                        # 首次获取群成员列表
                        _plugin_instance.logger.info(f"已获取群成员缓存，共 {new_count} 人")
                    else:
                        # 成员数量发生变化
                        change = new_count - old_count
                        if change > 0:
                            _plugin_instance.logger.info(f"群成员缓存已更新，新增 {change} 人（总计 {new_count} 人）")
                        else:
                            _plugin_instance.logger.info(f"群成员缓存已更新，减少 {abs(change)} 人（总计 {new_count} 人）")
                else:
                    # 成员数量未变化，使用debug级别日志
                    _plugin_instance.logger.debug(f"群成员缓存已刷新，成员数量无变化（共 {new_count} 人）")
        
        # 处理用户信息响应（get_stranger_info）
        elif (data.get("data") and isinstance(data["data"], dict) and 
              "user_id" in data["data"] and "nickname" in data["data"]):
            user_info = data["data"]
            user_id = str(user_info["user_id"])
            nickname = user_info["nickname"]
            
            # 更新待确认信息中的昵称
            if _plugin_instance and hasattr(_plugin_instance, '_pending_qq_confirmations'):
                for player_name, qq_info in _plugin_instance._pending_qq_confirmations.items():
                    if qq_info["qq"] == user_id:
                        qq_info["nickname"] = nickname
                        _plugin_instance.logger.debug(f"已更新QQ {user_id} 的昵称: {nickname}")
                        break
                
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"处理API响应失败: {e}")

async def handle_group_member_change(data: dict):
    """处理群成员变动事件"""
    try:
        if not _plugin_instance:
            return
        
        notice_type = data.get("notice_type")
        group_id = data.get("group_id")
        user_id = str(data.get("user_id", ""))
        target_group = _plugin_instance.get_config("target_group")
        
        # 只处理目标群的事件
        if group_id != target_group:
            return
        
        if notice_type == "group_increase":
            # 有人加入群聊
            if hasattr(_plugin_instance, '_group_members'):
                _plugin_instance._group_members.add(user_id)
            
            # 清理已退群玩家的日志缓存（如果重新加群）
            if hasattr(_plugin_instance, '_logged_left_players') and user_id in _plugin_instance._logged_left_players:
                _plugin_instance._logged_left_players.discard(user_id)
            
            # 检查是否有已绑定的玩家重新加入群聊
            bound_player = _plugin_instance.get_qq_player(user_id)
            if bound_player:
                # 找到对应的在线玩家并恢复权限
                for player in _plugin_instance.server.online_players:
                    if player.name == bound_player:
                        _plugin_instance.logger.info(f"玩家 {bound_player} (QQ: {user_id}) 重新加入群聊，恢复正常权限")
                        
                        # 在主线程中恢复权限
                        _plugin_instance.server.scheduler.run_task(
                            _plugin_instance,
                            lambda p=player: _plugin_instance.restore_player_permissions(p),
                            delay=1
                        )
                        
                        # 通知玩家（游戏内消息）
                        _plugin_instance.server.scheduler.run_task(
                            _plugin_instance,
                            lambda p=player: p.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}✅ 欢迎回到QQ群！已恢复完整游戏权限{ColorFormat.RESET}"),
                            delay=5
                        )
                        
                        # 通知QQ群（群内消息）
                        if _current_ws:
                            asyncio.run_coroutine_threadsafe(
                                send_group_msg(_current_ws, group_id=target_group, text=f"🎮 玩家 {bound_player} 重新加入群聊，已自动恢复游戏权限"),
                                _plugin_instance._loop
                            )
                        break
        
        elif notice_type == "group_decrease":
            # 有人离开群聊
            if hasattr(_plugin_instance, '_group_members'):
                _plugin_instance._group_members.discard(user_id)
            
            # 检查是否有已绑定的玩家离开群聊
            bound_player = _plugin_instance.get_qq_player(user_id)
            if bound_player:
                # 找到对应的在线玩家并设置为访客权限
                for player in _plugin_instance.server.online_players:
                    if player.name == bound_player:
                        _plugin_instance.logger.info(f"玩家 {bound_player} (QQ: {user_id}) 离开群聊，设置为访客权限")
                        
                        # 在主线程中设置访客权限
                        _plugin_instance.server.scheduler.run_task(
                            _plugin_instance,
                            lambda p=player: _plugin_instance._handle_player_left_group(p),
                            delay=1
                        )
                        
                        # 通知QQ群（群内消息）
                        if _current_ws:
                            asyncio.run_coroutine_threadsafe(
                                send_group_msg(_current_ws, group_id=target_group, text=f"⚠️ 玩家 {bound_player} 已离开群聊，已自动设为访客权限"),
                                _plugin_instance._loop
                            )
                        break
                
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"处理群成员变动事件失败: {e}")

async def message_loop(ws):
    async for raw in ws:
        try:
            data = json.loads(raw)
            
            # 处理API响应（群成员列表等）
            if "echo" in data and data.get("status") == "ok":
                await handle_api_response(data)
                continue
            
            # 处理群成员变动事件
            if data.get("post_type") == "notice":
                if data.get("notice_type") in ["group_increase", "group_decrease"]:
                    await handle_group_member_change(data)
                    continue
            
            # 处理消息事件
            if data.get("post_type") == "message":
                if data.get("message_type") == "group":
                    group_id = data.get("group_id")
                    target_group = _plugin_instance.get_config("target_group") if _plugin_instance else None
                    if group_id == target_group:
                        await handle_message(ws, data)
        except json.JSONDecodeError:
            continue
        except Exception as e:
            if _plugin_instance:
                error_msg = f"{ColorFormat.RED}处理消息失败: {ColorFormat.YELLOW}{e}{ColorFormat.RESET}"
                _plugin_instance.logger.error(error_msg)