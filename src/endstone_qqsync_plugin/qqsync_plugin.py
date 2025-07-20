from endstone.plugin import Plugin
from endstone import ColorFormat
from endstone.event import (
    event_handler,
    PlayerChatEvent,
    PlayerJoinEvent,
    PlayerQuitEvent,
    PlayerDeathEvent,
)

import asyncio
import threading
import json
from pathlib import Path
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))
import websockets

_current_ws = None
_plugin_instance = None

class qqsync(Plugin):

    api_version = "0.6"
    
    def on_load(self) -> None:
        self.logger.info(f"{ColorFormat.BLUE}qqsync_plugin {ColorFormat.WHITE}正在加载...{ColorFormat.RESET}")
    
    def on_enable(self) -> None:
        global _plugin_instance
        _plugin_instance = self
        
        self._init_config()

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
            "help_msg": "🎮 QQsync群服互通 - 命令：\n\n📊 查询命令（所有用户可用）：\n/help — 显示本帮助信息\n/list — 查看在线玩家列表\n/version — 查看服务器版本\n/plugins — 查看插件列表\n/tps — 查看服务器性能指标\n/info — 查看服务器综合信息\n\n⚙️ 管理命令（仅管理员可用）：\n/cmd <命令> — 执行服务器命令\n/tog_qq — 切换QQ消息转发开关 \n/tog_game — 切换游戏转发开关\n/reload — 重新加载配置文件"
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
            elif key == "help_msg" and self._config[key] != value:
                # 特殊处理help_msg，当默认内容更新时也更新配置
                self._config[key] = value
                config_updated = True
                self.logger.info(f"更新配置项: {key}")
        
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
            reload_msg = f"{ColorFormat.GREEN}配置已重新加载{ColorFormat.RESET}"
            self.logger.info(reload_msg)
            return True
        except Exception as e:
            self.logger.error(f"重新加载配置失败: {e}")
            return False
        
    def _run_loop(self):
        """在新线程中运行事件循环"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def on_disable(self) -> None:
        shutdown_msg = f"{ColorFormat.RED}qqsync_plugin {ColorFormat.RED}卸载{ColorFormat.RESET}"
        self.logger.info(shutdown_msg)
        # 优雅关闭
        if hasattr(self, "_task"):
            self._task.cancel()
        if hasattr(self, "_loop"):
            self._loop.call_soon_threadsafe(self._loop.stop)
        if hasattr(self, "_thread"):
            self._thread.join(timeout=2)

    @event_handler
    def on_player_join(self, event: PlayerJoinEvent) -> None:
        if not self.get_config("enable_game_to_qq", True):
            return
        player_name = event.player.name
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
        if not self.get_config("enable_game_to_qq", True):
            return
        player_name = event.player.name
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
        if not self.get_config("enable_game_to_qq", True):
            return
        player_name = event.player.name
        message = event.message
        target_group = self.get_config("target_group")
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
        target_group = self.get_config("target_group")
        if _current_ws:
            asyncio.run_coroutine_threadsafe(
                send_group_msg(_current_ws, group_id=target_group, text=f"💀 {event.death_message}"),
                self._loop
            )



# ========= 工具 =========
def parse_qq_message(message_data):
    """
    解析QQ消息，将非文本内容转换为对应的标识符
    支持结构化消息和CQ码格式
    """
    import re
    
    # 获取原始消息文本
    raw_message = message_data.get("raw_message", "")
    
    # 首先尝试处理结构化消息
    message = message_data.get("message", [])
    
    if isinstance(message, list) and message:
        # 处理结构化消息
        processed_parts = []
        
        for msg_segment in message:
            if not isinstance(msg_segment, dict):
                continue
                
            msg_type = msg_segment.get("type", "")
            
            if msg_type == "text":
                # 文本消息直接添加
                text_content = msg_segment.get("data", {}).get("text", "")
                if text_content.strip():
                    processed_parts.append(text_content)
                    
            elif msg_type == "image":
                # 图片消息
                processed_parts.append("[图片]")
                
            elif msg_type == "video":
                # 视频消息
                processed_parts.append("[视频]")
                
            elif msg_type == "record":
                # 语音消息
                processed_parts.append("[语音]")
                
            elif msg_type == "face":
                # QQ表情
                processed_parts.append("[表情]")
                
            elif msg_type == "at":
                # @某人
                at_qq = msg_segment.get("data", {}).get("qq", "")
                if at_qq == "all":
                    processed_parts.append("@全体成员")
                else:
                    processed_parts.append(f"@{at_qq}")
                    
            elif msg_type == "reply":
                # 回复消息
                processed_parts.append("[回复]")
                
            elif msg_type == "forward":
                # 转发消息
                processed_parts.append("[转发]")
                
            elif msg_type == "file":
                # 文件
                processed_parts.append("[文件]")
                
            elif msg_type == "share":
                # 分享链接
                processed_parts.append("[分享]")
                
            elif msg_type == "location":
                # 位置分享
                processed_parts.append("[位置]")
                
            elif msg_type == "music":
                # 音乐分享
                processed_parts.append("[音乐]")
                
            elif msg_type == "xml" or msg_type == "json":
                # 卡片消息
                processed_parts.append("[卡片]")
                
            else:
                # 其他未知类型
                processed_parts.append("[非文本]")
        
        if processed_parts:
            return "".join(processed_parts)
    
    # 如果结构化消息处理失败或为空，则处理CQ码格式
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
            # 格式化消息并在主线程中广播到游戏内
            game_msg = f"{ColorFormat.LIGHT_PURPLE}[QQ群]{ColorFormat.RESET} {ColorFormat.AQUA}{nickname}{ColorFormat.RESET}: {processed_msg}"
            # 使用调度器在主线程中执行广播
            _plugin_instance.server.scheduler.run_task(
                _plugin_instance, 
                lambda: _plugin_instance.server.broadcast_message(game_msg)
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
        reply = _plugin_instance.get_config("help_msg", "暂无帮助信息")

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

    elif cmd == "version" and len(cmd_parts) == 1:
        if _plugin_instance:
            server_version = _plugin_instance.server.version
            minecraft_version = _plugin_instance.server.minecraft_version
            reply = f"服务器版本信息：\nEndstone: {server_version}\nMinecraft: {minecraft_version}"
        else:
            reply = "插件未正确初始化"

    elif cmd == "plugins" and len(cmd_parts) == 1:
        if _plugin_instance:
            try:
                all_plugins = _plugin_instance.server.plugin_manager.plugins
                plugin_info_list = []
                for plugin in all_plugins:
                    if plugin:
                        try:
                            # 首先尝试使用 PluginDescription（如果存在）
                            desc = plugin.description
                            if desc is not None:
                                plugin_name = desc.name if hasattr(desc, 'name') and desc.name else None
                                plugin_desc = desc.description if hasattr(desc, 'description') and desc.description else None
                                plugin_version = desc.version if hasattr(desc, 'version') and desc.version else None
                                
                                # 获取作者信息 - authors 是字符串列表
                                authors_str = None
                                if hasattr(desc, 'authors') and desc.authors:
                                    if isinstance(desc.authors, list):
                                        authors_str = ", ".join(desc.authors)
                                    elif isinstance(desc.authors, str):
                                        authors_str = desc.authors
                                
                                # 获取网站信息（可选）
                                website = ""
                                if hasattr(desc, 'website') and desc.website:
                                    website = f"\n   🌐 网站: {desc.website}"
                                
                                # 如果有完整信息，格式化详细插件信息
                                if plugin_name and plugin_desc and plugin_version and authors_str:
                                    plugin_info = f"📦 {plugin_name} v{plugin_version}\n   📝 {plugin_desc}\n   👤 作者: {authors_str}{website}"
                                    plugin_info_list.append(plugin_info)
                                else:
                                    # 信息不完整，降级到简单格式
                                    if plugin_name:
                                        plugin_info_list.append(f"📦 {plugin_name}")
                                    else:
                                        raise Exception("无法获取插件名称")
                            else:
                                # description 为 None，使用 Plugin 类的属性
                                plugin_name = plugin.name if hasattr(plugin, 'name') and plugin.name else None
                                plugin_version = plugin.version if hasattr(plugin, 'version') and plugin.version else None
                                plugin_desc = plugin.description if hasattr(plugin, 'description') and plugin.description else None
                                
                                # 获取作者信息
                                authors_str = None
                                if hasattr(plugin, 'authors') and plugin.authors:
                                    if isinstance(plugin.authors, list):
                                        authors_str = ", ".join(plugin.authors)
                                    elif isinstance(plugin.authors, str):
                                        authors_str = plugin.authors
                                
                                # 获取网站信息
                                website = ""
                                if hasattr(plugin, 'website') and plugin.website:
                                    website = f"\n   🌐 网站: {plugin.website}"
                                
                                # 如果有完整信息，格式化详细插件信息
                                if plugin_name and plugin_desc and plugin_version and authors_str:
                                    plugin_info = f"📦 {plugin_name} v{plugin_version}\n   📝 {plugin_desc}\n   👤 作者: {authors_str}{website}"
                                    plugin_info_list.append(plugin_info)
                                elif plugin_name:
                                    # 只有名称，使用简单格式
                                    plugin_info_list.append(f"📦 {plugin_name}")
                                else:
                                    # 使用类名作为备用方案
                                    raise Exception("无法获取插件名称")
                            
                        except Exception as e:
                            # 降级到基本信息 - 只显示类名
                            try:
                                plugin_name = plugin.__class__.__name__
                                if plugin_name.endswith('Plugin'):
                                    plugin_name = plugin_name[:-6]
                                plugin_info_list.append(f"📦 {plugin_name}")
                            except:
                                plugin_info_list.append("📦 未知插件")
                
                if plugin_info_list:
                    reply = f"已加载插件 ({len(plugin_info_list)})：\n\n" + "\n\n".join(plugin_info_list)
                else:
                    reply = "没有加载任何插件"
            except Exception as e:
                _plugin_instance.logger.error(f"获取插件列表时出错: {e}")
                reply = f"获取插件列表失败: {e}"
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
                reply = f"✅ 命令已执行: {server_cmd}"
            except Exception as e:
                reply = f"❌ 执行出错：{e}"
        else:
            reply = "该命令仅限管理员使用"
    
    elif cmd == "cmd" and len(args) == 0:
        reply = "用法：/cmd <服务器命令>"

    elif cmd == "reload" and len(cmd_parts) == 1:
        admins = _plugin_instance.get_config("admins", [])
        if not admins or str(user_id) in admins:
            if _plugin_instance.reload_config():
                reply = "✅ 配置文件已重新加载"
            else:
                reply = "❌ 配置文件重新加载失败"
        else:
            reply = "该命令仅限管理员使用"

    elif cmd == "tog_qq" and len(cmd_parts) == 1:
        admins = _plugin_instance.get_config("admins", [])
        if not admins or str(user_id) in admins:
            current_state = _plugin_instance.get_config("enable_qq_to_game", True)
            _plugin_instance._config["enable_qq_to_game"] = not current_state
            _plugin_instance.save_config()
            status = "启用" if not current_state else "禁用"
            icon = "✅" if not current_state else "❌"
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
            icon = "✅" if not current_state else "❌"
            reply = f"{icon} 游戏消息转发已{status}"
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

async def message_loop(ws):
    async for raw in ws:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if data.get("post_type") != "message":
            continue
        if data.get("message_type") != "group":
            continue
        
        # 确保插件实例已初始化
        if not _plugin_instance:
            print("插件实例未初始化，跳过消息处理")
            continue
            
        target_group = _plugin_instance.get_config("target_group")
        if data.get("group_id") != target_group:
            continue
        await handle_message(ws, data)