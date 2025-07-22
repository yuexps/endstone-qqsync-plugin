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
    MessageForm,
    Label,
    TextInput,
    Header,
    Divider,
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
_verification_messages = {}  # 存储验证码消息ID: {qq_number: {"message_id": id, "timestamp": time, "player_name": name}}
_player_bind_attempts = {}   # 记录玩家绑定尝试时间: {player_name: timestamp} 用于60秒冷却

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
        
        # 首先初始化所有属性
        # 初始化权限附件存储
        self._player_attachments = {}
        
        # 初始化群成员缓存
        self._group_members = set()  # 存储群成员QQ号的集合
        
        # 初始化日志缓存（避免重复日志输出）
        self._logged_left_players = set()  # 存储已记录退群日志的QQ号
        
        # 初始化文件写入
        self._pending_data_save = False  # 是否有待保存的数据
        self._last_save_time = 0  # 上次保存时间
        self._save_interval = 0.02  # 保存间隔（20毫秒）
        self._force_save_interval = 0.1  # 强制保存间隔（100毫秒）
        
        # 实时保存
        self._auto_save_enabled = True  # 是否启用自动保存
        self._save_on_change = True  # 数据变更时立即保存
        self._max_pending_changes = 5  # 最大待保存变更数量
        self._pending_changes_count = 0  # 当前待保存变更计数
        
        # 然后初始化配置和数据（这些可能会调用 _trigger_realtime_save）
        self._init_config()
        self._init_bindqq_data()
        
        # 初始化多玩家处理
        self._verification_queue = {}  # 验证码发送队列：{qq: 发送时间}
        self._binding_rate_limit = {}  # 绑定频率限制：{qq: 上次绑定时间}
        self._form_display_cache = {}  # 表单显示缓存：{player_name: 显示时间}
        self._form_display_count = {}  # 表单显示次数计数：{player_name: 显示次数}
        self._pending_qq_confirmations = {}  # 待确认的QQ信息：{player_name: {qq: str, nickname: str, timestamp: float}}
        self._concurrent_bindings = set()  # 当前正在进行绑定的玩家
        self._max_concurrent_bindings = 25  # 最大并发绑定数量
        self._verification_rate_limit = 30  # 每分钟最多发送验证码数量（提高到30个）
        self._binding_cooldown = 10  # 绑定失败后的冷却时间（秒）（减少到10秒）
        
        # 队列管理
        self._binding_queue = []  # 绑定队列：[(player_name, qq_number, request_time)]
        self._queue_notification_sent = set()  # 已发送排队通知的玩家
        
        # 多人验证码发送
        self._verification_send_queue = []  # 验证码发送队列：[(player, qq_number, verification_code, attempt, timestamp)]
        self._verification_retry_count = {}  # 验证码重试计数：{qq_number: retry_count}
        self._max_verification_retries = 3  # 最大重试次数
        self._verification_send_interval = 2  # 验证码发送间隔（秒）
        self._last_verification_send_time = 0  # 上次验证码发送时间

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

        # 启动数据一致性检查任务
        if hasattr(self, '_loop'):
            # 创建定期数据一致性检查任务（每30秒）
            self._loop.call_later(30, self._schedule_consistency_check)

        # 启动定时清理任务
        self.server.scheduler.run_task(
            self,
            self._schedule_cleanup,
            delay=1200,  # 首次执行延迟1分钟
            period=1200  # 每1分钟执行一次
        )
        
        # 启动群成员检查任务（等待WebSocket连接建立后开始，每10分钟执行一次）
        self.server.scheduler.run_task(
            self,
            self._schedule_group_check,
            delay=300,   # 首次执行延迟15秒，确保WebSocket连接已建立
            period=12000  # 每10分钟执行一次
        )

        # 启动周期性清理任务
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
        
        # 启动验证码发送队列处理任务
        self.server.scheduler.run_task(
            self,
            self._process_verification_send_queue,
            delay=3,     # 3秒后首次执行
            period=40    # 每2秒检查一次验证码发送队列
        )
        
        # 启动验证码专用清理任务（高频清理确保在QQ 2分钟撤回限制内及时处理）
        self.server.scheduler.run_task(
            self,
            self.cleanup_expired_verifications,
            delay=100,   # 5秒后首次执行
            period=100   # 每5秒检查一次过期验证码（更高频率确保及时撤回）
        )

        startup_msg = f"{ColorFormat.GREEN}qqsync_plugin {ColorFormat.YELLOW}已启用{ColorFormat.RESET}"
        self.logger.info(startup_msg)
        welcome_msg = f"{ColorFormat.BLUE}欢迎使用QQsync群服互通插件，{ColorFormat.YELLOW}作者：yuexps{ColorFormat.RESET}"
        self.logger.info(welcome_msg)

    def _schedule_consistency_check(self):
        """安排数据一致性检查任务"""
        try:
            # 创建一致性检查任务
            asyncio.run_coroutine_threadsafe(_verify_data_consistency(), self._loop)
            
            # 安排下一次检查（30秒后）
            self._loop.call_later(30, self._schedule_consistency_check)
        except Exception as e:
            self.logger.error(f"安排数据一致性检查失败: {e}")
            # 尝试5分钟后重新安排
            self._loop.call_later(300, self._schedule_consistency_check)   
    
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
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}")
            elif visitor_reason == "未绑定QQ":
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}[警告] 您当前为访客权限（原因：未绑定QQ）{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}以下功能受限：{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法聊天{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法破坏/放置方块{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法与容器/机器交互{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法攻击生物或实体{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}解决方案：使用命令 /bindqq 绑定QQ{ColorFormat.RESET}")
            elif visitor_reason == "已退出QQ群":
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}[警告] 您当前为访客权限（原因：已退出QQ群）{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}以下功能受限：{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法聊天{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法破坏/放置方块{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法与容器/机器交互{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}• 无法攻击生物或实体{ColorFormat.RESET}")
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
            
            # 清理验证码发送队列
            if hasattr(self, '_verification_send_queue'):
                original_verification_queue_length = len(self._verification_send_queue)
                self._verification_send_queue = [(p, q, c, a, t) for p, q, c, a, t in self._verification_send_queue if p.name != player_name]
                if len(self._verification_send_queue) < original_verification_queue_length:
                    cache_cleaned.append("验证码发送队列")
            
            # 清理验证码重试计数
            if hasattr(self, '_verification_retry_count'):
                # 查找该玩家相关的QQ号
                player_qq = self.get_player_qq(player_name)
                if player_qq and player_qq in self._verification_retry_count:
                    del self._verification_retry_count[player_qq]
                    cache_cleaned.append("验证码重试计数")
            
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
            
        if self._is_websocket_connected():
            target_group = self.get_config("target_group")
            asyncio.run_coroutine_threadsafe(
                get_group_member_list(_current_ws, target_group),
                self._loop
            )
        else:
            # 使用新的状态消息方法
            status_msg = self._get_websocket_status_message("群成员缓存更新")
            if status_msg:
                # 只在首次启动时使用debug级别，运行中断开时使用warning级别
                if not hasattr(self, '_group_members') or len(self._group_members) == 0:
                    self.logger.debug(status_msg)
                else:
                    self.logger.warning(status_msg)
            # 如果WebSocket断开，暂时不进行退群检测
            # 避免因网络问题误判退群
    
    def _is_websocket_connected(self) -> bool:
        """检查WebSocket是否已连接"""
        return _current_ws is not None
    
    def _get_websocket_status_message(self, operation: str = "操作") -> str:
        """获取WebSocket状态相关的用户友好消息"""
        if self._is_websocket_connected():
            return ""
        
        # 检查是否是首次启动阶段
        if not hasattr(self, '_group_members') or len(self._group_members) == 0:
            return f"系统正在连接QQ服务，{operation}稍后将自动执行"
        else:
            return f"QQ服务连接断开，{operation}暂时不可用"
    
    def check_qq_in_group_immediate(self, qq_number: str) -> bool:
        """立即检查QQ号是否在群内（通过API实时查询）"""
        # 只有在启用强制绑定和退群检测时才进行检查
        if not (self.get_config("force_bind_qq", True) and 
                self.get_config("check_group_member", True)):
            return True  # 如果未启用相关功能，默认允许
            
        if not self._is_websocket_connected():
            status_msg = self._get_websocket_status_message("群成员检查")
            if status_msg:
                self.logger.debug(f"QQ {qq_number} 群成员检查跳过: {status_msg}")
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
        # 防御性检查：确保自动保存属性已初始化
        if not hasattr(self, '_auto_save_enabled') or not self._auto_save_enabled:
            # 如果自动保存未启用或属性未初始化，直接保存（兼容初始化阶段）
            self.save_binding_data()
            self.logger.debug(f"数据保存 (直接模式): {reason}")
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
                "xuid": "",
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
            # 根据是否启用强制绑定显示不同的内容
            if self.get_config("force_bind_qq", True):
                controls = [
                    Divider(),
                    Label("为了更好的游戏体验，请绑定您的QQ号"),
                    Label("绑定后可以与QQ群聊天互通"),
                    Label("享受群服一体化体验"),
                    Divider(),
                ]
            else:
                controls = [
                    Header("QQ群服互通 - 可选绑定"),
                    Divider(),
                    Label("绑定QQ号后可享受群服互通功能"),
                    Label("您也可以选择不绑定，不影响正常游戏"),
                    Divider(),
                ]
            
            # 添加输入框
            controls.append(
                TextInput(
                    label="请输入您的QQ号",
                    placeholder="例如: 2899659758 (5-11位数字)",
                    default_value=""
                )
            )
            
            form = ModalForm(
                title="QQsync群服互通 - 身份验证",
                controls=controls,
                submit_button="下一步",
                icon="textures/ui/icon_multiplayer"
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
            # 添加调试信息
            self.logger.debug(f"收到表单数据: {form_data}")
            
            try:
                # 尝试解析JSON格式
                data_list = json.loads(form_data)
                self.logger.debug(f"解析后的数据列表长度: {len(data_list)}, 内容: {data_list}")
                # TextInput总是在表单控件的最后位置，从末尾获取
                qq_input = data_list[-1] if len(data_list) > 0 else ""
            except (json.JSONDecodeError, IndexError):
                # 如果不是JSON，按逗号分割或其他方式解析
                data_parts = form_data.split(',') if form_data else []
                self.logger.debug(f"按逗号分割的数据长度: {len(data_parts)}, 内容: {data_parts}")
                qq_input = data_parts[-1].strip() if len(data_parts) > 0 else ""
            
            self.logger.debug(f"提取的QQ号: '{qq_input}'")
            
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
            
            # 临时存储待确认的QQ信息
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
            # 格式化显示内容
            content = f"""请确认以下QQ账号信息是否正确：

QQ号: {qq_number}
昵称: {nickname}"""
            
            form = MessageForm(
                title="确认QQ账号信息",
                content=content,
                button1="确认绑定",
                button2="重新输入"
            )
            
            # MessageForm 的 on_submit 回调函数接收按钮索引：0=button1, 1=button2
            def handle_submit(player, button_index):
                if button_index == 0:  # 确认绑定
                    self._handle_qq_confirmation(player, True, qq_number, nickname)
                else:  # 重新输入
                    self._handle_qq_confirmation(player, False, qq_number, nickname)
            
            form.on_submit = handle_submit
            form.on_close = lambda player: self._handle_qq_confirmation(player, False, qq_number, nickname)
            
            player.send_form(form)
            
        except Exception as e:
            self.logger.error(f"显示QQ确认表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}确认表单加载失败，请重试！{ColorFormat.RESET}")
    
    def _handle_qq_confirmation(self, player, confirmed, qq_number=None, nickname=None):
        """处理QQ信息确认结果"""
        global _pending_verifications, _verification_codes, _player_bind_attempts
        
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
                # 用户取消了绑定 - 记录绑定尝试时间用于60秒冷却
                current_time = time.time()
                _player_bind_attempts[player.name] = current_time
                self.logger.info(f"玩家 {player.name} 取消QQ绑定，已记录冷却时间")
                
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}QQ绑定已取消{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您可以使用命令 /bindqq 重新开始绑定{ColorFormat.RESET}")
                return
            
            if not qq_number:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}确认信息已过期，请重新开始绑定{ColorFormat.RESET}")
                return
            
            # 用户确认了QQ信息，开始验证码流程
            # 必须在任何清理操作之前检查60秒冷却限制
            current_time = time.time()
            if player.name in _pending_verifications:
                last_request_time = _pending_verifications[player.name].get("timestamp", 0)
                cooldown_remaining = 60 - (current_time - last_request_time)
                if cooldown_remaining > 0:
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}您在60秒内只能申请一个验证码，请等待{int(cooldown_remaining)}秒后再次尝试{ColorFormat.RESET}")
                    self.logger.info(f"玩家 {player.name} 验证码申请被拒绝：60秒冷却中，剩余{int(cooldown_remaining)}秒")
                    return
                else:
                    # 60秒已过，可以继续处理，清理旧验证码
                    self.logger.info(f"玩家 {player.name} 的验证码冷却已过（超过60秒），允许生成新验证码")
                    self._cleanup_old_verification(player.name)
            
            # 先立即执行一次全局清理，确保没有残留的旧验证码
            self.cleanup_expired_verifications()
            
            # 清理与该QQ号相关的所有旧验证码（防止QQ号被其他玩家使用过）
            self._cleanup_qq_old_verifications(qq_number)
            
            # 注册验证码发送尝试
            self._register_verification_attempt(qq_number, player.name)
            
            # 记录绑定尝试时间（用于60秒冷却）
            _player_bind_attempts[player.name] = current_time
            self.logger.info(f"玩家 {player.name} 开始验证码生成流程，已记录冷却时间")
            
            # 生成验证码
            verification_code = str(random.randint(100000, 999999))
            
            # 控制台显示验证码（管理员调试用）
            console_msg = f"{ColorFormat.AQUA}[验证码] 玩家: {ColorFormat.WHITE}{player.name}{ColorFormat.AQUA} | QQ: {ColorFormat.WHITE}{qq_number}{ColorFormat.AQUA} | 验证码: {ColorFormat.YELLOW}{verification_code}{ColorFormat.RESET}"
            self.logger.info(console_msg)
            
            # 存储待验证信息（包含验证码创建时间和玩家XUID）
            creation_time = datetime.datetime.now()
            _pending_verifications[player.name] = {
                "qq": qq_number,
                "code": verification_code,
                "timestamp": time.time(),
                "creation_time": creation_time,  # 精确时间记录
                "player_xuid": player.xuid  # 玩家XUID用于安全验证
            }
            
            _verification_codes[qq_number] = {
                "code": verification_code,
                "timestamp": time.time(),
                "creation_time": creation_time,  # 精确时间记录
                "player_name": player.name
            }
            
            # 发送验证码到QQ
            if _current_ws:
                try:
                    # 计算当前系统负载信息
                    current_concurrent = len(self._concurrent_bindings)
                    queue_length = len(self._binding_queue)
                    
                    # 将验证码发送添加到队列，而不是直接发送
                    self._verification_send_queue.append((
                        player, 
                        qq_number, 
                        verification_code, 
                        1,  # 初始尝试次数
                        time.time()
                    ))
                    
                    # 通知玩家验证码发送中
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}正在发送验证码到群组@您...{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}当前绑定队列：{len(self._concurrent_bindings)}/{self._max_concurrent_bindings}{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}验证码发送队列：{len(self._verification_send_queue)}个等待中{ColorFormat.RESET}")
                    
                    # 立即尝试处理验证码发送队列
                    self.server.scheduler.run_task(
                        self,
                        self._process_verification_send_queue,
                        delay=1
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
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}服务器未连接到QQ群，无法发送验证码！{ColorFormat.RESET}")
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
                    Divider(),
                    Label("验证码已通过群组@消息发送！"),
                    Label("请查看QQ群消息并输入收到的验证码"),
                    Label("验证码60秒内有效"),
                    Divider(),
                    TextInput(
                        label="请输入验证码",
                        placeholder="6位数字验证码",
                        default_value=""
                    )
                ],
                submit_button="确认绑定",
                icon="textures/ui/icon_book_writable"
            )
            
            form.on_submit = lambda player, form_data: self._handle_verification_submit(player, form_data)
            form.on_close = lambda player: self._handle_verification_close(player)
            
            player.send_form(form)
            
        except Exception as e:
            self.logger.error(f"显示验证码表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证表单加载失败！{ColorFormat.RESET}")
    
    def _handle_verification_close(self, player):
        """处理验证表单关闭"""
        global _pending_verifications, _verification_codes, _player_bind_attempts
        
        # 记录绑定尝试时间用于60秒冷却（即使用户取消了验证）
        current_time = time.time()
        _player_bind_attempts[player.name] = current_time
        self.logger.info(f"玩家 {player.name} 关闭验证码表单，已记录冷却时间")
        
        # 保留所有验证数据，让玩家可以在QQ中继续完成验证
        # 注意：不删除 _pending_verifications 数据，因为QQ验证需要这个数据进行安全检查
        if player.name in _pending_verifications:
            self.logger.info(f"玩家 {player.name} 关闭游戏表单，验证数据已保留供QQ验证使用")
        
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}游戏内绑定已取消{ColorFormat.RESET}")
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您仍可在QQ群中输入验证码完成绑定{ColorFormat.RESET}")
    
    def _handle_verification_submit(self, player, form_data):
        """处理验证码提交"""
        global _pending_verifications, _verification_codes
        
        try:
            # 首先检查是否有待验证的信息
            if player.name not in _pending_verifications:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证信息已过期，请重新开始绑定{ColorFormat.RESET}")
                return
            
            # 安全检查：验证XUID是否匹配（防止不同玩家冒用验证码）
            pending_info = _pending_verifications[player.name]
            if pending_info.get("player_xuid") and pending_info.get("player_xuid") != player.xuid:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证失败：玩家身份不匹配，请重新开始绑定{ColorFormat.RESET}")
                self.logger.warning(f"游戏内验证安全检查失败: 玩家 {player.name} XUID不匹配 - 期望: {pending_info.get('player_xuid')}, 实际: {player.xuid}")
                # 清理验证数据
                del _pending_verifications[player.name]
                qq_number = pending_info.get("qq")
                if qq_number and qq_number in _verification_codes:
                    del _verification_codes[qq_number]
                return
            
            # 检查验证码是否过期（60秒有效期）
            current_time = time.time()
            if current_time - pending_info["timestamp"] > 60:  # 60秒
                # 清理过期的验证信息
                del _pending_verifications[player.name]
                qq_number = pending_info["qq"]
                if qq_number in _verification_codes:
                    del _verification_codes[qq_number]
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证码已过期，请重新开始绑定{ColorFormat.RESET}")
                return
            
            # 解析form_data获取验证码
            self.logger.debug(f"收到验证码表单数据: {form_data}")
            
            try:
                # 尝试解析JSON格式
                data_list = json.loads(form_data)
                self.logger.debug(f"验证码表单解析后的数据列表长度: {len(data_list)}, 内容: {data_list}")
                # TextInput总是在表单控件的最后位置，从末尾获取
                verification_input = data_list[-1] if len(data_list) > 0 else ""
            except (json.JSONDecodeError, IndexError):
                # 如果不是JSON，按逗号分割或其他方式解析
                data_parts = form_data.split(',') if form_data else []
                self.logger.debug(f"验证码表单按逗号分割的数据长度: {len(data_parts)}, 内容: {data_parts}")
                verification_input = data_parts[-1].strip() if len(data_parts) > 0 else ""
            
            self.logger.debug(f"提取的验证码: '{verification_input}'")
            
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
            
            # 验证验证码
            if verification_input == pending_info["code"]:
                # 检查验证码是否已被使用
                qq_number = pending_info["qq"]
                if qq_number in _verification_codes and _verification_codes[qq_number].get("used", False):
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证码已使用：该验证码已被使用过，请重新申请绑定{ColorFormat.RESET}")
                    self.logger.warning(f"游戏内验证失败: 验证码已被使用 (玩家: {player.name}, QQ: {qq_number})")
                    # 清理验证数据
                    del _pending_verifications[player.name]
                    if qq_number in _verification_codes:
                        del _verification_codes[qq_number]
                    return
                
                # 控制台显示游戏内验证成功信息
                game_verify_success_msg = f"{ColorFormat.GREEN}[游戏内验证成功] 玩家: {ColorFormat.WHITE}{player.name}{ColorFormat.GREEN} | QQ: {ColorFormat.WHITE}{pending_info['qq']}{ColorFormat.GREEN} | 验证码: {ColorFormat.YELLOW}{verification_input}{ColorFormat.GREEN} | 来源: 游戏表单{ColorFormat.RESET}"
                self.logger.info(game_verify_success_msg)
                
                # 标记验证码为已使用（防止重复使用）
                qq_number = pending_info["qq"]
                verification_already_used = False
                if qq_number in _verification_codes:
                    verification_already_used = _verification_codes[qq_number].get("used", False)
                    _verification_codes[qq_number]["used"] = True
                    _verification_codes[qq_number]["use_time"] = time.time()
                
                # 清理统一验证尝试计数器
                verification_key = f"unified_attempts_{player.name}_{pending_info['qq']}"
                if hasattr(self, '_unified_verification_attempts') and verification_key in self._unified_verification_attempts:
                    del self._unified_verification_attempts[verification_key]
                
                # 绑定成功
                self.bind_player_qq(player.name, player.xuid, pending_info["qq"])
                
                # 清理验证数据
                del _pending_verifications[player.name]
                if pending_info["qq"] in _verification_codes:
                    del _verification_codes[pending_info["qq"]]
                
                # 撤回验证码消息
                if _current_ws:
                    asyncio.run_coroutine_threadsafe(
                        delete_verification_message(pending_info["qq"]),
                        self._loop
                    )
                
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
                
                # 异步通知QQ群（不阻塞主流程）- 只有游戏内验证才发送群通知
                if _current_ws and not verification_already_used:
                    asyncio.run_coroutine_threadsafe(
                        send_group_msg(_current_ws, group_id=self.get_config("target_group"), text=f"🎉 玩家 {player.name} 已完成QQ绑定！"),
                        self._loop
                    )
            else:
                # 验证失败处理：统一管理所有验证尝试
                verification_key = f"unified_attempts_{player.name}_{pending_info['qq']}"
                current_attempts = getattr(self, '_unified_verification_attempts', {}).get(verification_key, 0) + 1
                
                # 初始化统一验证尝试计数器
                if not hasattr(self, '_unified_verification_attempts'):
                    self._unified_verification_attempts = {}
                self._unified_verification_attempts[verification_key] = current_attempts
                
                max_attempts = 3  # 游戏内和QQ总共3次尝试机会
                remaining_attempts = max_attempts - current_attempts
                
                if remaining_attempts > 0:
                    # 还有重试机会
                    # 控制台显示游戏内验证失败信息
                    game_verify_fail_msg = f"{ColorFormat.RED}[游戏内验证失败] 玩家: {ColorFormat.WHITE}{player.name}{ColorFormat.RED} | QQ: {ColorFormat.WHITE}{pending_info['qq']}{ColorFormat.RED} | 输入验证码: {ColorFormat.YELLOW}{verification_input}{ColorFormat.RED} | 正确验证码: {ColorFormat.YELLOW}{pending_info['code']}{ColorFormat.RED} | 剩余尝试: {ColorFormat.YELLOW}{remaining_attempts}{ColorFormat.RED} | 来源: 游戏表单{ColorFormat.RESET}"
                    self.logger.info(game_verify_fail_msg)
                    
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证码错误！还可以尝试 {remaining_attempts} 次{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请仔细检查验证码后重新输入{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GRAY}提示：也可以直接在QQ群发送验证码{ColorFormat.RESET}")
                    
                    # 重新显示验证表单
                    self.server.scheduler.run_task(
                        self,
                        lambda: self.show_verification_form(player),
                        delay=10
                    )
                else:
                    # 尝试次数用完，清理验证数据并触发冷却
                    self.logger.warning(f"统一验证尝试次数超限: 玩家 {player.name} (QQ: {pending_info['qq']}) 已尝试 {max_attempts} 次，清理验证数据并触发冷却")
                    
                    # 清理验证数据
                    del _pending_verifications[player.name]
                    qq_number = pending_info["qq"]
                    if qq_number in _verification_codes:
                        del _verification_codes[qq_number]
                    
                    # 撤回验证码消息
                    if _current_ws:
                        asyncio.run_coroutine_threadsafe(
                            delete_verification_message(qq_number),
                            self._loop
                        )
                    
                    # 清理统一尝试计数
                    if verification_key in self._unified_verification_attempts:
                        del self._unified_verification_attempts[verification_key]
                    
                    # 触发验证失败冷却（60秒）
                    if not hasattr(self, '_player_verification_cooldown'):
                        self._player_verification_cooldown = {}
                    self._player_verification_cooldown[player.name] = time.time()
                    
                    # 同时对QQ号也设置冷却
                    self._binding_rate_limit[qq_number] = time.time()
                    
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证码尝试次数已达上限（{max_attempts}次）{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请等待60秒后重新使用命令 /bindqq 申请新的验证码{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GRAY}或者等待当前验证码自动过期{ColorFormat.RESET}")
            
        except Exception as e:
            self.logger.error(f"处理验证码提交失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证过程出错，请重试！{ColorFormat.RESET}")
    
    def cleanup_expired_verifications(self):
        """清理过期的验证码并撤回消息（优化为90秒过期，确保在QQ 2分钟撤回限制内处理）"""
        global _pending_verifications, _verification_codes, _verification_messages
        
        current_time = time.time()
        expired_players = []
        expired_qq = []
        
        # 清理过期的待验证信息（90秒过期，为QQ撤回留出充足时间）
        for player_name, info in _pending_verifications.items():
            if current_time - info["timestamp"] > 90:
                expired_players.append(player_name)
        
        for player_name in expired_players:
            del _pending_verifications[player_name]
        
        # 清理过期的验证码并撤回消息（90秒过期，确保2分钟内撤回）
        for qq_number, info in _verification_codes.items():
            if current_time - info["timestamp"] > 90:
                expired_qq.append(qq_number)
        
        for qq_number in expired_qq:
            del _verification_codes[qq_number]
            # 异步撤回对应的验证码消息
            if _current_ws and qq_number in _verification_messages:
                asyncio.run_coroutine_threadsafe(
                    delete_verification_message(qq_number),
                    self._loop
                )
        
        # 清理对应的统一验证尝试计数器（修复：移到循环外部）
        for qq_number in expired_qq:
            # 清理所有与此QQ相关的统一验证计数器
            if hasattr(self, '_unified_verification_attempts'):
                keys_to_remove = [key for key in self._unified_verification_attempts.keys() 
                                if key.endswith(f"_{qq_number}")]
                for key in keys_to_remove:
                    del self._unified_verification_attempts[key]
        
        # 清理过期玩家的统一验证尝试计数器
        for player_name in expired_players:
            if hasattr(self, '_unified_verification_attempts'):
                keys_to_remove = [key for key in self._unified_verification_attempts.keys() 
                                if key.startswith(f"unified_attempts_{player_name}_")]
                for key in keys_to_remove:
                    del self._unified_verification_attempts[key]
        
        # 清理过期的验证码消息记录（90秒过期，确保及时撤回）
        expired_messages = [qq for qq, msg_info in _verification_messages.items() 
                           if current_time - msg_info["timestamp"] > 90]
        
        # 检查接近2分钟撤回限制的紧急消息（1分45秒 = 105秒）
        urgent_messages = [qq for qq, msg_info in _verification_messages.items() 
                          if current_time - msg_info["timestamp"] > 105]
        
        # 优先处理紧急消息
        for qq in urgent_messages:
            if _current_ws:
                asyncio.run_coroutine_threadsafe(
                    delete_verification_message(qq),
                    self._loop
                )
            else:
                # 如果没有WebSocket连接，只清理记录
                if qq in _verification_messages:
                    del _verification_messages[qq]
        
        # 处理常规过期消息
        for qq in expired_messages:
            # 如果消息记录过期但验证码还未过期，也需要撤回消息
            if qq not in expired_qq and _current_ws:
                asyncio.run_coroutine_threadsafe(
                    delete_verification_message(qq),
                    self._loop
                )
            elif qq not in expired_qq:
                # 如果没有WebSocket连接，只清理记录
                del _verification_messages[qq]
        
        if expired_players or expired_qq or expired_messages:
            urgent_count = len(urgent_messages)
            msg_parts = [f"{len(expired_players)} 个待验证玩家", f"{len(expired_qq)} 个验证码", f"{len(expired_messages)} 条消息记录"]
            if urgent_count > 0:
                msg_parts.append(f"其中 {urgent_count} 条紧急消息(接近2分钟限制)")
            self.logger.info(f"清理过期验证码: {', '.join(msg_parts)} (已撤回相关消息)")
        
        # 清理过期的多玩家处理缓存
        self._cleanup_expired_caches()
    
    def _cleanup_expired_caches(self):
        """清理过期的多玩家处理缓存"""
        global _player_bind_attempts
        current_time = time.time()
        
        # 清理玩家绑定尝试记录中的过期记录（65秒过期，比60秒冷却稍长一点）
        expired_attempts = [player_name for player_name, attempt_time in _player_bind_attempts.items() 
                           if current_time - attempt_time > 65]
        for player_name in expired_attempts:
            del _player_bind_attempts[player_name]
        if expired_attempts:
            self.logger.debug(f"清理{len(expired_attempts)}个过期的玩家绑定尝试记录")
        
        # 清理离线玩家的绑定尝试记录
        online_player_names = {player.name for player in self.server.online_players}
        offline_attempts = [player_name for player_name in _player_bind_attempts.keys() 
                           if player_name not in online_player_names]
        for player_name in offline_attempts:
            del _player_bind_attempts[player_name]
        if offline_attempts:
            self.logger.debug(f"清理{len(offline_attempts)}个离线玩家的绑定尝试记录")
        
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
        
        # 清理验证码发送队列中的过期记录（60秒过期）
        if hasattr(self, '_verification_send_queue'):
            original_length = len(self._verification_send_queue)
            self._verification_send_queue = [(p, q, c, a, t) for p, q, c, a, t in self._verification_send_queue 
                                           if current_time - t <= 60]
            if len(self._verification_send_queue) < original_length:
                removed_count = original_length - len(self._verification_send_queue)
                self.logger.debug(f"清理{removed_count}个过期的验证码发送任务")
        
        # 清理验证码重试计数中的过期记录
        if hasattr(self, '_verification_retry_count'):
            expired_retries = [qq for qq, count_info in self._verification_retry_count.items() 
                             if current_time - count_info.get("last_attempt", 0) > 300]  # 5分钟过期
            for qq in expired_retries:
                del self._verification_retry_count[qq]
        
        # 清理统一验证尝试计数中的过期记录（5分钟过期或玩家离线）
        if hasattr(self, '_unified_verification_attempts'):
            online_player_names = {player.name for player in self.server.online_players}
            expired_unified_attempts = []
            
            for key in self._unified_verification_attempts.keys():
                # 解析key: unified_attempts_{player_name}_{qq_number}
                if key.startswith("unified_attempts_"):
                    parts = key.split("_", 3)  # ['unified', 'attempts', player_name, qq_number]
                    if len(parts) >= 3:
                        player_name = parts[2]
                        # 如果玩家离线，清理其计数器
                        if player_name not in online_player_names:
                            expired_unified_attempts.append(key)
            
            for key in expired_unified_attempts:
                del self._unified_verification_attempts[key]
                
            if expired_unified_attempts:
                self.logger.debug(f"清理{len(expired_unified_attempts)}个离线玩家的统一验证计数器")
        
        # 清理离线的并发绑定玩家
        if hasattr(self, '_concurrent_bindings'):
            offline_concurrent_players = []
            for player_name in self._concurrent_bindings.copy():
                player_online = False
                for player in self.server.online_players:
                    if player.name == player_name:
                        player_online = True
                        break
                if not player_online:
                    offline_concurrent_players.append(player_name)
            
            for player_name in offline_concurrent_players:
                self._concurrent_bindings.discard(player_name)
                self.logger.debug(f"清理离线玩家的并发绑定状态: {player_name}")
        
        # 注意: 验证码消息记录的清理已经在cleanup_expired_verifications中处理，这里不再重复
        if hasattr(self, '_verification_retry_count'):
            # 这里我们清理那些没有对应待验证记录的重试计数
            global _verification_codes
            expired_retries = [qq for qq in self._verification_retry_count.keys() 
                             if qq not in _verification_codes]
            for qq in expired_retries:
                del self._verification_retry_count[qq]
        
        # 清理表单显示缓存中的过期记录或离线玩家记录
        online_player_names = {player.name for player in self.server.online_players}
        expired_forms = []
        
        for player_name, display_time in self._form_display_cache.items():
            if (current_time - display_time > 300 or  # 5分钟过期
                player_name not in online_player_names):  # 或玩家离线
                expired_forms.append(player_name)
        
        for player_name in expired_forms:
            if player_name in self._form_display_cache:
                del self._form_display_cache[player_name]
            if player_name in self._form_display_count:
                del self._form_display_count[player_name]
        
        # 清理绑定队列中的过期记录或离线玩家记录
        if hasattr(self, '_binding_queue'):
            original_queue_length = len(self._binding_queue)
            self._binding_queue = [(p, q, t) for p, q, t in self._binding_queue 
                                 if (current_time - t <= 300 and  # 5分钟过期
                                     p in online_player_names)]  # 且玩家在线
            if len(self._binding_queue) < original_queue_length:
                removed_count = original_queue_length - len(self._binding_queue)
                self.logger.debug(f"清理{removed_count}个过期/离线的绑定队列任务")
        
        # 清理队列通知记录中的离线玩家
        if hasattr(self, '_queue_notification_sent'):
            offline_notified_players = [p for p in self._queue_notification_sent if p not in online_player_names]
            for player_name in offline_notified_players:
                self._queue_notification_sent.discard(player_name)
    
    def _can_send_verification(self, qq_number: str, player_name: str = None) -> tuple[bool, str]:
        """检查是否可以发送验证码（包含60秒冷却检查）"""
        global _pending_verifications, _verification_codes, _player_bind_attempts
        current_time = time.time()
        
        # 1. 检查玩家级别的60秒验证码申请冷却（包括取消绑定的情况）
        if player_name:
            # 首先检查绑定尝试记录（包括取消绑定的情况）
            if player_name in _player_bind_attempts:
                last_attempt_time = _player_bind_attempts[player_name]
                cooldown_remaining = 60 - (current_time - last_attempt_time)
                if cooldown_remaining > 0:
                    self.logger.info(f"玩家 {player_name} 验证码申请被拒绝：60秒冷却中，剩余{int(cooldown_remaining)}秒（包括取消绑定）")
                    return False, f"您在60秒内只能申请一个验证码，请等待{int(cooldown_remaining)}秒后再次尝试"
            
            # 检查该玩家是否有待验证的请求
            if player_name in _pending_verifications:
                last_request_time = _pending_verifications[player_name].get("timestamp", 0)
                cooldown_remaining = 60 - (current_time - last_request_time)
                if cooldown_remaining > 0:
                    self.logger.info(f"玩家 {player_name} 验证码申请被拒绝：60秒冷却中，剩余{int(cooldown_remaining)}秒")
                    return False, f"您在60秒内只能申请一个验证码，请等待{int(cooldown_remaining)}秒后再次尝试"
        
        # 2. 检查QQ号级别的冷却（绑定失败后的惩罚冷却）
        if qq_number in self._binding_rate_limit:
            cooldown_remaining = self._binding_cooldown - (current_time - self._binding_rate_limit[qq_number])
            if cooldown_remaining > 0:
                return False, f"该QQ号请等待{int(cooldown_remaining)}秒后再次尝试"
        
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
    
    def _cleanup_old_verification(self, player_name: str):
        """清理玩家的旧验证码并撤回消息"""
        global _pending_verifications, _verification_codes, _verification_messages
        
        try:
            # 如果玩家有旧的验证请求，清理它
            if player_name in _pending_verifications:
                old_verification = _pending_verifications[player_name]
                old_qq = old_verification.get("qq")
                
                self.logger.info(f"玩家 {player_name} 重新申请验证码，清理旧验证码 (QQ: {old_qq})")
                
                # 清理旧的验证码记录
                del _pending_verifications[player_name]
                
                # 清理对应的QQ验证码记录
                if old_qq and old_qq in _verification_codes:
                    del _verification_codes[old_qq]
                
                # 清理验证队列中的记录
                if old_qq and old_qq in self._verification_queue:
                    del self._verification_queue[old_qq]
                
                # 从并发绑定集合中移除
                self._concurrent_bindings.discard(player_name)
                
                # 异步撤回旧的验证码消息
                if old_qq and old_qq in _verification_messages and _current_ws:
                    asyncio.run_coroutine_threadsafe(
                        delete_verification_message(old_qq),
                        self._loop
                    )
                    self.logger.info(f"已撤回玩家 {player_name} 的旧验证码消息 (QQ: {old_qq})")
                
                # 从验证码发送队列中移除旧的请求
                original_queue_length = len(self._verification_send_queue)
                self._verification_send_queue = [(p, q, c, a, t) for p, q, c, a, t in self._verification_send_queue 
                                               if p.name != player_name]
                if len(self._verification_send_queue) < original_queue_length:
                    self.logger.debug(f"已从验证码发送队列中移除玩家 {player_name} 的旧请求")
                
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"清理玩家 {player_name} 旧验证码时出错: {e}")
            return False
    
    def _cleanup_qq_old_verifications(self, qq_number: str):
        """清理与特定QQ号相关的所有旧验证码并撤回消息"""
        global _pending_verifications, _verification_codes, _verification_messages
        
        try:
            cleaned_count = 0
            
            # 清理验证码记录
            if qq_number in _verification_codes:
                del _verification_codes[qq_number]
                cleaned_count += 1
                self.logger.info(f"清理QQ {qq_number} 的旧验证码记录")
            
            # 撤回验证码消息
            if qq_number in _verification_messages and _current_ws:
                asyncio.run_coroutine_threadsafe(
                    delete_verification_message(qq_number),
                    self._loop
                )
                cleaned_count += 1
                self.logger.info(f"撤回QQ {qq_number} 的旧验证码消息")
            
            # 清理验证队列中的记录
            if qq_number in self._verification_queue:
                del self._verification_queue[qq_number]
                cleaned_count += 1
            
            # 清理统一验证尝试计数器中与此QQ相关的记录
            if hasattr(self, '_unified_verification_attempts'):
                keys_to_remove = [key for key in self._unified_verification_attempts.keys() 
                                if key.endswith(f"_{qq_number}")]
                for key in keys_to_remove:
                    del self._unified_verification_attempts[key]
                    cleaned_count += 1
            
            if cleaned_count > 0:
                self.logger.info(f"已清理QQ {qq_number} 相关的 {cleaned_count} 项旧验证码数据")
            
            return cleaned_count > 0
            
        except Exception as e:
            self.logger.error(f"清理QQ {qq_number} 旧验证码时出错: {e}")
            return False
    
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
        """多玩家优化的周期清理任务"""
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
            
            # 清理过期的待确认QQ信息（60秒过期或玩家离线）
            expired_confirmations = []
            for player_name, qq_info in self._pending_qq_confirmations.items():
                if (current_time - qq_info["timestamp"] > 60 or  # 60秒过期
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
            
            # 清理过期的队列项（超过60秒）
            original_length = len(self._binding_queue)
            self._binding_queue = [(p, q, t) for p, q, t in self._binding_queue 
                                 if current_time - t < 60]
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
                    
                    # 通知玩家开始绑定（使用调度器确保在主线程执行）
                    def notify_player_start():
                        try:
                            online_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}🎉 排队完成！开始为您处理QQ绑定{ColorFormat.RESET}")
                        except Exception as e:
                            self.logger.error(f"发送绑定开始通知失败: {e}")
                    
                    self.server.scheduler.run_task(
                        self,
                        notify_player_start,
                        delay=1  # 立即执行
                    )
                    
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
                            # 使用调度器确保在主线程执行
                            def notify_queue_status():
                                try:
                                    online_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}⏳ 排队状态更新：您是第{queue_position}位{ColorFormat.RESET}")
                                except Exception as e:
                                    self.logger.error(f"发送排队状态更新失败: {e}")
                            
                            self.server.scheduler.run_task(
                                self,
                                notify_queue_status,
                                delay=1  # 立即执行
                            )
                            self._queue_notification_sent.add(player_name)
                    break  # 如果第一个都无法处理，后面的也不行
            
            # 记录处理日志
            if processed_count > 0 or expired_count > 0:
                self.logger.debug(f"队列处理完成: 处理{processed_count}个请求, 清理{expired_count}个过期项, 剩余{len(self._binding_queue)}个")
                
        except Exception as e:
            self.logger.error(f"队列处理失败: {e}")
    
    def _process_verification_send_queue(self):
        """处理验证码发送队列"""
        try:
            if not self._verification_send_queue:
                return
            
            current_time = time.time()
            
            # 检查发送间隔
            if current_time - self._last_verification_send_time < self._verification_send_interval:
                return
            
            # 检查WebSocket连接
            if not self._is_websocket_connected():
                self.logger.warning("WebSocket未连接，验证码发送队列暂停")
                return
            
            processed = 0
            to_remove = []
            
            for i, (player, qq_number, verification_code, attempt, timestamp) in enumerate(self._verification_send_queue):
                # 检查是否过期（60秒）
                if current_time - timestamp > 60:
                    to_remove.append(i)
                    continue
                
                # 检查玩家是否仍在线
                online_player = None
                for p in self.server.online_players:
                    if p.name == player.name:
                        online_player = p
                        break
                
                if not online_player:
                    to_remove.append(i)
                    continue
                
                # 尝试发送验证码
                try:
                    verification_text = f"\n🎮 QQsync-群服互通 绑定验证\n\n玩家名: {player.name}\n验证码: {verification_code}\n\n✅ 请在游戏中输入此验证码完成绑定\n📱 或群内输入: /verify {verification_code}\n⏰ 验证码60秒内有效\n\n🔄 尝试次数: {attempt}/{self._max_verification_retries}"
                    
                    # 异步发送验证码
                    asyncio.run_coroutine_threadsafe(
                        self._send_verification_with_retry(_current_ws, int(qq_number), verification_text, player, verification_code, attempt),
                        self._loop
                    )
                    
                    # 更新发送时间
                    self._last_verification_send_time = current_time
                    
                    # 标记为已处理
                    to_remove.append(i)
                    processed += 1
                    
                    self.logger.info(f"验证码发送队列处理: 玩家{player.name} QQ{qq_number} 尝试{attempt}")
                    
                    # 每次只处理一个，避免过快发送
                    break
                    
                except Exception as e:
                    self.logger.error(f"验证码发送失败: {e}")
                    
                    # 增加重试计数
                    if attempt < self._max_verification_retries:
                        # 更新重试次数
                        self._verification_send_queue[i] = (player, qq_number, verification_code, attempt + 1, timestamp)
                    else:
                        # 超过最大重试次数，通知玩家失败（使用调度器确保在主线程执行）
                        def notify_player_failure():
                            try:
                                online_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证码发送失败，请重新开始绑定流程{ColorFormat.RESET}")
                            except Exception as e:
                                self.logger.error(f"发送验证码失败通知失败: {e}")
                        
                        self.server.scheduler.run_task(
                            self,
                            notify_player_failure,
                            delay=1  # 立即执行
                        )
                        to_remove.append(i)
            
            # 移除已处理或过期的项目（从后往前删除）
            for i in sorted(to_remove, reverse=True):
                del self._verification_send_queue[i]
            
            if processed > 0:
                self.logger.debug(f"验证码队列处理完成: 发送{processed}个验证码, 剩余{len(self._verification_send_queue)}个")
                
        except Exception as e:
            self.logger.error(f"验证码发送队列处理失败: {e}")
    
    async def _send_verification_with_retry(self, ws, user_id, verification_text, player, verification_code, attempt):
        """带重试机制的验证码发送（发送到群组@消息）"""
        try:
            # 获取目标群组ID
            target_group = self.get_config("target_group")
            
            # 发送群组@消息而不是私聊消息，传递QQ号用于标识
            await send_group_at_msg(ws, target_group, user_id, verification_text, str(user_id))
            
            # 发送成功，在控制台再次显示验证码（确认发送成功）
            success_console_msg = f"{ColorFormat.GREEN}[验证码发送成功] 玩家: {ColorFormat.WHITE}{player.name}{ColorFormat.GREEN} | QQ: {ColorFormat.WHITE}{user_id}{ColorFormat.GREEN} | 验证码: {ColorFormat.YELLOW}{verification_code}{ColorFormat.GREEN} | 尝试: {attempt} | 方式: 群组@消息{ColorFormat.RESET}"
            self.logger.info(success_console_msg)
            
            # 发送成功，通知玩家（必须在主线程中执行）
            def notify_player_success():
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}验证码已在群组中@您发送！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请查看QQ群消息{ColorFormat.RESET}")
            
            self.server.scheduler.run_task(
                self,
                notify_player_success,
                delay=1  # 立即执行
            )
            
            # 显示验证码输入表单（必须在主线程中执行）
            self.server.scheduler.run_task(
                self,
                lambda: self.show_verification_form(player),
                delay=20  # 1秒延迟
            )
            
        except Exception as e:
            # 发送失败，在控制台显示失败信息
            fail_console_msg = f"{ColorFormat.RED}[验证码发送失败] 玩家: {ColorFormat.WHITE}{player.name}{ColorFormat.RED} | QQ: {ColorFormat.WHITE}{user_id}{ColorFormat.RED} | 验证码: {ColorFormat.YELLOW}{verification_code}{ColorFormat.RED} | 尝试: {attempt} | 方式: 群组@消息 | 错误: {e}{ColorFormat.RESET}"
            self.logger.error(fail_console_msg)
            
            self.logger.error(f"验证码发送异常 (尝试{attempt}): {e}")
            raise e
    
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

def remove_emoji_for_game(text):
    """
    将emoji表情符号转换为文本描述，供游戏内显示使用
    """
    if not text:
        return text
    
    # 常见emoji映射表
    emoji_map = {
        '😀': '[笑脸]', '😁': '[开心]', '😂': '[笑哭]', '🤣': '[大笑]', '😃': '[微笑]',
        '😄': '[开心]', '😅': '[汗笑]', '😆': '[眯眼笑]', '😉': '[眨眼]', '😊': '[微笑]',
        '😋': '[流口水]', '😎': '[酷]', '😍': '[花眼]', '😘': '[飞吻]', '🥰': '[三颗心]',
        '😗': '[亲吻]', '😙': '[亲吻]', '😚': '[亲吻]', '☺': '[微笑]', '🙂': '[微笑]',
        '🤗': '[拥抱]', '🤩': '[星眼]', '🤔': '[思考]', '🤨': '[怀疑]', '😐': '[面无表情]',
        '😑': '[无语]', '😶': '[无言]', '🙄': '[白眼]', '😏': '[坏笑]', '😣': '[困扰]',
        '😥': '[失望]', '😮': '[惊讶]', '🤐': '[闭嘴]', '😯': '[惊讶]', '😪': '[困倦]',
        '😫': '[疲倦]', '😴': '[睡觉]', '😌': '[安心]', '😛': '[吐舌]', '😜': '[眨眼吐舌]',
        '😝': '[闭眼吐舌]', '🤤': '[流口水]', '😒': '[无聊]', '😓': '[冷汗]', '😔': '[沮丧]',
        '😕': '[困惑]', '🙃': '[倒脸]', '🤑': '[财迷]', '😲': '[震惊]', '☹': '[皱眉]',
        '🙁': '[皱眉]', '😖': '[困扰]', '😞': '[失望]', '😟': '[担心]', '😤': '[愤怒]',
        '😢': '[流泪]', '😭': '[大哭]', '😦': '[皱眉]', '😧': '[痛苦]', '😨': '[害怕]',
        '😩': '[疲倦]', '🤯': '[爆头]', '😬': '[咧嘴]', '😰': '[冷汗]', '😱': '[尖叫]',
        '🥵': '[热]', '🥶': '[冷]', '😳': '[脸红]', '🤪': '[疯狂]', '😵': '[晕]',
        '😡': '[愤怒]', '😠': '[生气]', '🤬': '[咒骂]', '😷': '[口罩]', '🤒': '[生病]',
        '🤕': '[受伤]', '🤢': '[恶心]', '🤮': '[呕吐]', '🤧': '[喷嚏]', '😇': '[天使]',
        '🥳': '[庆祝]', '🥺': '[请求]', '🤠': '[牛仔]', '🤡': '[小丑]', '🤥': '[说谎]',
        '🤫': '[嘘]', '🤭': '[捂嘴笑]', '🧐': '[单片眼镜]', '🤓': '[书呆子]',
        # 手势
        '👍': '[赞]', '👎': '[踩]', '👌': '[OK]', '✌': '[胜利]', '🤞': '[交叉手指]',
        '🤟': '[爱你]', '🤘': '[摇滚]', '🤙': '[打电话]', '👈': '[左指]', '👉': '[右指]',
        '👆': '[上指]', '👇': '[下指]', '☝': '[食指]', '✋': '[举手]', '🤚': '[举手背]',
        '🖐': '[张开手]', '🖖': '[瓦肯礼]', '👋': '[挥手]', '🤛': '[左拳]', '🤜': '[右拳]',
        '👊': '[拳头]', '✊': '[拳头]', '👏': '[拍手]', '🙌': '[举双手]', '👐': '[张开双手]',
        '🤲': '[捧手]', '🙏': '[祈祷]', '✍': '[写字]', '💪': '[肌肉]',
        # 心形
        '❤': '[红心]', '🧡': '[橙心]', '💛': '[黄心]', '💚': '[绿心]', '💙': '[蓝心]',
        '💜': '[紫心]', '🖤': '[黑心]', '🤍': '[白心]', '🤎': '[棕心]', '💔': '[心碎]',
        '❣': '[心叹号]', '💕': '[两颗心]', '💞': '[旋转心]', '💓': '[心跳]', '💗': '[增长心]',
        '💖': '[闪亮心]', '💘': '[心箭]', '💝': '[心礼盒]', '💟': '[心装饰]',
        # 常用符号
        '🔥': '[火]', '💯': '[100分]', '💢': '[愤怒]', '💥': '[爆炸]', '💫': '[星星]',
        '💦': '[汗滴]', '💨': '[风]', '🕳': '[洞]', '💣': '[炸弹]', '💤': '[睡觉]',
        '👀': '[眼睛]', '🗨': '[对话框]', '💭': '[思考泡泡]', '💯': '[满分]',
        # 食物
        '🍕': '[披萨]', '🍔': '[汉堡]', '🍟': '[薯条]', '🌭': '[热狗]', '🥪': '[三明治]',
        '🌮': '[玉米饼]', '🌯': '[卷饼]', '🥙': '[口袋饼]', '🧆': '[丸子]', '🥚': '[鸡蛋]',
        '🍳': '[煎蛋]', '🥘': '[炖菜]', '🍲': '[火锅]', '🥣': '[碗]', '🥗': '[沙拉]',
        '🍿': '[爆米花]', '🧈': '[黄油]', '🧂': '[盐]', '🥫': '[罐头]', '🍱': '[便当]',
        '🍘': '[米饼]', '🍙': '[饭团]', '🍚': '[米饭]', '🍛': '[咖喱]', '🍜': '[拉面]',
        '🍝': '[意面]', '🍠': '[红薯]', '🍢': '[关东煮]', '🍣': '[寿司]', '🍤': '[天妇罗]',
        '🍥': '[鱼糕]', '🥮': '[月饼]', '🍡': '[丸子]', '🥟': '[饺子]', '🥠': '[幸运饼]',
        '🥡': '[外卖盒]', '🦀': '[螃蟹]', '🦞': '[龙虾]', '🦐': '[虾]', '🦑': '[鱿鱼]',
        '🐙': '[章鱼]', '🍌': '[香蕉]', '🍎': '[苹果]', '🍏': '[青苹果]', '🍐': '[梨]',
        '🍊': '[橘子]', '🍋': '[柠檬]', '🍉': '[西瓜]', '🍇': '[葡萄]', '🍓': '[草莓]',
        '🫐': '[蓝莓]', '🍈': '[甜瓜]', '🍒': '[樱桃]', '🍑': '[桃子]', '🥭': '[芒果]',
        '🍍': '[菠萝]', '🥥': '[椰子]', '🥝': '[猕猴桃]', '🍅': '[番茄]', '🍆': '[茄子]',
        '🥑': '[牛油果]', '🥦': '[西兰花]', '🥬': '[白菜]', '🥒': '[黄瓜]', '🌶': '[辣椒]',
        '🫑': '[彩椒]', '🌽': '[玉米]', '🥕': '[胡萝卜]', '🫒': '[橄榄]', '🧄': '[大蒜]',
        '🧅': '[洋葱]', '🥔': '[土豆]', '🍠': '[红薯]', '🥐': '[羊角包]', '🥖': '[法棍]',
        '🫓': '[扁面包]', '🥨': '[椒盐卷饼]', '🥯': '[百吉饼]', '🍞': '[面包]', '🥜': '[花生]',
        '🌰': '[栗子]', '🥧': '[派]', '🧁': '[纸杯蛋糕]', '🍰': '[蛋糕]', '🎂': '[生日蛋糕]',
        '🍮': '[布丁]', '🍭': '[棒棒糖]', '🍬': '[糖果]', '🍫': '[巧克力]', '🍩': '[甜甜圈]',
        '🍪': '[饼干]', '🌭': '[热狗]', '☕': '[咖啡]', '🍵': '[茶]', '🧃': '[果汁盒]',
        '🥤': '[饮料]', '🧋': '[奶茶]', '🍶': '[清酒]', '🍾': '[香槟]', '🍷': '[红酒]',
        '🍸': '[鸡尾酒]', '🍹': '[热带饮料]', '🍺': '[啤酒]', '🍻': '[干杯]', '🥂': '[香槟杯]',
        '🥃': '[威士忌]', '🥛': '[牛奶]', '🧊': '[冰块]', '🥄': '[勺子]', '🍴': '[餐具]',
        '🍽': '[餐盘]', '🥢': '[筷子]', '🏺': '[陶罐]',
        # 动物
        '🐶': '[小狗]', '🐱': '[小猫]', '🐭': '[老鼠]', '🐹': '[仓鼠]', '🐰': '[兔子]',
        '🦊': '[狐狸]', '🐻': '[熊]', '🐼': '[熊猫]', '🐻‍❄️': '[北极熊]', '🐨': '[考拉]',
        '🐯': '[老虎]', '🦁': '[狮子]', '🐮': '[牛]', '🐷': '[猪]', '🐽': '[猪鼻]',
        '🐸': '[青蛙]', '🐵': '[猴脸]', '🙈': '[非礼勿视]', '🙉': '[非礼勿听]', '🙊': '[非礼勿言]',
        '🐒': '[猴子]', '🐔': '[鸡]', '🐧': '[企鹅]', '🐦': '[鸟]', '🐤': '[小鸡]',
        '🐣': '[破壳鸡]', '🐥': '[小鸡]', '🦆': '[鸭子]', '🦅': '[老鹰]', '🦉': '[猫头鹰]',
        '🦇': '[蝙蝠]', '🐺': '[狼]', '🐗': '[野猪]', '🐴': '[马脸]', '🦄': '[独角兽]',
        '🐝': '[蜜蜂]', '🐛': '[毛毛虫]', '🦋': '[蝴蝶]', '🐌': '[蜗牛]', '🐞': '[瓢虫]',
        '🐜': '[蚂蚁]', '🦟': '[蚊子]', '🦗': '[蟋蟀]', '🕷': '[蜘蛛]', '🕸': '[蜘蛛网]',
        '🦂': '[蝎子]', '🐢': '[乌龟]', '🐍': '[蛇]', '🦎': '[蜥蜴]', '🦖': '[霸王龙]',
        '🦕': '[长颈龙]', '🐙': '[章鱼]', '🦑': '[鱿鱼]', '🦐': '[虾]', '🦞': '[龙虾]',
        '🦀': '[螃蟹]', '🐡': '[河豚]', '🐠': '[热带鱼]', '🐟': '[鱼]', '🐬': '[海豚]',
        '🐳': '[鲸鱼]', '🐋': '[蓝鲸]', '🦈': '[鲨鱼]', '🐊': '[鳄鱼]', '🐅': '[老虎]',
        '🐆': '[豹子]', '🦓': '[斑马]', '🦍': '[大猩猩]', '🦧': '[猩猩]', '🐘': '[大象]',
        '🦣': '[猛犸象]', '🦏': '[犀牛]', '🦛': '[河马]', '🐪': '[骆驼]', '🐫': '[双峰驼]',
        '🦒': '[长颈鹿]', '🦘': '[袋鼠]', '🐃': '[水牛]', '🐂': '[公牛]', '🐄': '[奶牛]',
        '🐎': '[马]', '🐖': '[猪]', '🐏': '[公羊]', '🐑': '[绵羊]', '🦙': '[羊驼]',
        '🐐': '[山羊]', '🦌': '[鹿]', '🐕': '[狗]', '🐩': '[贵宾犬]', '🦮': '[导盲犬]',
        '🐕‍🦺': '[服务犬]', '🐈': '[猫]', '🐈‍⬛': '[黑猫]', '🐓': '[公鸡]', '🦃': '[火鸡]',
        '🦚': '[孔雀]', '🦜': '[鹦鹉]', '🦢': '[天鹅]', '🦩': '[火烈鸟]', '🕊': '[鸽子]',
        '🐇': '[兔子]', '🦝': '[浣熊]', '🦨': '[臭鼬]', '🦡': '[獾]', '🦦': '[水獭]',
        '🦥': '[树懒]', '🐁': '[老鼠]', '🐀': '[大老鼠]', '🐿': '[松鼠]', '🦔': '[刺猬]',
    }
    
    # 替换已知的emoji
    result = text
    for emoji, description in emoji_map.items():
        result = result.replace(emoji, description)
    
    # 使用正则表达式移除其他unicode emoji
    # 这个正则表达式匹配大部分emoji字符
    emoji_pattern = re.compile(
        '['
        '\U0001F600-\U0001F64F'  # 表情符号
        '\U0001F300-\U0001F5FF'  # 符号和象形文字
        '\U0001F680-\U0001F6FF'  # 交通和地图符号
        '\U0001F1E0-\U0001F1FF'  # 国旗
        '\U00002600-\U000026FF'  # 杂项符号
        '\U00002700-\U000027BF'  # 装饰符号
        '\U0001F900-\U0001F9FF'  # 补充符号和象形文字
        '\U0001FA70-\U0001FAFF'  # 符号和象形文字扩展-A
        '\U00002300-\U000023FF'  # 杂项技术符号
        '\U0001F000-\U0001F02F'  # 麻将符号
        '\U0001F0A0-\U0001F0FF'  # 扑克符号
        ']+',
        flags=re.UNICODE
    )
    
    # 将未映射的emoji替换为[表情]
    result = emoji_pattern.sub('[表情]', result)
    
    return result

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
        
        # 处理emoji表情符号，转换为游戏内可显示的文本
        processed_message = remove_emoji_for_game(processed_message)
        
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

async def send_group_at_msg(ws, group_id: int, user_id: int, text: str, verification_qq: str = None):
    """发送群组@消息"""
    # 构建@消息格式
    message = [
        {
            "type": "at",
            "data": {
                "qq": str(user_id)
            }
        },
        {
            "type": "text",
            "data": {
                "text": f" {text}"
            }
        }
    ]
    
    payload = {
        "action": "send_group_msg",
        "params": {
            "group_id": group_id, 
            "message": message
        },
    }
    
    # 如果是验证码消息，添加echo标识符和紧急撤回机制
    if verification_qq:
        payload["echo"] = f"verification_msg:{verification_qq}"
        
        # 设置紧急撤回定时器：1分45秒后强制撤回（确保在QQ 2分钟限制内）
        async def emergency_retract():
            await asyncio.sleep(105)  # 1分45秒
            if verification_qq in _verification_messages:
                if _plugin_instance:
                    _plugin_instance.logger.warning(f"QQ {verification_qq} 的验证码消息接近2分钟限制，执行紧急撤回")
                await delete_verification_message(verification_qq)
        
        # 异步启动紧急撤回定时器
        asyncio.create_task(emergency_retract())
        
        # 设置超时检查：如果5秒内没有收到API响应，记录警告
        async def check_message_id_timeout():
            await asyncio.sleep(5)  # 等待5秒
            if verification_qq not in _verification_messages:
                if _plugin_instance:
                    _plugin_instance.logger.warning(f"验证码消息发送后未能获取message_id，无法自动撤回 (QQ: {verification_qq})")
        
        # 异步启动超时检查
        asyncio.create_task(check_message_id_timeout())
    
    max_retries = 3
    retry_delay = 1
    
    for attempt in range(max_retries):
        try:
            # 检查WebSocket连接状态（安全方式）
            try:
                if hasattr(ws, 'closed') and ws.closed:
                    raise Exception("WebSocket连接已关闭")
                elif hasattr(ws, 'close_code') and ws.close_code is not None:
                    raise Exception("WebSocket连接已关闭")
            except AttributeError:
                # 如果无法检查状态，继续尝试发送
                pass
            
            await ws.send(json.dumps(payload))
            
            if _plugin_instance:
                _plugin_instance.logger.info(f"{ColorFormat.GREEN}验证码已发送到群组@消息: QQ{user_id} (尝试{attempt + 1}){ColorFormat.RESET}")
            return True
            
        except Exception as e:
            if _plugin_instance:
                _plugin_instance.logger.warning(f"群组@消息发送失败 (尝试{attempt + 1}/{max_retries}): {e}")
            
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # 指数退避
            else:
                if _plugin_instance:
                    error_msg = f"{ColorFormat.RED}群组@消息发送最终失败: {ColorFormat.YELLOW}{e}{ColorFormat.RESET}"
                    _plugin_instance.logger.error(error_msg)
                raise e
    
    return False

async def delete_verification_message(qq_number: str, retry_count: int = 0):
    """撤回验证码消息（带重试机制）"""
    global _verification_messages
    
    if qq_number in _verification_messages and _current_ws:
        try:
            message_info = _verification_messages[qq_number]
            message_id = message_info["message_id"]
            
            # 撤回消息
            success = await delete_msg(_current_ws, message_id)
            
            if success:
                # 从记录中移除
                del _verification_messages[qq_number]
                
                if _plugin_instance:
                    _plugin_instance.logger.info(f"{ColorFormat.GREEN}已撤回QQ {qq_number} 的验证码消息{ColorFormat.RESET}")
            else:
                # 撤回失败，重试
                if retry_count < 2:  # 最多重试2次
                    await asyncio.sleep(1)  # 等待1秒后重试
                    await delete_verification_message(qq_number, retry_count + 1)
                else:
                    # 重试失败，仍然从记录中移除，避免重复尝试
                    del _verification_messages[qq_number]
                    if _plugin_instance:
                        _plugin_instance.logger.warning(f"QQ {qq_number} 的验证码消息撤回失败，已放弃重试")
                
        except Exception as e:
            if _plugin_instance:
                _plugin_instance.logger.error(f"撤回验证码消息失败 (QQ: {qq_number}): {e}")
            
            # 发生异常时，如果不是网络问题，也从记录中移除
            if qq_number in _verification_messages:
                del _verification_messages[qq_number]

async def delete_msg(ws, message_id: int):
    """撤回消息"""
    payload = {
        "action": "delete_msg",
        "params": {
            "message_id": message_id
        }
    }
    
    try:
        await ws.send(json.dumps(payload))
        if _plugin_instance:
            _plugin_instance.logger.info(f"{ColorFormat.GREEN}已撤回消息 ID: {message_id}{ColorFormat.RESET}")
        return True
    except Exception as e:
        if _plugin_instance:
            error_msg = f"{ColorFormat.RED}撤回消息失败: {ColorFormat.YELLOW}{e}{ColorFormat.RESET}"
            _plugin_instance.logger.error(error_msg)
        return False

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
    raw_nickname = data.get("sender", {}).get("nickname", user_id)
    
    # 处理昵称中的emoji，确保控制台和游戏内显示正常
    nickname = remove_emoji_for_game(raw_nickname)
    
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
            
            # 确保显示名称不包含emoji（额外保护）
            display_name = remove_emoji_for_game(display_name)
            
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
            if _plugin_instance:
                _plugin_instance.logger.debug(f"找到验证码数据: QQ {user_id}")
            code_info = _verification_codes[user_id]
            # 双重时间检查：timestamp和creation_time
            time_since_creation = time.time() - code_info["timestamp"]
            
            # 如果有精确时间记录，使用更准确的检查
            if "creation_time" in code_info:
                creation_time = code_info["creation_time"]
                time_delta = datetime.datetime.now() - creation_time
                is_expired = time_delta.total_seconds() > 60
            else:
                is_expired = time_since_creation > 60
                
            if is_expired:  # 60秒过期
                del _verification_codes[user_id]
                # 同时清理对应的待验证数据
                player_name = code_info.get("player_name")
                if player_name and player_name in _pending_verifications:
                    del _pending_verifications[player_name]
                
                # 撤回过期的验证码消息
                if _current_ws:
                    asyncio.run_coroutine_threadsafe(
                        delete_verification_message(user_id),
                        _plugin_instance._loop
                    )
                
                reply = f"验证码已过期（{time_since_creation:.1f}秒），请重新在游戏中申请绑定"
            elif verification_code == code_info["code"]:
                # 验证码匹配，但需要进一步验证安全性
                player_name = code_info["player_name"]
                
                # 安全检查1：验证该玩家确实在pending_verifications中有对应的验证请求
                if player_name not in _pending_verifications:
                    reply = "验证码无效：该验证码对应的玩家未在游戏中申请绑定"
                    _plugin_instance.logger.warning(f"QQ验证安全检查失败: 玩家 {player_name} 不在待验证列表中 (QQ: {user_id})")
                elif _pending_verifications[player_name].get("qq") != str(user_id):
                    reply = "验证码无效：该验证码不属于您的QQ号"
                    _plugin_instance.logger.warning(f"QQ验证安全检查失败: QQ号不匹配 - 期望: {_pending_verifications[player_name].get('qq')}, 实际: {user_id}")
                elif code_info.get("used", False):
                    reply = "验证码已使用：该验证码已被使用过，请重新申请"
                    _plugin_instance.logger.warning(f"QQ验证安全检查失败: 验证码已被使用 (QQ: {user_id}, 玩家: {player_name})")
                else:
                    # 安全检查通过，继续绑定流程
                    # 标记验证码为已使用，防止重复使用
                    code_info["used"] = True
                    code_info["use_time"] = time.time()
                    
                    # 控制台显示验证成功信息
                    verify_success_msg = f"{ColorFormat.GREEN}[验证成功] 玩家: {ColorFormat.WHITE}{player_name}{ColorFormat.GREEN} | QQ: {ColorFormat.WHITE}{user_id}{ColorFormat.GREEN} | 验证码: {ColorFormat.YELLOW}{verification_code}{ColorFormat.GREEN} | 绑定完成{ColorFormat.RESET}"
                    _plugin_instance.logger.info(verify_success_msg)
                    
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
                        
                        # 发送成功消息给玩家（如果在线）- 使用调度器确保在主线程执行
                        def notify_player_success():
                            try:
                                online_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}🎉 QQ绑定成功！{ColorFormat.RESET}")
                                online_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您的QQ {user_id} 已与游戏账号绑定{ColorFormat.RESET}")
                            except Exception as e:
                                _plugin_instance.logger.error(f"发送绑定成功消息失败: {e}")
                        
                        _plugin_instance.server.scheduler.run_task(
                            _plugin_instance,
                            notify_player_success,
                            delay=1  # 立即执行
                        )
                        
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
                    
                    # 清理统一验证尝试计数器
                    verification_key = f"unified_attempts_{player_name}_{user_id}"
                    if hasattr(_plugin_instance, '_unified_verification_attempts') and verification_key in _plugin_instance._unified_verification_attempts:
                        del _plugin_instance._unified_verification_attempts[verification_key]
                    
                    # 撤回验证码消息
                    if _current_ws:
                        asyncio.run_coroutine_threadsafe(
                            delete_verification_message(user_id),
                            _plugin_instance._loop
                        )
                    if player_name in _pending_verifications:
                        del _pending_verifications[player_name]
                    
                    # 通知QQ群
                    if _current_ws and reply.startswith("🎉"):
                        asyncio.run_coroutine_threadsafe(
                            send_group_msg(_current_ws, group_id=group_id, text=f"🎮 玩家 {player_name} 已完成QQ绑定"),
                            _plugin_instance._loop
                        )
            else:
                # 验证失败处理：使用统一的验证尝试计数
                player_name = code_info.get("player_name", "unknown")
                verification_key = f"unified_attempts_{player_name}_{user_id}"
                current_attempts = getattr(_plugin_instance, '_unified_verification_attempts', {}).get(verification_key, 0) + 1
                
                # 初始化统一验证尝试计数器
                if not hasattr(_plugin_instance, '_unified_verification_attempts'):
                    _plugin_instance._unified_verification_attempts = {}
                _plugin_instance._unified_verification_attempts[verification_key] = current_attempts
                
                max_attempts = 3  # 游戏内和QQ总共3次尝试机会
                remaining_attempts = max_attempts - current_attempts
                
                if remaining_attempts > 0:
                    # 还有重试机会，记录冷却时间和安全日志
                    _plugin_instance._binding_rate_limit[user_id] = time.time()
                    _plugin_instance.logger.warning(f"QQ验证码验证失败: QQ {user_id} 输入错误验证码，对应玩家: {player_name}，剩余尝试次数: {remaining_attempts}")
                    reply = f"验证码错误！还可以尝试 {remaining_attempts} 次\n请检查验证码后重新输入\n提示：也可以在游戏内表单输入验证码"
                else:
                    # 尝试次数用完，清理验证数据并触发冷却
                    _plugin_instance.logger.warning(f"统一验证尝试次数超限: 玩家 {player_name} (QQ: {user_id}) 已尝试 {max_attempts} 次，清理验证数据并触发冷却")
                    
                    # 清理验证数据
                    del _verification_codes[user_id]
                    if player_name in _pending_verifications:
                        del _pending_verifications[player_name]
                    
                    # 撤回验证码消息
                    if _current_ws:
                        asyncio.run_coroutine_threadsafe(
                            delete_verification_message(user_id),
                            _plugin_instance._loop
                        )
                    
                    # 清理统一尝试计数
                    if verification_key in _plugin_instance._unified_verification_attempts:
                        del _plugin_instance._unified_verification_attempts[verification_key]
                    
                    # 触发验证失败冷却（60秒）
                    if not hasattr(_plugin_instance, '_player_verification_cooldown'):
                        _plugin_instance._player_verification_cooldown = {}
                    _plugin_instance._player_verification_cooldown[player_name] = time.time()
                    
                    # 同时对QQ号也设置冷却
                    _plugin_instance._binding_rate_limit[user_id] = time.time()
                    
                    reply = f"验证码尝试次数已达上限（{max_attempts}次）\n已触发60秒冷却，请稍后重新在游戏中申请绑定获取新的验证码"
        else:
            # 未找到验证码，记录冷却时间（多玩家优化）
            if _plugin_instance:
                # 添加调试信息
                _plugin_instance.logger.debug(f"未找到验证码: QQ {user_id}, 当前验证码数据: {list(_verification_codes.keys())}")
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
                    
                    reply = f"✅ 已解绑玩家 {target_player} 的QQ绑定 (原QQ: {original_qq})"
                else:
                    reply = f"❌ 解绑失败：操作过程中出现错误"
            elif not reply:  # 如果还没有设置错误消息
                reply = f"❌ 解绑失败：未找到有效的绑定记录"
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
                reply = "❌ 封禁功能已禁用：强制QQ绑定功能未启用"
            else:
                target_player = args[0]
                ban_reason = " ".join(args[1:]) if len(args) > 1 else "管理员封禁"
                
                # 检查玩家是否已经被封禁
                if _plugin_instance.is_player_banned(target_player):
                    reply = f"❌ 玩家 {target_player} 已经被封禁"
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
        # 如果插件实例未初始化，返回（无法记录日志）
        return
        
    access_token = _plugin_instance.get_config("access_token", "")
    napcat_ws = _plugin_instance.get_config("napcat_ws", "ws://localhost:3001")
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
    delay = 1
    startup_msg = f"{ColorFormat.BLUE}QQsync 启动，准备连接 {ColorFormat.YELLOW}NapCat WS{ColorFormat.BLUE}…{ColorFormat.RESET}"
    _plugin_instance.logger.info(startup_msg)
    connect_msg = f"{ColorFormat.GOLD}连接地址: {ColorFormat.WHITE}{napcat_ws}{ColorFormat.RESET}"
    _plugin_instance.logger.info(connect_msg)
    
    consecutive_failures = 0
    max_consecutive_failures = 5
    
    while True:
        try:
            # 增加连接超时和ping间隔设置
            async with websockets.connect(
                napcat_ws, 
                additional_headers=headers,
                ping_interval=20,  # 20秒ping间隔
                ping_timeout=10,   # 10秒ping超时
                close_timeout=10   # 10秒关闭超时
            ) as ws:
                _current_ws = ws  # 设置全局websocket变量
                consecutive_failures = 0  # 重置失败计数
                
                success_msg = f"{ColorFormat.GREEN}✅ 已连接 {ColorFormat.YELLOW}NapCat WS{ColorFormat.RESET}"
                _plugin_instance.logger.info(success_msg)
                
                # 连接成功后立即获取群成员列表
                await get_group_member_list(ws, _plugin_instance.get_config("target_group"))
                
                # 处理待发送的验证码队列
                if hasattr(_plugin_instance, '_verification_send_queue') and _plugin_instance._verification_send_queue:
                    queue_count = len(_plugin_instance._verification_send_queue)
                    _plugin_instance.logger.info(f"WebSocket重连后，发现{queue_count}个待发送验证码，将重新处理")
                
                await asyncio.gather(
                    heartbeat(ws),
                    message_loop(ws)
                )
        except Exception as e:
            _current_ws = None  # 连接断开时清空
            consecutive_failures += 1
            
            # 根据连续失败次数调整重连策略
            if consecutive_failures <= max_consecutive_failures:
                error_msg = f"{ColorFormat.RED}❌ NapCat WS 连接失败 (尝试{consecutive_failures}/{max_consecutive_failures}): {ColorFormat.YELLOW}{e}{ColorFormat.RESET}"
                _plugin_instance.logger.warning(error_msg)
                
                # 指数退避，但最大不超过30秒
                delay = min(30, delay * 1.5 if consecutive_failures > 1 else 5)
            else:
                error_msg = f"{ColorFormat.RED}❌ NapCat WS 连接持续失败，暂停重连30秒: {ColorFormat.YELLOW}{e}{ColorFormat.RESET}"
                _plugin_instance.logger.error(error_msg)
                delay = 30  # 持续失败时使用固定延迟
                consecutive_failures = 0  # 重置计数，给系统恢复机会
            
            retry_msg = f"{ColorFormat.AQUA}🔄 将在 {delay:.1f} 秒后重试连接...{ColorFormat.RESET}"
            _plugin_instance.logger.info(retry_msg)
            await asyncio.sleep(delay)
        else:
            delay = 1

async def heartbeat(ws):
    while True:
        await ws.send("{}")
        await asyncio.sleep(30)

async def handle_api_response(data: dict):
    """处理API响应"""
    global _verification_messages
    
    try:
        # 处理发送消息响应（获取消息ID用于撤回）
        if (data.get("data") and isinstance(data["data"], dict) and 
            "message_id" in data["data"]):
            message_id = data["data"]["message_id"]
            echo = data.get("echo", "")
            
            # 检查是否为验证码消息
            if echo.startswith("verification_msg:"):
                qq_number = echo.split(":", 1)[1]
                
                # 获取对应的玩家名
                player_name = ""
                if qq_number in _verification_codes:
                    player_name = _verification_codes[qq_number].get("player_name", "")
                
                _verification_messages[qq_number] = {
                    "message_id": message_id,
                    "timestamp": time.time(),
                    "player_name": player_name
                }
                if _plugin_instance:
                    _plugin_instance.logger.info(f"{ColorFormat.GREEN}已记录验证码消息ID: {message_id} (QQ: {qq_number}){ColorFormat.RESET}")
            
        # 处理群成员列表响应
        elif data.get("data") and isinstance(data["data"], list):
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

async def _verify_data_consistency():
    """验证数据一致性和时间记录完整性"""
    try:
        current_time = datetime.datetime.now()
        inconsistencies = []
        
        # 检查_pending_verifications和_verification_codes的一致性
        for player_name, pending_info in list(_pending_verifications.items()):
            qq_number = pending_info.get("qq")
            if qq_number not in _verification_codes:
                inconsistencies.append(f"Player {player_name} in pending but not in codes")
                continue
                
            code_info = _verification_codes[qq_number]
            
            # 检查时间记录完整性
            if "creation_time" not in pending_info or "creation_time" not in code_info:
                inconsistencies.append(f"Missing creation_time for {player_name}")
                continue
                
            # 检查时间是否超过60秒
            time_diff = current_time - pending_info["creation_time"]
            if time_diff.total_seconds() > 60:
                inconsistencies.append(f"Expired verification for {player_name} ({time_diff.total_seconds():.1f}s)")
                
                # 清理过期数据并撤回消息
                del _pending_verifications[player_name]
                del _verification_codes[qq_number]
                
                if _current_ws:
                    await delete_verification_message(qq_number)
        
        # 记录发现的不一致性
        if inconsistencies and _plugin_instance:
            _plugin_instance.logger.warning(f"数据一致性检查发现 {len(inconsistencies)} 个问题: {', '.join(inconsistencies[:3])}")
            
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"数据一致性验证失败: {e}")