import asyncio
import threading
import time
from pathlib import Path

from endstone.plugin import Plugin
from endstone import ColorFormat

# 导入核心模块
from .core import (
    ConfigManager,
    DataManager, 
    VerificationManager,
    PermissionManager,
    EventHandlers
)
from .websocket import WebSocketClient
from .websocket.handlers import set_plugin_instance, send_group_msg_to_all_groups
from .ui import UIManager
from .utils.time_utils import TimeUtils
from .utils.message_utils import parse_qq_message


class qqsync(Plugin):
    """QQsync群服互通插件主类"""

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
        
        # 初始化时间系统
        self.logger.info(f"{ColorFormat.YELLOW}正在初始化时间系统...{ColorFormat.RESET}")
        time_init_result = TimeUtils.initialize_time_system()
        
        if time_init_result.get('success', True):
            if not time_init_result.get('local_time_accurate', True):
                self.logger.info(f"{ColorFormat.YELLOW}⚠️ 本地时间不准确，将使用网络时间同步{ColorFormat.RESET}")
        else:
            self.logger.warning(f"{ColorFormat.RED}⚠️ 时间系统初始化失败，使用默认本地时间{ColorFormat.RESET}")
            
        self.logger.info(f"{ColorFormat.BLUE}时间系统初始化完成{ColorFormat.RESET}")

    def on_enable(self) -> None:
        """插件启用"""
        try:
            # 初始化管理器
            self._init_managers()
            
            # 初始化WebSocket相关
            self._init_websocket()
            
            # 设置启动消息标志
            self._send_startup_message = True
            
            # 注册事件处理器
            self.register_events(self.event_handlers)
            
            # 启动定时任务
            self._schedule_tasks()
            
            # 设置全局插件实例引用
            set_plugin_instance(self)

            # 启动消息
            startup_msg = f"{ColorFormat.GREEN}qqsync_plugin {ColorFormat.YELLOW}已启用 (重构版本){ColorFormat.RESET}"
            self.logger.info(startup_msg)
            welcome_msg = f"{ColorFormat.BLUE}欢迎使用QQsync群服互通插件，{ColorFormat.YELLOW}作者：yuexps{ColorFormat.RESET}"
            self.logger.info(welcome_msg)
            
        except Exception as e:
            self.logger.error(f"插件启用失败: {e}")
            raise

    def _init_managers(self):
        """初始化各种管理器"""
        # 配置管理器
        self.config_manager = ConfigManager(Path(self.data_folder), self.logger)
        
        # 数据管理器
        self.data_manager = DataManager(Path(self.data_folder), self.logger)
        
        # 验证管理器
        self.verification_manager = VerificationManager(self, self.logger)
        
        # 权限管理器
        self.permission_manager = PermissionManager(self, self.logger)
        
        # 事件处理器
        self.event_handlers = EventHandlers(self)
        
        # UI管理器
        self.ui_manager = UIManager(self)
        
        # 群成员缓存
        self.group_members = set()
        self.logged_left_players = set()
        
        self.logger.info(f"{ColorFormat.AQUA}管理器初始化完成{ColorFormat.RESET}")

    def _init_websocket(self):
        """初始化WebSocket连接"""
        # 确保没有重复的WebSocket客户端
        if hasattr(self, 'ws_client') and self.ws_client:
            self.logger.warning("NapCat WS 客户端已存在，停止旧实例")
            self.ws_client.stop()
        
        # WebSocket客户端
        self.ws_client = WebSocketClient(self)
        
        # WebSocket连接引用
        self._current_ws = None
        
        # 确保没有重复的事件循环
        if hasattr(self, '_loop') and self._loop:
            self.logger.warning("检测到旧的事件循环，正在清理")
            self._loop.call_soon_threadsafe(self._loop.stop)
        
        # 创建专用事件循环
        self._loop = asyncio.new_event_loop()
        
        # 在新线程里启动该循环
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        # 把协程提交到该循环
        future = asyncio.run_coroutine_threadsafe(self.ws_client.connect_forever(), self._loop)
        self._task = future

    def _run_loop(self):
        """运行异步事件循环"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _schedule_tasks(self):
        """安排定时任务"""
        # 定时清理任务
        self.server.scheduler.run_task(
            self,
            self._cleanup_expired_data,
            delay=1200,   # 首次执行延迟1分钟 (60秒 × 20tick/秒)
            period=6000   # 每5分钟执行一次 (300秒 × 20tick/秒)
        )
        
        # 群成员检查任务
        self.server.scheduler.run_task(
            self,
            self._update_group_members,
            delay=1200,    # 首次执行延迟1分钟 (60秒 × 20tick/秒)
            period=72000   # 每1小时执行一次 (3600秒 × 20tick/秒)
        )
        
        # 验证码发送队列处理任务
        self.server.scheduler.run_task(
            self,
            self.verification_manager.process_verification_send_queue,
            delay=60,     # 3秒后首次执行 (3秒 × 20tick/秒)
            period=60    # 每3秒检查一次队列 (3秒 × 20tick/秒)
        )
        
        # 验证码清理任务
        self.server.scheduler.run_task(
            self,
            self.verification_manager.cleanup_expired_verifications,
            delay=600,    # 30秒后首次执行 (30秒 × 20tick/秒)
            period=1200   # 每1分钟检查一次过期验证码 (60秒 × 20tick/秒)
        )
        
        # 在线时长计时器任务
        self.server.scheduler.run_task(
            self,
            self._update_online_playtime_timers,
            delay=1200,   # 30秒后首次执行 (30秒 × 20tick/秒)
            period=1200   # 每1分钟更新一次 (60秒 × 20tick/秒)
        )

    def _cleanup_expired_data(self):
        """清理过期数据"""
        try:
            # 清理验证相关的过期数据
            self.verification_manager.cleanup_expired_verifications()
            
            # 清理其他过期缓存
            current_time = TimeUtils.get_timestamp()
            
            # 清理离线玩家的权限附件缓存（超过1小时）
            online_players = {player.name for player in self.server.online_players}
            offline_players = []
            
            for player_name in list(self.permission_manager.player_attachments.keys()):
                if player_name not in online_players:
                    offline_players.append(player_name)
            
            for player_name in offline_players:
                self.permission_manager.cleanup_player_permissions(player_name)
                self.verification_manager.cleanup_player_data(player_name)
            
            if offline_players:
                self.logger.info(f"已清理 {len(offline_players)} 个离线玩家的缓存数据")
            
        except Exception as e:
            self.logger.error(f"清理过期数据失败: {e}")

    def _update_group_members(self):
        """更新群成员缓存"""
        try:
            # 只有在启用强制绑定和退群检测时才更新群成员缓存
            if not (self.config_manager.get_config("force_bind_qq", True) and 
                    self.config_manager.get_config("check_group_member", True)):
                return
                
            if self._current_ws:
                from .websocket.handlers import get_all_groups_member_list
                asyncio.run_coroutine_threadsafe(
                    get_all_groups_member_list(self._current_ws),
                    self._loop
                )
            else:
                # 只在首次启动时使用info级别，运行中断开时使用warning级别
                if not self.group_members:
                    self.logger.info("系统正在连接QQ服务，群成员缓存更新稍后将自动执行")
                else:
                    self.logger.warning("QQ服务连接断开，群成员缓存更新暂时不可用")
        except Exception as e:
            self.logger.error(f"更新群成员缓存失败: {e}")

    def _update_online_playtime_timers(self):
        """更新在线玩家的游戏时长计时器"""
        try:
            # 获取当前在线玩家列表
            online_players = list(self.server.online_players)
            
            # 更新计时器
            self.data_manager.update_online_timers(online_players)
            
        except Exception as e:
            self.logger.error(f"更新在线时长计时器失败: {e}")

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
            if self.data_manager.is_player_bound(player_name, player.xuid):
                player_qq = self.data_manager.get_player_qq(player_name)
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}您的QQ绑定状态：{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}已绑定QQ: {player_qq}{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如需重新绑定，请联系管理员{ColorFormat.RESET}")
            else:
                # 玩家未绑定，显示绑定表单
                if self.config_manager.get_config("force_bind_qq", True):
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}您尚未绑定QQ，正在为您显示绑定表单...{ColorFormat.RESET}")
                    # 延迟显示表单，确保消息先发送
                    self.server.scheduler.run_task(
                        self,
                        lambda p=player: self.ui_manager.show_qq_binding_form(p) if self.is_valid_player(p) else None,
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

    def is_valid_player(self, player) -> bool:
        """检查玩家对象是否有效且在线"""
        try:
            return (player and 
                    hasattr(player, "send_message") and 
                    hasattr(player, "name") and 
                    hasattr(player, "xuid") and
                    getattr(player, "is_online", True))
        except Exception:
            return False
        
    def api_send_message(self, text: str) -> bool:
        """
        QQ消息API
        """
        api_qq_enabled = self.config_manager.get_config("api_qq_enable", False)
        if api_qq_enabled:
            try:
                asyncio.run_coroutine_threadsafe(
                    send_group_msg_to_all_groups(self._current_ws, text=text),
                    self._loop
                )

                # 不等待结果，立即返回成功
                return True
                
            except Exception:
                return False
        else:
            self.logger.warning("QQ消息API功能未启用！")

    def on_disable(self) -> None:
        """插件禁用"""
        try:
            self.logger.info("正在禁用插件...")
            
            # 发送服务器停止消息（在停止WebSocket连接之前）
            if (hasattr(self, '_current_ws') and self._current_ws and 
                hasattr(self, '_loop') and self._loop and 
                not self._loop.is_closed() and self._loop.is_running()):
                try:
                    server_end_msg = "[QQSync] 服务器已停止！"
                    future = asyncio.run_coroutine_threadsafe(
                        send_group_msg_to_all_groups(self._current_ws, server_end_msg),
                        self._loop
                    )
                    # 等待消息发送完成，但设置超时
                    future.result(timeout=3)
                    self.logger.info("服务器停止消息已发送")
                except Exception as msg_error:
                    self.logger.warning(f"发送关闭消息失败（这是正常的）: {msg_error}")
            else:
                self.logger.warning("NapCat WS 连接不可用，跳过关闭消息发送")

            # 保存数据
            if hasattr(self, 'data_manager'):
                # 清理计时器系统
                self.data_manager.cleanup_timer_system()
                # 保存最终数据
                self.data_manager.save_data()
            
            # 停止WebSocket连接
            if hasattr(self, 'ws_client') and self.ws_client:
                self.ws_client.stop()
            
            # 停止事件循环
            if hasattr(self, '_loop') and self._loop:
                try:
                    if not self._loop.is_closed():
                        if self._loop.is_running():
                            self._loop.call_soon_threadsafe(self._loop.stop)
                        else:
                            self.logger.info("事件循环已停止，无需再次停止")
                    else:
                        self.logger.info("事件循环已关闭")
                except Exception as loop_error:
                    self.logger.warning(f"停止事件循环时出错: {loop_error}")

                # 等待线程结束
                if hasattr(self, '_thread') and self._thread and self._thread.is_alive():
                    try:
                        self._thread.join(timeout=5)
                        if self._thread.is_alive():
                            self.logger.warning("事件循环线程未能在5秒内正常结束")
                    except Exception as thread_error:
                        self.logger.warning(f"等待线程结束时出错: {thread_error}")

            self.logger.info(f"{ColorFormat.YELLOW}qqsync_plugin 已禁用{ColorFormat.RESET}")
            
        except Exception as e:
            self.logger.error(f"插件禁用过程中出错: {e}")
            # 确保在任何情况下都尝试保存数据
            try:
                if hasattr(self, 'data_manager'):
                    self.data_manager.save_data()
            except Exception as save_error:
                self.logger.error(f"保存数据失败: {save_error}")