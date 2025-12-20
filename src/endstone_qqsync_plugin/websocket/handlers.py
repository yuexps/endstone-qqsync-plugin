"""
WebSocket消息处理函数
"""

import asyncio
import json
import time
import re
import datetime
from typing import Dict, Any, List
from endstone import ColorFormat
from ..utils.time_utils import TimeUtils
from endstone.command import CommandSenderWrapper
from endstone.lang import Language,Translatable
import queue
import html


# 全局变量引用
_plugin_instance = None
_current_ws = None
_verification_messages = {}


def set_plugin_instance(plugin):
    """设置插件实例引用"""
    global _plugin_instance
    _plugin_instance = plugin


async def send_group_msg(ws, group_id: int, text: str):
    """发送群消息 - OneBot V11 API"""
    try:
        payload = {
            "action": "send_group_msg",
            "params": {
                "group_id": group_id,
                "message": text
            },
            "echo": f"send_group_msg_{int(TimeUtils.get_timestamp())}"
        }
        await ws.send(json.dumps(payload))
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"发送群消息失败: {e}")


async def send_group_msg_to_all_groups(ws, text: str):
    """向所有配置的群组发送消息"""
    try:
        target_groups = _plugin_instance.config_manager.get_config("target_groups", [])
        # 添加类型转换，确保group_id为整数类型
        target_groups = [int(gid) for gid in target_groups]
        for group_id in target_groups:
            await send_group_msg(ws, group_id, text)
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"向所有群组发送消息失败: {e}")


async def send_group_msg_with_at(ws, group_id: int, user_id: int, text: str):
    """发送@用户的群消息 - OneBot V11 API"""
    try:
        payload = {
            "action": "send_group_msg",
            "params": {
                "group_id": group_id,
                "message": [
                    {"type": "at", "data": {"qq": str(user_id)}},
                    {"type": "text", "data": {"text": f" {text}"}}
                ]
            },
            "echo": f"bind_success_msg_{int(TimeUtils.get_timestamp())}"
        }
        await ws.send(json.dumps(payload))
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"发送@消息失败: {e}")


async def send_group_at_msg(ws, group_id: int, user_id: int, text: str, verification_qq: str = None):
    """发送@消息（用于验证码）- OneBot V11 API"""
    try:
        global _verification_messages
        
        echo_value = f"verification_msg:{verification_qq}" if verification_qq else f"at_msg_{int(TimeUtils.get_timestamp())}"
        payload = {
            "action": "send_group_msg", 
            "params": {
                "group_id": group_id,
                "message": [
                    {"type": "at", "data": {"qq": str(user_id)}},
                    {"type": "text", "data": {"text": f" {text}"}}
                ]
            },
            "echo": echo_value
        }
        
        # 如果是验证码消息，为兼容性创建handlers记录
        if verification_qq:
            _verification_messages[verification_qq] = {
                "echo": echo_value,
                "message_id": None,
                "timestamp": TimeUtils.get_timestamp()
            }
            if _plugin_instance:
                _plugin_instance.logger.debug(f"为QQ {verification_qq} 创建handlers验证码消息记录，echo: {echo_value}")
        
        await ws.send(json.dumps(payload))
        
        # 设置紧急撤回任务（90秒后）
        if verification_qq:
            async def emergency_retract():
                await asyncio.sleep(90)  # 90秒
                try:
                    # 通过 verification_manager 统一管理验证码撤回
                    if hasattr(_plugin_instance, 'verification_manager'):
                        await _plugin_instance.verification_manager.delete_verification_message_by_qq(verification_qq)
                except Exception as e:
                    if _plugin_instance:
                        _plugin_instance.logger.warning(f"紧急撤回验证码失败: {e}")
            
            asyncio.create_task(emergency_retract())
        
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"发送@消息失败: {e}")


async def delete_verification_message(qq_number: str, retry_count: int = 0):
    """删除验证码消息"""
    global _verification_messages
    
    try:
        # 优先使用verification_manager的方法
        if (_plugin_instance and 
            hasattr(_plugin_instance, 'verification_manager') and 
            hasattr(_plugin_instance.verification_manager, 'delete_verification_message_by_qq')):
            await _plugin_instance.verification_manager.delete_verification_message_by_qq(qq_number)
            return
        
        # 回退到handlers的实现（兼容性）
        if qq_number not in _verification_messages:
            return
        
        message_info = _verification_messages[qq_number]
        message_id = message_info.get("message_id")
        
        if not message_id:
            if _plugin_instance:
                _plugin_instance.logger.debug(f"QQ {qq_number} 的验证码消息ID未获取到，无法撤回")
            return
        
        # 发送撤回请求
        if _plugin_instance and _plugin_instance._current_ws:
            await delete_msg(_plugin_instance._current_ws, message_id)
        
        # 清理记录
        del _verification_messages[qq_number]
        
        if _plugin_instance:
            _plugin_instance.logger.debug(f"已撤回QQ {qq_number} 的验证码消息")
            
    except Exception as e:
        if retry_count < 2:  # 最多重试2次
            if _plugin_instance:
                _plugin_instance.logger.warning(f"撤回验证码消息失败，准备重试: {e}")
            await asyncio.sleep(1)
            await delete_verification_message(qq_number, retry_count + 1)
        else:
            if _plugin_instance:
                _plugin_instance.logger.error(f"撤回验证码消息失败（重试{retry_count}次后放弃）: {e}")


async def delete_msg(ws, message_id: int):
    """删除消息 - OneBot V11 API"""
    try:
        payload = {
            "action": "delete_msg",
            "params": {
                "message_id": message_id
            },
            "echo": f"delete_msg_{int(TimeUtils.get_timestamp())}"
        }
        await ws.send(json.dumps(payload))
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"删除消息失败: {e}")


async def set_group_card(ws, group_id: int, user_id: int, card: str):
    """设置群昵称 - OneBot V11 API"""
    try:
        payload = {
            "action": "set_group_card",
            "params": {
                "group_id": group_id,
                "user_id": user_id,
                "card": card
            },
            "echo": f"set_group_card_{int(TimeUtils.get_timestamp())}"
        }
        if _plugin_instance:
            _plugin_instance.logger.info(f"🏷️ 尝试设置群昵称: QQ={user_id}, 群={group_id}, 昵称='{card}'")
        await ws.send(json.dumps(payload))
    except Exception as e:
        # 让异常向上传播，由调用者(verification_manager)处理日志
        raise e


async def set_group_card_in_all_groups(ws, user_id: int, card: str):
    """在所有配置的群组中设置群昵称"""
    try:
        target_groups = _plugin_instance.config_manager.get_config("target_groups", [])
        # 添加类型转换，确保group_id为整数类型
        target_groups = [int(gid) for gid in target_groups]
        for group_id in target_groups:
            await set_group_card(ws, group_id, user_id, card)
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"在所有群组中设置群昵称失败: {e}")


async def get_group_member_list(ws, group_id: int):
    """获取群成员列表 - OneBot V11 API"""
    try:
        payload = {
            "action": "get_group_member_list",
            "params": {
                "group_id": group_id
            },
            "echo": f"get_group_member_list_{int(TimeUtils.get_timestamp())}"
        }
        await ws.send(json.dumps(payload))
        if _plugin_instance:
            _plugin_instance.logger.debug(f"已发送OneBot V11群成员列表请求: 群{group_id}")
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"获取群成员列表失败: {e}")


async def get_all_groups_member_list(ws):
    """获取所有配置群组的成员列表"""
    try:
        target_groups = _plugin_instance.config_manager.get_config("target_groups", [])
        # 添加类型转换，确保group_id为整数类型
        target_groups = [int(gid) for gid in target_groups]
        for group_id in target_groups:
            await get_group_member_list(ws, group_id)
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"获取所有群组成员列表失败: {e}")


async def handle_message(ws, data: dict):
    """处理接收到的消息"""
    try:
        # 只处理群消息
        if data.get("message_type") != "group":
            return
            
        group_id = data.get("group_id")
        user_id = data.get("user_id")
        raw_message = data.get("raw_message", "")
        sender = data.get("sender", {})
        nickname = sender.get("nickname", "未知")
        card = sender.get("card", "")
        
        # 添加日志记录，确保群聊消息在控制台打印出来
        if _plugin_instance:
            _plugin_instance.logger.info(f"[MSG] [群ID: {group_id}] [QQ: {user_id}] [昵称: {card if card else nickname}] - 内容: {raw_message}")
        
        if not _plugin_instance:
            return
        
        # 先检查是否是目标群组，避免不必要的数据库查询
        target_groups = _plugin_instance.config_manager.get_config("target_groups", [])
        # 确保target_groups中的元素都是整数类型，与group_id保持一致
        target_groups = [int(gid) for gid in target_groups]
        if group_id not in target_groups:
            return
        
        # 检查用户是否已绑定QQ，如果已绑定则使用玩家游戏ID
        bound_player = _plugin_instance.data_manager.get_qq_player(str(user_id))
        if bound_player:
            display_name = bound_player  # 使用玩家游戏ID作为显示名
        else:
            display_name = card if card else nickname  # 使用QQ群昵称或QQ昵称
        
        # 处理验证码
        if raw_message.isdigit() and len(raw_message) == 6:
            await _handle_verification_code(user_id, raw_message, display_name)
            return
        
        # 处理群内命令（包括管理员和普通用户命令）
        if raw_message.startswith("/"):
            await _handle_group_command(ws, user_id, raw_message, display_name, group_id)
            return
        
        # 转发消息到游戏
        if _plugin_instance.config_manager.get_config("enable_qq_to_game", True):
            await _forward_message_to_game(data, display_name)
            
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"处理群消息失败: {e}")


async def _handle_verification_code(user_id: int, code: str, display_name: str):
    """处理验证码"""
    try:
        qq_str = str(user_id)
        
        # 检查是否是有效的验证码
        if qq_str not in _plugin_instance.verification_manager.verification_codes:
            return
        
        verification_info = _plugin_instance.verification_manager.verification_codes[qq_str]
        player_name = verification_info.get("player_name")
        
        if not player_name:
            return
        
        # 查找对应的在线玩家
        target_player = None
        for player in _plugin_instance.server.online_players:
            if player.name == player_name:
                target_player = player
                break
        
        if not target_player:
            _plugin_instance.logger.debug(f"玩家 {player_name} 不在线，跳过QQ验证码处理")
            return
        
        # 验证验证码
        success, message, pending_info = _plugin_instance.verification_manager.verify_code(
            player_name, target_player.xuid, code, "qq"
        )
        
        if success:
            # 绑定成功 - 数据绑定由 data_manager 处理
            _plugin_instance.data_manager.bind_player_qq(player_name, target_player.xuid, qq_str)
            
            # 注意：验证数据清理、验证码撤回和成功播报已在 verification_manager.verify_code() 中统一处理
            
            # 通知玩家 - 使用调度器确保在主线程执行
            def notify_player():
                """在主线程中通知玩家绑定成功"""
                try:
                    if _plugin_instance.is_valid_player(target_player):
                        target_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] QQ绑定成功！{ColorFormat.RESET}")
                        target_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您的QQ {qq_str} 已与游戏账号绑定{ColorFormat.RESET}")
                except Exception as e:
                    if _plugin_instance:
                        _plugin_instance.logger.error(f"通知玩家绑定成功失败: {e}")
            
            # 使用调度器在主线程执行通知
            _plugin_instance.server.scheduler.run_task(_plugin_instance, notify_player, delay=1)
            
            # 恢复玩家权限
            if _plugin_instance.config_manager.get_config("force_bind_qq", True):
                _plugin_instance.server.scheduler.run_task(
                    _plugin_instance,
                    lambda: _plugin_instance.permission_manager.restore_player_permissions(target_player),
                    delay=2
                )
            
            _plugin_instance.logger.info(f"QQ验证成功: 玩家 {player_name} (QQ: {qq_str}) 通过群内验证")
        else:
            # 验证失败，发送错误消息到群
            if _plugin_instance._current_ws:
                target_groups = _plugin_instance.config_manager.get_config("target_groups", [])
                # 添加类型转换，确保group_id为整数类型
                target_groups = [int(gid) for gid in target_groups]
                for group_id in target_groups:
                    await send_group_msg(_plugin_instance._current_ws, group_id=group_id, 
                                       text=f"@{display_name} {message}")
            
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"处理QQ验证码失败: {e}")


async def _handle_verification_code_with_feedback(ws, user_id: int, code: str, display_name: str, group_id: int):
    """处理验证码（带反馈）- 专用于/verify命令"""
    try:
        qq_str = str(user_id)
        
        # 检查是否是有效的验证码
        if qq_str not in _plugin_instance.verification_manager.verification_codes:
            await send_group_msg(ws, group_id, f"@{display_name} ❌ 您没有待验证的绑定请求\n💡 请先在游戏中使用 /bindqq 命令申请绑定")
            return False
        
        verification_info = _plugin_instance.verification_manager.verification_codes[qq_str]
        player_name = verification_info.get("player_name")
        
        if not player_name:
            await send_group_msg(ws, group_id, f"@{display_name} ❌ 验证信息异常，请重新申请绑定")
            return False
        
        # 查找对应的在线玩家
        target_player = None
        for player in _plugin_instance.server.online_players:
            if player.name == player_name:
                target_player = player
                break
        
        if not target_player:
            await send_group_msg(ws, group_id, f"@{display_name} ❌ 玩家 {player_name} 不在线\n💡 请确保对应的游戏角色在线后再验证")
            return False
        
        # 验证验证码
        success, message, pending_info = _plugin_instance.verification_manager.verify_code(
            player_name, target_player.xuid, code, "qq"
        )
        
        if success:
            # 绑定成功 - 数据绑定由 data_manager 处理
            _plugin_instance.data_manager.bind_player_qq(player_name, target_player.xuid, qq_str)
            
            # 注意：验证数据清理、验证码撤回和绑定成功播报已在 verification_manager.verify_code() 中统一处理
            
            # 通知玩家 - 使用调度器确保在主线程执行
            def notify_player():
                """在主线程中通知玩家绑定成功"""
                try:
                    if _plugin_instance.is_valid_player(target_player):
                        target_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] QQ绑定成功！{ColorFormat.RESET}")
                        target_player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您的QQ {qq_str} 已与游戏账号绑定{ColorFormat.RESET}")
                except Exception as e:
                    if _plugin_instance:
                        _plugin_instance.logger.error(f"通知玩家绑定成功失败: {e}")
            
            # 使用调度器在主线程执行通知
            _plugin_instance.server.scheduler.run_task(_plugin_instance, notify_player, delay=1)
            
            # 恢复玩家权限
            if _plugin_instance.config_manager.get_config("force_bind_qq", True):
                _plugin_instance.server.scheduler.run_task(
                    _plugin_instance,
                    lambda: _plugin_instance.permission_manager.restore_player_permissions(target_player),
                    delay=2
                )
            
            _plugin_instance.logger.info(f"QQ验证成功: 玩家 {player_name} (QQ: {qq_str}) 通过群内/verify命令验证")
            return True
        else:
            # 验证失败，发送错误消息到群
            await send_group_msg(ws, group_id, f"@{display_name} ❌ {message}")
            return False
            
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"处理/verify命令失败: {e}")
        await send_group_msg(ws, group_id, f"@{display_name} ❌ 验证过程发生错误，请稍后重试")
        return False


async def _handle_group_command(ws, user_id: int, raw_message: str, display_name: str, group_id: int):
    """处理群内命令"""
    try:
        # 解析命令
        cmd_parts = raw_message.strip().split()
        if not cmd_parts:
            return
        
        cmd = cmd_parts[0][1:] if cmd_parts[0].startswith('/') else cmd_parts[0]  # 去掉/前缀
        args = cmd_parts[1:] if len(cmd_parts) > 1 else []
        
        admins = _plugin_instance.config_manager.get_config("admins", [])
        is_admin = str(user_id) in admins
        
        reply = ""
        
        # /help 命令
        if cmd == "help":
            if is_admin:
                reply = _plugin_instance.config_manager.get_help_text_with_admin()
            else:
                reply = _plugin_instance.config_manager.get_help_text()
        
        # /list 命令 - 查看在线玩家列表
        elif cmd == "list":
            online_players = _plugin_instance.server.online_players
            if not online_players:
                reply = "🎮 当前没有玩家在线"
            else:
                player_list = []
                for player in online_players:
                    # 获取玩家延迟
                    try:
                        ping = player.ping
                        ping_display = f"{ping}ms"
                    except:
                        ping_display = "N/A"
                    
                    # 检查玩家绑定状态
                    bound_qq = _plugin_instance.data_manager.get_player_qq(player.name)
                    if bound_qq:
                        player_list.append(f"• {player.name} [{ping_display}]")
                    else:
                        player_list.append(f"• {player.name} [未绑定QQ]")
                
                reply = f"🎮 在线玩家 ({len(online_players)}/{_plugin_instance.server.max_players}):\n" + "\n".join(player_list)
        
        # /tps 命令 - 查看服务器性能
        elif cmd == "tps":
            try:
                current_tps = _plugin_instance.server.current_tps
                average_tps = _plugin_instance.server.average_tps
                current_mspt = _plugin_instance.server.current_mspt
                average_mspt = _plugin_instance.server.average_mspt
                current_tick_usage = _plugin_instance.server.current_tick_usage
                average_tick_usage = _plugin_instance.server.average_tick_usage
                
                reply = f"📊 服务器性能状态:\n"
                reply += f"• 当前TPS: {current_tps:.2f}/20.0"
                
                # TPS状态指示
                if current_tps >= 19.0:
                    reply += " ✅ 良好\n"
                elif current_tps >= 15.0:
                    reply += " ⚠️ 轻微延迟\n"
                else:
                    reply += " ❌ 严重延迟\n"
                
                reply += f"• 平均TPS: {average_tps:.2f}/20.0\n"
                reply += f"• 当前MSPT: {current_mspt:.2f}ms\n"
                reply += f"• 平均MSPT: {average_mspt:.2f}ms\n"
                reply += f"• 当前Tick使用率: {current_tick_usage:.1f}%\n"
                reply += f"• 平均Tick使用率: {average_tick_usage:.1f}%"
                
            except Exception as e:
                reply = "📊 无法获取服务器性能数据"
                if _plugin_instance:
                    _plugin_instance.logger.error(f"获取服务器性能数据失败: {e}")
        
        # /info 命令 - 查看服务器信息
        elif cmd == "info":
            try:
                from ..utils.time_utils import TimeUtils
                from ..utils.info import get_system_info_dict
                
                online_count = len(_plugin_instance.server.online_players)
                max_players = _plugin_instance.server.max_players
                server_name = _plugin_instance.server.name
                version = _plugin_instance.server.version
                minecraft_version = _plugin_instance.server.minecraft_version
                start_time = _plugin_instance.server.start_time
                
                # 获取插件统计信息
                total_bindings = len(_plugin_instance.data_manager.binding_data)
                
                # 使用时间工具模块获取当前时间和运行时长
                time_info = TimeUtils.get_current_time_info()
                uptime_info = TimeUtils.calculate_uptime(start_time)
                
                # 获取系统硬件信息
                system_info = get_system_info_dict()
                
                reply = f"ℹ️ 服务器信息:\n"

                # === 服务器基本信息 ===
                reply += f"• 服务器名称: {server_name}\n"
                reply += f"• Endstone版本: {version}\n"
                reply += f"• Minecraft版本: {minecraft_version}\n"
                reply += f"• 启动时间: {TimeUtils.format_datetime(start_time)}\n"
                reply += f"• 当前时间: {time_info['formatted_time']} ({time_info['source']})\n"
                reply += f"• 运行时长: {uptime_info['uptime_str']}\n"
                reply += f"• 在线玩家: {online_count}/{max_players}\n"
                reply += f"• 总绑定数: {total_bindings}\n"
                
                # === 系统硬件信息 ===
                reply += f"\n🖥️ 系统信息:\n"
                reply += f"• 操作系统: {system_info['os']}\n"
                
                # CPU信息
                cpu_info = system_info['cpu']
                cpu_model = cpu_info['model'][:50] + "..." if len(cpu_info['model']) > 50 else cpu_info['model']  # 限制长度
                reply += f"• CPU型号: {cpu_model}\n"
                
                if cpu_info['max_freq_ghz']:
                    reply += f"• CPU主频: {cpu_info['max_freq_ghz']:.2f}GHz"
                    if cpu_info['current_freq_ghz']:
                        reply += f" (当前: {cpu_info['current_freq_ghz']:.2f}GHz)"
                    reply += "\n"
                
                reply += f"• CPU核心: {cpu_info['physical_cores']}核{cpu_info['logical_cores']}线程\n"
                reply += f"• CPU使用率: {cpu_info['usage_percent']:.1f}%\n"
                
                # 内存信息
                mem_info = system_info['memory']
                reply += f"• 内存: {mem_info['used_gb']:.1f}GB / {mem_info['total_gb']:.1f}GB ({mem_info['percent']:.1f}%)\n"
                
                # 硬盘信息
                disk_info = system_info['disks']
                if disk_info:
                    for disk in disk_info:
                        if 'error' in disk:
                            continue
                        reply += f"• 硬盘({disk['device']}): {disk['used_gb']:.1f}GB / {disk['total_gb']:.1f}GB ({disk['percent']:.1f}%)\n"
                
                reply += f"\n• QQSync群服互通: 运行中 ✅"
                
            except Exception as e:
                reply = "ℹ️ 无法获取服务器信息"
                if _plugin_instance:
                    _plugin_instance.logger.error(f"获取服务器信息失败: {e}")
                    # 提供基础信息作为回退
                    try:
                        online_count = len(_plugin_instance.server.online_players)
                        max_players = _plugin_instance.server.max_players
                        reply = f"ℹ️ 服务器基础信息:\n• 在线玩家: {online_count}/{max_players}\n• QQSync: 运行中 ✅"
                    except:
                        reply = "ℹ️ 服务器信息获取失败"
        
        # /bindqq 命令 - 查看绑定状态
        elif cmd == "bindqq":
            from ..utils.time_utils import TimeUtils
            bound_player = _plugin_instance.data_manager.get_qq_player(str(user_id))
            if bound_player:
                player_data = _plugin_instance.data_manager.binding_data.get(bound_player, {})
                
                reply = f"=== 您的绑定信息 ===\n"
                reply += f"绑定角色: {bound_player}\n"
                reply += f"绑定QQ: {user_id}\n"
                
                # 显示XUID
                xuid = player_data.get("xuid", "")
                if xuid:
                    reply += f"XUID: {xuid}\n"
                
                # 检查在线状态
                is_online = any(player.name == bound_player for player in _plugin_instance.server.online_players)
                reply += f"当前状态: {'在线' if is_online else '离线'}\n"
                
                # 检查封禁状态
                if _plugin_instance.data_manager.is_player_banned(bound_player):
                    reply += "状态: 已封禁 ❌\n"
                else:
                    reply += "状态: 正常 ✅\n"
                
                # 添加游戏统计信息
                reply += "\n📊 游戏统计:\n"
                
                # 游戏时长
                total_playtime = player_data.get("total_playtime", 0)
                if total_playtime > 0:
                    hours = total_playtime // 3600
                    minutes = (total_playtime % 3600) // 60
                    reply += f"总游戏时长: {hours}小时{minutes}分钟\n"
                else:
                    reply += "总游戏时长: 无记录\n"
                
                # 登录次数
                session_count = player_data.get("session_count", 0)
                reply += f"登录次数: {session_count}次\n"
                
                # 绑定时间
                bind_time = player_data.get("bind_time")
                if bind_time:
                    try:
                        bind_time_dt = datetime.datetime.fromtimestamp(bind_time)
                        bind_time_str = TimeUtils.format_datetime(bind_time_dt)
                        reply += f"绑定时间: {bind_time_str}\n"
                    except (ValueError, TypeError):
                        reply += f"绑定时间: 时间格式错误\n"
                
                # 最后登录时间
                last_join_time = player_data.get("last_join_time")
                if last_join_time:
                    try:
                        last_join_dt = datetime.datetime.fromtimestamp(last_join_time)
                        last_join_str = TimeUtils.format_datetime(last_join_dt)
                        reply += f"最后登录: {last_join_str}"
                    except (ValueError, TypeError):
                        reply += f"最后登录: 时间格式错误"
                else:
                    reply += "最后登录: 无记录"
            else:
                reply = "您的QQ尚未绑定游戏角色\n请在游戏中使用 /bindqq 命令开始绑定流程"
        
        # /verify 命令 - 验证QQ绑定
        elif cmd == "verify":
            if len(args) == 1:
                verification_code = args[0]
                # 检查验证码格式（6位数字）
                if not verification_code.isdigit() or len(verification_code) != 6:
                    reply = f"❌ 验证码格式错误\n💡 请输入6位数字验证码，例如：/verify 123456"
                else:
                    result = await _handle_verification_code_with_feedback(ws, user_id, verification_code, display_name, group_id)
                    if result:
                        return  # 验证码处理成功，有自己的回复逻辑
                    else:
                        # 验证失败，不需要设置reply因为函数内已经发送了回复
                        return
            else:
                reply = f"❌ 命令格式错误\n💡 正确用法：/verify <验证码>\n💡 例如：/verify 123456"
        
        # === 管理员命令 ===
        elif is_admin:
            if cmd == "cmd" and len(args) >= 1:
                command_to_execute = " ".join(args)
                # 转义HTML字符
                command_to_execute = html.unescape(command_to_execute)
                # 返回的信息和状态
                msg_ret = []
                error_ret = []
                success = False

                try:

                    # 创建一个队列来接收主线程的执行结果
                    result_queue = queue.Queue()

                    language = _plugin_instance.server.language

                    def on_message(msg):
                        if isinstance(msg, str):
                            msg_ret.append(msg)
                        else:
                            try:
                                translated = language.translate(msg, language.locale)
                                msg_ret.append(translated)
                            except Exception as e:
                                msg_ret.append(f"[消息翻译失败: {e}]")

                    def on_error(err):
                        if isinstance(err, str):
                            error_ret.append(err)
                        else:
                            try:
                                translated = language.translate(err)
                                error_ret.append(translated)
                            except Exception as e:
                                error_ret.append(f"[错误翻译失败: {e}]")

                    wrapper = CommandSenderWrapper(
                        sender=_plugin_instance.server.command_sender,
                        on_message=on_message,
                        on_error=on_error
                    )

                    def server_thread():
                        try:
                            # 在主线程中执行命令
                            success_result = _plugin_instance.server.dispatch_command(wrapper, command_to_execute)
                            # 将结果放入队列
                            result_queue.put((success_result, None))
                        except Exception as e:
                            # 如果有异常，也放入队列
                            result_queue.put((False, str(e)))

                    # 提交任务到主线程
                    _plugin_instance.server.scheduler.run_task(_plugin_instance, server_thread, 0, 0)

                    # 阻塞等待结果
                    success, error = result_queue.get(block=True, timeout=10)  # 设置10秒超时

                    if error:
                        raise Exception(error)

                    # 合并输出
                    lines = []
                    lines.extend(msg_ret)
                    lines.extend([f"[ERROR] {e}" for e in error_ret])

                    output_text = "\n".join(lines) if lines else "无返回值"
                    status = "成功" if success else "失败, 请检查命令语法或权限"

                    reply = f"✅ 命令已执行: /{command_to_execute}\n状态: {status}\n输出:\n{output_text}"

                except queue.Empty:
                    reply = "❌ 命令执行超时"
                except Exception as e:
                    reply = f"❌ 命令执行失败: {str(e)}"
            
            elif cmd == "who" and len(args) >= 1:
                # 查询玩家信息
                # 修复包含空格的玩家名处理问题
                search_input = " ".join(args)
                # 处理带双引号的玩家名
                if search_input.startswith('"') and search_input.endswith('"') and len(search_input) >= 2:
                    search_input = search_input[1:-1]
                target_player = None
                player_data = None
                
                if search_input.isdigit():
                    # 输入的是QQ号
                    target_player = _plugin_instance.data_manager.get_qq_player(search_input)
                    if not target_player:
                        reply = f"❌ 未找到绑定QQ号 {search_input} 的玩家"
                    else:
                        player_data = _plugin_instance.data_manager.binding_data.get(target_player, {})
                else:
                    # 输入的是玩家名
                    target_player = search_input
                    if target_player not in _plugin_instance.data_manager.binding_data:
                        reply = f"❌ 玩家 {target_player} 未找到记录"
                    else:
                        player_data = _plugin_instance.data_manager.binding_data[target_player]
                
                if target_player and player_data:
                    from ..utils.time_utils import TimeUtils
                    
                    player_qq = player_data.get("qq", "")
                    
                    reply = f"= 玩家 {target_player} 详细信息 =\n"
                    reply += f"绑定QQ: {player_qq if player_qq else '未绑定'}\n"
                    
                    xuid = player_data.get("xuid", "")
                    if xuid:
                        reply += f"XUID: {xuid}\n"
                    
                    # 检查在线状态
                    is_online = any(player.name == target_player for player in _plugin_instance.server.online_players)
                    reply += f"当前状态: {'在线' if is_online else '离线'}\n"
                    
                    # 检查封禁状态
                    if _plugin_instance.data_manager.is_player_banned(target_player):
                        ban_time = player_data.get("ban_time", "")
                        ban_by = player_data.get("ban_by", "未知")
                        ban_reason = player_data.get("ban_reason", "无原因")
                        reply += f"封禁状态: 已封禁 ❌\n"
                        
                        # 格式化封禁时间
                        if ban_time:
                            try:
                                ban_time_dt = datetime.datetime.fromtimestamp(ban_time)
                                ban_time_str = TimeUtils.format_datetime(ban_time_dt)
                                reply += f"封禁时间: {ban_time_str}\n"
                            except (ValueError, TypeError):
                                reply += f"封禁时间: 格式错误\n"
                        
                        reply += f"封禁操作者: {ban_by}\n"
                        reply += f"封禁原因: {ban_reason}\n"
                    else:
                        reply += "封禁状态: 正常 ✅\n"
                    
                    # 添加游戏统计信息
                    reply += "\n📊 游戏统计:\n"
                    
                    # 游戏时长
                    total_playtime = player_data.get("total_playtime", 0)
                    if total_playtime > 0:
                        hours = total_playtime // 3600
                        minutes = (total_playtime % 3600) // 60
                        reply += f"总游戏时长: {hours}小时{minutes}分钟\n"
                    else:
                        reply += "总游戏时长: 无记录\n"
                    
                    # 会话统计
                    session_count = player_data.get("session_count", 0)
                    reply += f"登录次数: {session_count}次\n"
                    
                    # 最后登录时间
                    last_join_time = player_data.get("last_join_time")
                    if last_join_time:
                        try:
                            last_join_dt = datetime.datetime.fromtimestamp(last_join_time)
                            last_join_str = TimeUtils.format_datetime(last_join_dt)
                            reply += f"最后登录: {last_join_str}\n"
                        except (ValueError, TypeError):
                            reply += f"最后登录: 时间格式错误\n"
                    else:
                        reply += "最后登录: 无记录\n"
                    
                    # 最后退出时间
                    last_quit_time = player_data.get("last_quit_time")
                    if last_quit_time:
                        try:
                            last_quit_dt = datetime.datetime.fromtimestamp(last_quit_time)
                            last_quit_str = TimeUtils.format_datetime(last_quit_dt)
                            reply += f"最后退出: {last_quit_str}\n"
                        except (ValueError, TypeError):
                            reply += f"最后退出: 时间格式错误\n"
                    else:
                        reply += "最后退出: 无记录\n"
                    
                    # 绑定历史
                    reply += "\n🔗 绑定历史:\n"
                    
                    # 初始绑定时间
                    bind_time = player_data.get("bind_time")
                    if bind_time:
                        try:
                            bind_time_dt = datetime.datetime.fromtimestamp(bind_time)
                            bind_time_str = TimeUtils.format_datetime(bind_time_dt)
                            reply += f"初始绑定: {bind_time_str}\n"
                        except (ValueError, TypeError):
                            reply += f"初始绑定: 时间格式错误\n"
                    
                    # 重新绑定时间
                    rebind_time = player_data.get("rebind_time")
                    if rebind_time:
                        try:
                            rebind_time_dt = datetime.datetime.fromtimestamp(rebind_time)
                            rebind_time_str = TimeUtils.format_datetime(rebind_time_dt)
                            reply += f"重新绑定: {rebind_time_str}\n"
                        except (ValueError, TypeError):
                            reply += f"重新绑定: 时间格式错误\n"
                    
                    # 解绑历史
                    unbind_time = player_data.get("unbind_time")
                    if unbind_time:
                        try:
                            unbind_time_dt = datetime.datetime.fromtimestamp(unbind_time)
                            unbind_time_str = TimeUtils.format_datetime(unbind_time_dt)
                            unbind_by = player_data.get("unbind_by", "未知")
                            unbind_reason = player_data.get("unbind_reason", "无原因")
                            reply += f"解绑时间: {unbind_time_str}\n"
                            reply += f"解绑操作者: {unbind_by}\n"
                            reply += f"解绑原因: {unbind_reason}\n"
                        except (ValueError, TypeError):
                            reply += f"解绑时间: 时间格式错误\n"
                    
                    # 原始QQ（如果有解绑记录）
                    original_qq = player_data.get("original_qq")
                    if original_qq:
                        reply += f"原绑定QQ: {original_qq}\n"
                    
                    # 解封历史（如果有）
                    unban_time = player_data.get("unban_time")
                    if unban_time:
                        try:
                            unban_time_dt = datetime.datetime.fromtimestamp(unban_time)
                            unban_time_str = TimeUtils.format_datetime(unban_time_dt)
                            unban_by = player_data.get("unban_by", "未知")
                            reply += f"\n🔓 解封历史:\n"
                            reply += f"解封时间: {unban_time_str}\n"
                            reply += f"解封操作者: {unban_by}"
                        except (ValueError, TypeError):
                            reply += f"\n🔓 解封时间: 时间格式错误"
                
                # 如果没有设置reply且没有找到数据，设置默认错误消息
                if not reply:
                    reply = f"❌ 未找到玩家 {search_input} 的数据"
            
            elif cmd == "ban" and len(args) >= 1:
                # 封禁玩家
                player_name = args[0]
                ban_reason = " ".join(args[1:]) if len(args) > 1 else "管理员封禁"
                
                if _plugin_instance.data_manager.ban_player(player_name, display_name, ban_reason):
                    reply = f"✅ 已封禁玩家 {player_name}\n原因: {ban_reason}"
                else:
                    reply = f"❌ 封禁失败，玩家 {player_name} 不存在"
            
            elif cmd == "unban" and len(args) == 1:
                # 解封玩家
                player_name = args[0]
                if _plugin_instance.data_manager.unban_player(player_name):
                    reply = f"✅ 已解封玩家 {player_name}"
                else:
                    reply = f"❌ 解封失败，玩家 {player_name} 未被封禁或不存在"
            
            elif cmd == "banlist":
                # 查看封禁列表
                banned_players = _plugin_instance.data_manager.get_banned_players()
                if not banned_players:
                    reply = "📋 当前没有被封禁的玩家"
                else:
                    reply = f"📋 封禁列表 ({len(banned_players)}):\n"
                    for banned_info in banned_players[:10]:  # 最多显示10个
                        player_name = banned_info["name"]
                        ban_by = banned_info["ban_by"]
                        ban_reason = banned_info["ban_reason"]
                        reply += f"• {player_name} (by {ban_by}): {ban_reason}\n"
                    
                    if len(banned_players) > 10:
                        reply += f"... 还有 {len(banned_players) - 10} 个被封禁的玩家"
            
            elif cmd == "unbindqq" and len(args) == 1:
                # 解绑QQ
                search_input = args[0]
                target_player = None
                
                if search_input.isdigit():
                    # 输入的是QQ号
                    target_player = _plugin_instance.data_manager.get_qq_player(search_input)
                    if not target_player:
                        reply = f"❌ 未找到绑定QQ号 {search_input} 的玩家"
                else:
                    # 输入的是玩家名
                    target_player = search_input
                
                if target_player and _plugin_instance.data_manager.unbind_player_qq(target_player, display_name):
                    reply = f"✅ 已解绑玩家 {target_player} 的QQ绑定"
                    
                    # 如果玩家在线，重新应用权限
                    for player in _plugin_instance.server.online_players:
                        if player.name == target_player:
                            def apply_permissions():
                                _plugin_instance.permission_manager.check_and_apply_permissions(player)
                            _plugin_instance.server.scheduler.run_task(_plugin_instance, apply_permissions, delay=1)
                            break
                else:
                    reply = f"❌ 解绑失败，玩家 {target_player} 不存在或未绑定QQ"
            
            elif cmd == "tog_qq":
                # 切换QQ消息转发
                current_state = _plugin_instance.config_manager.get_config("enable_qq_to_game", True)
                _plugin_instance.config_manager.set_config("enable_qq_to_game", not current_state)
                _plugin_instance.config_manager.save_config()
                
                status = "启用" if not current_state else "禁用"
                icon = "✅" if not current_state else "❌"
                reply = f"{icon} QQ消息转发已{status}"
            
            elif cmd == "tog_game":
                # 切换游戏消息转发
                current_state = _plugin_instance.config_manager.get_config("enable_game_to_qq", True)
                _plugin_instance.config_manager.set_config("enable_game_to_qq", not current_state)
                _plugin_instance.config_manager.save_config()
                
                status = "启用" if not current_state else "禁用"
                icon = "✅" if not current_state else "❌"
                reply = f"{icon} 游戏消息转发已{status}"
            
            elif cmd == "reload":
                # 重新加载配置
                try:
                    _plugin_instance.config_manager.reload_config()
                    reply = "✅ 配置文件已重新加载"
                except Exception as e:
                    reply = f"❌ 重新加载配置失败: {str(e)}"
            
            else:
                reply = f"❓ 未知的管理员命令: /{cmd}\n💡 使用 /help 查看可用命令"
        
        else:
            # 非管理员使用管理员命令
            if cmd in ["cmd", "who", "ban", "unban", "banlist", "unbindqq", "tog_qq", "tog_game", "reload"]:
                reply = "❌ 该命令仅限管理员使用"
            else:
                reply = f"❓ 未知命令: /{cmd}\n💡 使用 /help 查看可用命令"
        
        # 发送回复
        if reply:
            await send_group_msg(ws, group_id, reply)
            
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"处理群内命令失败: {e}")
            await send_group_msg(ws, group_id, f"❌ 命令处理失败: {str(e)}")


async def _forward_message_to_game(message_data: dict, display_name: str):
    """转发消息到游戏"""
    try:
        # 使用新的消息解析工具来处理消息
        from ..utils.message_utils import parse_qq_message, clean_message_text, truncate_message
        
        # 获取群组ID和名称
        group_id = message_data.get("group_id")
        group_name = ""
        if _plugin_instance and group_id:
            group_names = _plugin_instance.config_manager.get_config("group_names", {})
            group_name = group_names.get(str(group_id), "")
        
        # 解析QQ消息，处理emoji和CQ码
        parsed_message = parse_qq_message(message_data)
        
        # 构建完整的格式化消息，如果配置了群组名称则添加前缀
        if group_name:
            formatted_message = f"[{group_name}] {display_name}: {parsed_message}"
        else:
            formatted_message = f"{display_name}: {parsed_message}"
        
        # 清理文本并限制长度
        clean_message = clean_message_text(formatted_message)
        clean_message = truncate_message(clean_message, max_length=150)
        
        if not clean_message or parsed_message == "[空消息]":
            return
        
        # 转发到游戏 - 使用调度器确保在主线程执行
        game_message = f"{ColorFormat.GREEN}[QQ群] {ColorFormat.AQUA}{clean_message}{ColorFormat.RESET}"
        
        if _plugin_instance:
            _plugin_instance.logger.info(f"{ColorFormat.GREEN}[QQ群] {ColorFormat.AQUA}{clean_message}{ColorFormat.RESET}")

            # 为webui写入聊天历史记录
            webui = _plugin_instance.server.plugin_manager.get_plugin('qqsync_webui_plugin')
            if webui:
                try:
                    webui.on_message_sent(sender=display_name, content=parsed_message, msg_type="chat", direction="qq_to_game")
                except Exception as e:
                    _plugin_instance.logger.warning(f"webui on_message_sent调用失败: {e}")
        
        def send_to_players():
            """在主线程中发送消息给所有玩家"""
            try:
                for player in _plugin_instance.server.online_players:
                    player.send_message(game_message)

            except Exception as e:
                if _plugin_instance:
                    _plugin_instance.logger.error(f"发送游戏消息失败: {e}")
        
        # 使用调度器在主线程执行
        if _plugin_instance:
            _plugin_instance.server.scheduler.run_task(_plugin_instance, send_to_players, delay=1)
            
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"转发消息到游戏失败: {e}")


async def handle_api_response(data: dict):
    """处理API响应"""
    try:
        global _verification_messages
        
        # OneBot V11 API响应格式：{"status": "ok", "retcode": 0, "data": {...}, "echo": "..."}
        status = data.get("status")
        retcode = data.get("retcode")
        response_data = data.get("data", {})
        echo = data.get("echo", "")
        
        # 尝试从echo中推断action（兼容性处理）
        action = None
        if echo.startswith("verification_msg:"):
            action = "send_group_msg"
        elif echo.startswith("get_group_member_list"):
            action = "get_group_member_list"
        elif echo.startswith("get_stranger_info"):
            action = "get_stranger_info"
        elif echo.startswith("set_group_card"):
            action = "set_group_card"
        
        if not _plugin_instance:
            return
        
        # 只处理成功的API响应
        if status == "ok" and retcode == 0 and response_data and action == "send_group_msg":
            # 消息发送成功，保存消息ID用于撤回
            message_id = response_data.get("message_id")
            
            if _plugin_instance:
                _plugin_instance.logger.debug(f"收到send_group_msg响应: status={status}, retcode={retcode}, message_id={message_id}, echo={echo}")
            
            if message_id and echo.startswith("verification_msg:"):
                # 通知verification_manager保存消息ID
                if _plugin_instance and hasattr(_plugin_instance, 'verification_manager'):
                    _plugin_instance.verification_manager.handle_message_response(echo, message_id)
            elif message_id:
                # 处理handlers中的_verification_messages（兼容性）
                for qq_number, msg_info in _verification_messages.items():
                    if not msg_info.get("message_id") and msg_info.get("echo") == echo:
                        msg_info["message_id"] = message_id
                        if _plugin_instance:
                            _plugin_instance.logger.debug(f"通过echo匹配保存QQ {qq_number} 的验证码消息ID: {message_id}")
                        break
            else:
                if _plugin_instance:
                    if not message_id:
                        _plugin_instance.logger.warning(f"❌ send_group_msg响应中缺少message_id: {data}")
                    elif not echo.startswith("verification_msg:"):
                        _plugin_instance.logger.debug(f"非验证码消息响应: echo={echo}")
        
        elif status == "ok" and retcode != 0:
            # API请求失败的情况
            if _plugin_instance:
                error_msg = data.get("message", "未知错误")
                if action == "set_group_card":
                    # 通知verification_manager处理失败响应，由它负责日志输出
                    if hasattr(_plugin_instance, 'verification_manager'):
                        _plugin_instance.verification_manager.handle_api_response(echo, "failed", {"retcode": retcode, "message": error_msg})
                else:
                    _plugin_instance.logger.warning(f"❌ API请求失败: retcode={retcode}, msg={error_msg}, echo={echo}")
        
        elif action == "get_group_member_list" and status == "ok" and retcode == 0 and response_data:
            # 更新群成员列表
            group_members = set()
            for member in response_data:
                user_id = str(member.get("user_id", ""))
                if user_id:
                    group_members.add(user_id)
            
            # 如果插件实例中有群成员集合，则更新它
            if hasattr(_plugin_instance, 'group_members'):
                _plugin_instance.group_members.update(group_members)
            
            _plugin_instance.logger.info(f"已更新群成员列表，新增 {len(group_members)} 人")
        
        elif action == "get_stranger_info" and status == "ok" and retcode == 0 and response_data:
            # 用户信息查询成功，更新昵称
            nickname = response_data.get("nickname", "未知昵称")
            user_id = str(response_data.get("user_id", ""))
            
            if _plugin_instance:
                _plugin_instance.logger.info(f"📋 获取QQ用户信息成功: QQ={user_id}, 昵称={nickname}")
            
            # 查找等待昵称的确认信息
            found = False
            for player_name, qq_info in _plugin_instance.verification_manager.pending_qq_confirmations.items():
                if qq_info.get("qq") == user_id:
                    qq_info["nickname"] = nickname
                    found = True
                    if _plugin_instance:
                        _plugin_instance.logger.info(f"✅ 已更新玩家 {player_name} 的QQ昵称: {nickname}")
                    break
            
            if not found and _plugin_instance:
                _plugin_instance.logger.warning(f"❌ 未找到等待QQ昵称的玩家，QQ号: {user_id}")
                _plugin_instance.logger.debug(f"当前等待确认的玩家: {list(_plugin_instance.verification_manager.pending_qq_confirmations.keys())}")
        
        elif action == "get_stranger_info" and status == "ok" and retcode != 0:
            # 用户信息查询失败
            error_msg = data.get("msg", "未知错误")
            if _plugin_instance:
                _plugin_instance.logger.warning(f"获取QQ用户信息失败: retcode={retcode}, msg={error_msg}")
        
        # 调用verification_manager处理API响应
        if _plugin_instance and hasattr(_plugin_instance, 'verification_manager'):
            _plugin_instance.verification_manager.handle_api_response(echo, status, data)
                    
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"处理API响应失败: {e}")


async def handle_group_member_change(data: dict):
    """处理群成员变动"""
    try:
        if not _plugin_instance:
            return
            
        notice_type = data.get("notice_type")
        user_id = str(data.get("user_id", ""))
        group_id = data.get("group_id")
        
        target_groups = _plugin_instance.config_manager.get_config("target_groups", [])
        if group_id not in target_groups:
            return
        
        if notice_type == "group_increase":
            # 有人加群
            if hasattr(_plugin_instance, 'group_members'):
                _plugin_instance.group_members.add(user_id)
            _plugin_instance.logger.info(f"用户 {user_id} 加入群聊")
            
        elif notice_type == "group_decrease":
            # 有人退群
            if hasattr(_plugin_instance, 'group_members'):
                _plugin_instance.group_members.discard(user_id)
            
            # 检查是否有玩家绑定了这个QQ
            player_name = _plugin_instance.data_manager.get_qq_player(user_id)
            if player_name:
                _plugin_instance.logger.info(f"绑定玩家 {player_name} 的QQ {user_id} 退出群聊")
                
                # 如果玩家在线，重新应用权限
                for player in _plugin_instance.server.online_players:
                    if player.name == player_name:
                        _plugin_instance.permission_manager.check_and_apply_permissions(player)
                        break
            else:
                _plugin_instance.logger.info(f"用户 {user_id} 退出群聊")
                
    except Exception as e:
        if _plugin_instance:
            _plugin_instance.logger.error(f"处理群成员变动失败: {e}")