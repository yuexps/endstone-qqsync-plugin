"""
验证管理模块
负责QQ绑定验证码的生成、发送、验证和管理
"""

import asyncio
import json
import random
import time
import datetime
from ..utils.time_utils import TimeUtils
from typing import Dict, List, Set, Any, Tuple, Optional

# 延迟导入避免循环依赖，但统一管理
_ColorFormat = None

def _get_color_format():
    """获取 ColorFormat，延迟导入"""
    global _ColorFormat
    if _ColorFormat is None:
        from endstone import ColorFormat
        _ColorFormat = ColorFormat
    return _ColorFormat


class VerificationManager:
    """验证管理器"""
    
    def __init__(self, plugin, logger):
        self.plugin = plugin
        self.logger = logger
        
        # 验证码相关存储
        self.pending_verifications: Dict[str, Dict[str, Any]] = {}  # {player_name: verification_info}
        self.verification_codes: Dict[str, Dict[str, Any]] = {}     # {qq_number: verification_info}
        self.verification_messages: Dict[str, Dict[str, Any]] = {}  # {qq_number: message_info}
        self.player_bind_attempts: Dict[str, float] = {}            # {player_name: timestamp}
        
        # 多玩家处理相关
        self.verification_queue: Dict[str, float] = {}              # {qq: 发送时间}
        self.binding_rate_limit: Dict[str, float] = {}              # {qq: 上次绑定时间}
        self.form_display_cache: Dict[str, float] = {}              # {player_name: 显示时间}
        self.form_display_count: Dict[str, int] = {}                # {player_name: 显示次数}
        self.pending_qq_confirmations: Dict[str, Dict[str, Any]] = {} # {player_name: qq_info}
        self.concurrent_bindings: Set[str] = set()                  # 当前正在进行绑定的玩家
        
        # 配置参数
        self.max_concurrent_bindings = 25
        self.verification_rate_limit_count = 30  # 每分钟最多发送验证码数量
        self.binding_cooldown = 10  # 绑定失败后的冷却时间（秒）
        
        # 队列管理
        self.binding_queue: List[Tuple[str, str, float]] = []       # [(player_name, qq_number, request_time)]
        self.queue_notification_sent: Set[str] = set()             # 已发送排队通知的玩家
        
        # 验证码发送队列
        self.verification_send_queue: List[Tuple[Any, str, str, int, float]] = [] # [(player, qq, code, attempt, timestamp)]
        self.verification_retry_count: Dict[str, int] = {}          # {qq_number: retry_count}
        self.max_verification_retries = 3
        self.verification_send_interval = 2  # 验证码发送间隔（秒）
        self.last_verification_send_time = 0
        
        # 统一验证尝试计数器
        self.unified_verification_attempts: Dict[str, int] = {}     # {verification_key: attempts}
        self.player_verification_cooldown: Dict[str, float] = {}    # {player_name: cooldown_time}
    
    def can_send_verification(self, qq_number: str, player_name: str) -> Tuple[bool, str]:
        """检查是否可以发送验证码"""
        current_time = TimeUtils.get_timestamp()
        
        # 检查玩家60秒冷却
        if player_name in self.pending_verifications:
            last_request_time = self.pending_verifications[player_name].get("timestamp", 0)
            cooldown_remaining = 60 - (current_time - last_request_time)
            if cooldown_remaining > 0:
                return False, f"您在60秒内只能申请一个验证码，请等待{int(cooldown_remaining)}秒后再次尝试"
        
        # 检查验证失败冷却
        if player_name in self.player_verification_cooldown:
            cooldown_remaining = 60 - (current_time - self.player_verification_cooldown[player_name])
            if cooldown_remaining > 0:
                return False, f"验证失败冷却中，请等待{int(cooldown_remaining)}秒后重试"
        
        # 检查QQ号绑定频率限制
        if qq_number in self.binding_rate_limit:
            cooldown_remaining = self.binding_cooldown - (current_time - self.binding_rate_limit[qq_number])
            if cooldown_remaining > 0:
                return False, f"该QQ号刚刚尝试过绑定，请等待{int(cooldown_remaining)}秒后重试"
        
        # 检查并发绑定数量
        if len(self.concurrent_bindings) >= self.max_concurrent_bindings:
            return False, f"当前绑定请求过多（{len(self.concurrent_bindings)}/{self.max_concurrent_bindings}），请稍后重试"
        
        # 检查验证码发送频率
        verification_count = sum(1 for t in self.verification_queue.values() 
                               if current_time - t < 60)  # 统计1分钟内的发送次数
        if verification_count >= self.verification_rate_limit_count:
            return False, f"系统验证码发送频率已达上限，请稍后重试"
        
        return True, ""
    
    def register_verification_attempt(self, qq_number: str, player_name: str):
        """注册验证码发送尝试"""
        current_time = TimeUtils.get_timestamp()
        self.verification_queue[qq_number] = current_time
        self.concurrent_bindings.add(player_name)
        
        # 清理过期的记录（超过5分钟）
        expired_qq = [qq for qq, t in self.verification_queue.items() if current_time - t > 300]
        for qq in expired_qq:
            del self.verification_queue[qq]
    
    def unregister_verification_attempt(self, qq_number: str, player_name: str, success: bool = True):
        """注销验证码发送尝试"""
        if qq_number in self.verification_queue:
            del self.verification_queue[qq_number]
        
        if player_name in self.concurrent_bindings:
            self.concurrent_bindings.discard(player_name)
        
        if not success:
            # 失败时设置冷却
            self.binding_rate_limit[qq_number] = TimeUtils.get_timestamp()
    
    def cleanup_old_verification(self, player_name: str):
        """清理玩家的旧验证码"""
        if player_name in self.pending_verifications:
            old_qq = self.pending_verifications[player_name].get("qq")
            del self.pending_verifications[player_name]
            
            if old_qq and old_qq in self.verification_codes:
                del self.verification_codes[old_qq]
                
            self.logger.info(f"已清理玩家 {player_name} 的旧验证码")
    
    def cleanup_qq_old_verifications(self, qq_number: str):
        """清理与指定QQ号相关的所有旧验证码"""
        # 清理verification_codes中的记录
        if qq_number in self.verification_codes:
            old_player = self.verification_codes[qq_number].get("player_name")
            del self.verification_codes[qq_number]
            
            # 同时清理对应玩家的pending_verifications
            if old_player and old_player in self.pending_verifications:
                del self.pending_verifications[old_player]
                
            self.logger.info(f"已清理QQ {qq_number} 相关的旧验证码")
    
    def generate_verification_code(self, player, qq_number: str, nickname: str = "未知昵称") -> bool:
        """生成并存储验证码"""
        try:
            # 检查是否可以发送验证码
            can_send, error_msg = self.can_send_verification(qq_number, player.name)
            if not can_send:
                player.send_message(f"[QQsync] [限制] {error_msg}")
                return False
            
            # 清理旧验证码
            self.cleanup_expired_verifications()
            self.cleanup_qq_old_verifications(qq_number)
            
            # 注册验证码发送尝试
            self.register_verification_attempt(qq_number, player.name)
            
            # 记录绑定尝试时间
            current_time = TimeUtils.get_timestamp()
            self.player_bind_attempts[player.name] = current_time
            
            # 生成验证码
            verification_code = str(random.randint(100000, 999999))
            
            # 存储验证信息
            creation_time = TimeUtils.get_network_time()[0]  # 获取准确的网络时间
            self.pending_verifications[player.name] = {
                "qq": qq_number,
                "code": verification_code,
                "timestamp": current_time,
                "creation_time": creation_time,
                "player_xuid": player.xuid
            }
            
            self.verification_codes[qq_number] = {
                "code": verification_code,
                "timestamp": current_time,
                "creation_time": creation_time,
                "player_name": player.name
            }
            
            # 控制台显示验证码
            ColorFormat = _get_color_format()
            console_msg = f"{ColorFormat.AQUA}[验证码] 玩家: {ColorFormat.WHITE}{player.name}{ColorFormat.AQUA} | QQ: {ColorFormat.WHITE}{qq_number}{ColorFormat.AQUA} | 验证码: {ColorFormat.YELLOW}{verification_code}{ColorFormat.RESET}"
            self.logger.info(console_msg)
            
            # 添加到发送队列
            self.verification_send_queue.append((
                player, qq_number, verification_code, 1, current_time
            ))
            
            return True
            
        except Exception as e:
            self.logger.error(f"生成验证码失败: {e}")
            self.unregister_verification_attempt(qq_number, player.name, False)
            return False
    
    def verify_code(self, player_name: str, player_xuid: str, input_code: str, source: str = "game") -> Tuple[bool, str, Dict[str, Any]]:
        """验证验证码"""
        # 检查是否有待验证的信息
        if player_name not in self.pending_verifications:
            return False, "验证信息已过期，请重新开始绑定", {}
        
        pending_info = self.pending_verifications[player_name]
        
        # 安全检查：验证XUID是否匹配
        if pending_info.get("player_xuid") and pending_info.get("player_xuid") != player_xuid:
            return False, "验证失败：玩家身份不匹配，请重新开始绑定", {}
        
        # 检查验证码是否过期（60秒有效期）
        current_time = TimeUtils.get_timestamp()
        if current_time - pending_info["timestamp"] > 60:
            # 清理过期验证信息
            qq_number = pending_info["qq"]
            del self.pending_verifications[player_name]
            if qq_number in self.verification_codes:
                del self.verification_codes[qq_number]
            return False, "验证码已过期，请重新开始绑定", {}
        
        # 验证验证码格式
        if not input_code or not input_code.isdigit() or len(input_code) != 6:
            return False, "请输入有效的6位数字验证码！", {}
        
        # 验证验证码是否正确
        if input_code == pending_info["code"]:
            # 检查验证码是否已被使用
            qq_number = pending_info["qq"]
            if qq_number in self.verification_codes and self.verification_codes[qq_number].get("used", False):
                return False, "验证码已使用：该验证码已被使用过，请重新申请绑定", {}
            
            # 标记验证码为已使用
            if qq_number in self.verification_codes:
                self.verification_codes[qq_number]["used"] = True
                self.verification_codes[qq_number]["use_time"] = current_time
            
            # 验证成功后立即撤回验证码消息并发送成功播报
            def handle_success():
                """在主线程中处理验证成功后续操作"""
                try:
                    # 使用异步方式处理撤回和播报
                    asyncio.run_coroutine_threadsafe(
                        self._handle_verification_success(player_name, qq_number),
                        self.plugin._loop
                    )
                except Exception as e:
                    self.logger.error(f"处理验证成功后续操作失败: {e}")
            
            # 使用调度器在主线程执行，确保线程安全
            self.plugin.server.scheduler.run_task(
                self.plugin, 
                handle_success, 
                delay=1  # 1 tick 延迟确保状态同步
            )
            
            # 清理验证数据 - verification_manager 统一管理
            del self.pending_verifications[player_name]
            if qq_number in self.verification_codes:
                del self.verification_codes[qq_number]
            
            # 清理统一验证尝试计数器
            verification_key = f"unified_attempts_{player_name}_{qq_number}"
            if verification_key in self.unified_verification_attempts:
                del self.unified_verification_attempts[verification_key]
            
            return True, "验证成功", pending_info
        else:
            # 验证失败处理
            qq_number = pending_info["qq"]
            verification_key = f"unified_attempts_{player_name}_{qq_number}"
            current_attempts = self.unified_verification_attempts.get(verification_key, 0) + 1
            self.unified_verification_attempts[verification_key] = current_attempts
            
            max_attempts = 3
            remaining_attempts = max_attempts - current_attempts
            
            if remaining_attempts > 0:
                return False, f"验证码错误！还可以尝试 {remaining_attempts} 次", {}
            else:
                # 尝试次数用完，清理验证数据并触发冷却
                del self.pending_verifications[player_name]
                if qq_number in self.verification_codes:
                    del self.verification_codes[qq_number]
                
                # 立即撤回验证码消息（次数用完）
                def handle_max_attempts():
                    """在主线程中处理验证次数达到上限后的撤回"""
                    try:
                        # 使用异步方式撤回验证码消息
                        asyncio.run_coroutine_threadsafe(
                            self._delete_verification_message(qq_number),
                            self.plugin._loop
                        )
                        self.logger.info(f"验证次数达到上限，已撤回QQ {qq_number} 的验证码消息")
                    except Exception as e:
                        self.logger.error(f"撤回验证码消息失败（次数达到上限）: {e}")
                
                # 使用调度器在主线程执行撤回，确保线程安全
                self.plugin.server.scheduler.run_task(
                    self.plugin, 
                    handle_max_attempts, 
                    delay=1  # 1 tick 延迟确保状态同步
                )
                
                # 清理统一尝试计数
                if verification_key in self.unified_verification_attempts:
                    del self.unified_verification_attempts[verification_key]
                
                # 触发验证失败冷却
                self.player_verification_cooldown[player_name] = current_time
                self.binding_rate_limit[qq_number] = current_time
                
                return False, f"验证码尝试次数已达上限（{max_attempts}次），请等待60秒后重新申请", {}
    
    def cleanup_expired_verifications(self):
        """清理过期的验证码"""
        current_time = TimeUtils.get_timestamp()
        expired_players = []
        expired_qqs = []
        
        # 检查pending_verifications中的过期项
        for player_name, data in self.pending_verifications.items():
            if current_time - data["timestamp"] > 60:  # 60秒过期
                expired_players.append(player_name)
        
        # 检查verification_codes中的过期项
        for qq_number, data in self.verification_codes.items():
            if current_time - data["timestamp"] > 60:  # 60秒过期
                expired_qqs.append(qq_number)
        
        # 清理过期项
        for player_name in expired_players:
            del self.pending_verifications[player_name]
            self.logger.info(f"清理过期验证码: 玩家 {player_name}")
        
        for qq_number in expired_qqs:
            del self.verification_codes[qq_number]
            self.logger.info(f"清理过期验证码: QQ {qq_number}")
        
        # 清理verification_messages中的过期项并撤回消息
        expired_message_qqs = []
        for qq_number, data in self.verification_messages.items():
            if current_time - data["timestamp"] > 60:  # 60秒过期
                expired_message_qqs.append(qq_number)
        
        for qq_number in expired_message_qqs:
            # 尝试撤回消息 - 使用调度器确保线程安全
            def create_retract_task(qq_num):
                def retract_expired():
                    try:
                        asyncio.run_coroutine_threadsafe(
                            self._delete_verification_message(qq_num),
                            self.plugin._loop
                        )
                    except Exception as e:
                        self.logger.warning(f"撤回过期验证码消息失败 (QQ {qq_num}): {e}")
                return retract_expired
            
            try:
                if hasattr(self.plugin, '_current_ws') and self.plugin._current_ws:
                    self.plugin.server.scheduler.run_task(
                        self.plugin,
                        create_retract_task(qq_number),
                        delay=1
                    )
            except Exception as e:
                self.logger.warning(f"调度撤回过期验证码消息失败: {e}")
            
            # 清理记录（注意：实际删除在 _delete_verification_message 中进行）
            self.logger.info(f"已调度撤回过期验证码消息: QQ {qq_number}")
        
        # 清理其他过期缓存
        self._cleanup_expired_caches(current_time)
    
    def _cleanup_expired_caches(self, current_time: float):
        """清理其他过期缓存"""
        # 清理表单显示缓存（5分钟过期）
        expired_form_cache = [name for name, timestamp in self.form_display_cache.items() 
                             if current_time - timestamp > 300]
        for name in expired_form_cache:
            del self.form_display_cache[name]
        
        # 清理待确认QQ信息（10分钟过期）
        expired_confirmations = [name for name, data in self.pending_qq_confirmations.items() 
                               if current_time - data["timestamp"] > 600]
        for name in expired_confirmations:
            del self.pending_qq_confirmations[name]
        
        # 清理绑定队列中的过期项（30分钟过期）
        self.binding_queue = [(p, q, t) for p, q, t in self.binding_queue 
                             if current_time - t <= 1800]
        
        # 清理验证码发送队列中的过期项（10分钟过期）
        self.verification_send_queue = [(p, q, c, a, t) for p, q, c, a, t in self.verification_send_queue 
                                       if current_time - t <= 600]
    
    def cleanup_player_data(self, player_name: str):
        """清理离线玩家的验证相关数据"""
        try:
            cache_cleaned = []
            
            # 清理表单显示缓存
            if player_name in self.form_display_cache:
                del self.form_display_cache[player_name]
                cache_cleaned.append("表单显示缓存")
            
            # 清理表单显示次数计数
            if player_name in self.form_display_count:
                del self.form_display_count[player_name]
                cache_cleaned.append("表单显示次数")
            
            # 清理待确认QQ信息
            if player_name in self.pending_qq_confirmations:
                del self.pending_qq_confirmations[player_name]
                cache_cleaned.append("待确认QQ信息")
            
            # 清理并发绑定集合
            if player_name in self.concurrent_bindings:
                self.concurrent_bindings.discard(player_name)
                cache_cleaned.append("并发绑定缓存")
            
            # 清理绑定队列
            original_queue_length = len(self.binding_queue)
            self.binding_queue = [(p, q, t) for p, q, t in self.binding_queue if p != player_name]
            if len(self.binding_queue) < original_queue_length:
                cache_cleaned.append("绑定队列")
            
            # 清理队列通知记录
            if player_name in self.queue_notification_sent:
                self.queue_notification_sent.discard(player_name)
                cache_cleaned.append("队列通知缓存")
            
            # 清理验证码发送队列
            original_verification_queue_length = len(self.verification_send_queue)
            self.verification_send_queue = [(p, q, c, a, t) for p, q, c, a, t in self.verification_send_queue 
                                          if p.name != player_name]
            if len(self.verification_send_queue) < original_verification_queue_length:
                cache_cleaned.append("验证码发送队列")
            
            # 清理待验证数据
            if player_name in self.pending_verifications:
                qq_number = self.pending_verifications[player_name].get("qq")
                del self.pending_verifications[player_name]
                
                # 同时清理对应的验证码
                if qq_number and qq_number in self.verification_codes:
                    del self.verification_codes[qq_number]
                
                cache_cleaned.append("验证数据缓存")
            
            if cache_cleaned:
                self.logger.info(f"已清理玩家 {player_name} 的验证缓存：{', '.join(cache_cleaned)}")
                
        except Exception as e:
            self.logger.warning(f"清理玩家 {player_name} 验证缓存时出错: {e}")
    
    async def _delete_verification_message(self, qq_number: str):
        """异步删除验证码消息"""
        try:
            if qq_number in self.verification_messages:
                message_info = self.verification_messages[qq_number]
                message_id = message_info.get("message_id")
                
                self.logger.info(f"尝试撤回QQ {qq_number} 的验证码消息，message_id: {message_id}")
                
                if message_id and hasattr(self.plugin, '_current_ws') and self.plugin._current_ws:
                    # 发送删除消息请求
                    payload = {
                        "action": "delete_msg",
                        "params": {
                            "message_id": message_id
                        },
                        "echo": f"delete_msg_{int(TimeUtils.get_timestamp())}"
                    }
                    await self.plugin._current_ws.send(json.dumps(payload))
                    self.logger.info(f"✅ 已发送撤回请求: QQ {qq_number}, message_id: {message_id}")
                else:
                    if not message_id:
                        self.logger.warning(f"❌ 无法撤回QQ {qq_number} 的验证码消息: message_id 为空")
                    elif not hasattr(self.plugin, '_current_ws') or not self.plugin._current_ws:
                        self.logger.warning(f"❌ 无法撤回QQ {qq_number} 的验证码消息: WebSocket 连接不可用")
                
                # 清理记录
                del self.verification_messages[qq_number]
            else:
                self.logger.warning(f"❌ 未找到QQ {qq_number} 的验证码消息记录，无法撤回")
                
        except Exception as e:
            self.logger.error(f"删除验证码消息失败: {e}")
    
    async def _handle_verification_success(self, player_name: str, qq_number: str):
        """处理验证成功后的撤回和播报 - 统一在 verification_manager 中处理"""
        try:
            # 1. 立即撤回验证码消息
            await self._delete_verification_message(qq_number)
            self.logger.info(f"验证成功，已撤回QQ {qq_number} 的验证码消息")
            
            # 2. 设置群昵称为游戏ID
            await self._set_group_card(qq_number, player_name)
            
            # 3. 发送绑定成功播报到群
            if hasattr(self.plugin, '_current_ws') and self.plugin._current_ws:
                # 检查是否启用了群昵称同步
                nickname_info = ""
                if self.plugin.config_manager.get_config("sync_group_card", True):
                    nickname_info = f"\n🏷️ 群昵称已设置为：{player_name}"
                
                success_message = f"\n🎉 已完成QQ绑定验证\n玩家ID：{player_name}\nQQ号：{qq_number}{nickname_info}"
                
                # 发送@播报消息到所有群组
                target_groups = self.plugin.config_manager.get_config("target_groups", [])
                # 添加类型转换，确保group_id为整数类型
                target_groups = [int(gid) for gid in target_groups]
                for group_id in target_groups:
                    payload = {
                        "action": "send_group_msg",
                        "params": {
                            "group_id": group_id,
                            "message": [
                                {"type": "at", "data": {"qq": qq_number}},
                                {"type": "text", "data": {"text": f" {success_message}"}}
                            ]
                        },
                        "echo": f"bind_success_msg_{int(TimeUtils.get_timestamp())}_{group_id}"
                    }
                    
                    await self.plugin._current_ws.send(json.dumps(payload))
                
                self.logger.info(f"已发送绑定成功播报: 玩家 {player_name} (QQ: {qq_number})")
            
        except Exception as e:
            self.logger.error(f"处理验证成功后续操作失败: {e}")
    
    async def _set_group_card(self, qq_number: str, player_name: str):
        """设置群昵称为游戏ID"""
        try:
            # 检查是否启用了群昵称同步
            if not self.plugin.config_manager.get_config("sync_group_card", True):
                self.logger.info(f"群昵称同步已禁用，跳过设置: QQ {qq_number}")
                return
            
            if not hasattr(self.plugin, '_current_ws') or not self.plugin._current_ws:
                self.logger.warning(f"❌ 无法设置群昵称: WebSocket 连接不可用")
                return
            
            target_groups = self.plugin.config_manager.get_config("target_groups", [])
            if not target_groups:
                self.logger.warning(f"❌ 无法设置群昵称: 未配置目标群组")
                return
            
            # 添加类型转换，确保group_id为整数类型
            target_groups = [int(gid) for gid in target_groups]
            
            # 构建设置群昵称的payload
            for group_id in target_groups:
                payload = {
                    "action": "set_group_card",
                    "params": {
                        "group_id": group_id,
                        "user_id": int(qq_number),
                        "card": player_name
                    },
                    "echo": f"set_group_card:{qq_number}:{player_name}:{group_id}"
                }
                
                await self.plugin._current_ws.send(json.dumps(payload))
            
            self.logger.info(f"📤 已发送设置群昵称请求: QQ {qq_number} -> {player_name}")
            
        except Exception as e:
            self.logger.error(f"❌ 设置群昵称失败: {e}")
    
    def handle_message_response(self, echo: str, message_id: int):
        """处理消息发送响应，保存消息ID"""
        try:
            if echo.startswith("verification_msg:"):
                qq_number = echo.split("verification_msg:")[1]
                if qq_number in self.verification_messages:
                    self.verification_messages[qq_number]["message_id"] = message_id
                    self.logger.info(f"✅ 已保存验证码消息ID: QQ {qq_number}, message_id {message_id}")
                else:
                    self.logger.warning(f"❌ 收到验证码消息ID，但找不到对应的QQ记录: {qq_number}")
        except Exception as e:
            self.logger.error(f"处理消息响应失败: {e}")
    
    def handle_api_response(self, echo: str, status: str, data: dict = None):
        """处理API操作响应"""
        try:
            if echo.startswith("set_group_card:"):
                # 解析echo: set_group_card:qq_number:player_name:group_id
                parts = echo.split(":", 3)
                if len(parts) >= 4:
                    qq_number = parts[1]
                    player_name = parts[2]
                    group_id = parts[3]
                    
                    if status == "ok":
                        self.logger.info(f"✅ 群昵称设置成功: QQ {qq_number} -> {player_name} (群 {group_id})")
                    else:
                        self.logger.warning(f"❌ 群昵称设置失败: QQ {qq_number} -> {player_name} (群 {group_id}), 状态: {status}")
                        if data:
                            self.logger.warning(f"错误详情: {data}")
                else:
                    self.logger.warning(f"❌ 解析set_group_card响应echo失败: {echo}")
        except Exception as e:
            self.logger.error(f"处理API响应失败: {e}")
    
    async def delete_verification_message_by_qq(self, qq_number: str):
        """根据QQ号删除验证码消息（公共接口）"""
        await self._delete_verification_message(qq_number)
    
    def store_verification_message(self, qq_number: str, message_id: int):
        """存储验证码消息ID用于后续撤回"""
        self.verification_messages[qq_number] = {
            "message_id": message_id,
            "timestamp": TimeUtils.get_timestamp(),
            "player_name": self.verification_codes.get(qq_number, {}).get("player_name", "")
        }
    
    def process_verification_send_queue(self):
        """处理验证码发送队列"""
        if not self.verification_send_queue:
            return
        
        current_time = TimeUtils.get_timestamp()
        
        # 检查发送间隔
        if current_time - self.last_verification_send_time < self.verification_send_interval:
            return
        
        # 获取队列中的第一个项目
        if self.verification_send_queue:
            player, qq_number, verification_code, attempt, timestamp = self.verification_send_queue.pop(0)
            
            try:
                # 检查玩家是否仍然有效
                if not self.plugin.is_valid_player(player):
                    self.logger.info(f"玩家 {player.name} 已离线，跳过验证码发送")
                    return
                
                # 发送验证码
                if hasattr(self.plugin, '_current_ws') and self.plugin._current_ws:
                    asyncio.run_coroutine_threadsafe(
                        self._send_verification_with_retry(
                            self.plugin._current_ws, 
                            int(qq_number), 
                            f"\n玩家ID：{player.name}\n🔐 QQ绑定验证码：{verification_code}\n⏰ 验证码60秒内有效\n💡 请在游戏中输入此验证码完成绑定\n📝 或直接在群内发送 /verify {verification_code}", 
                            player, 
                            verification_code, 
                            attempt
                        ),
                        self.plugin._loop
                    )
                    
                    self.last_verification_send_time = current_time
                    
                    # 通知玩家
                    ColorFormat = _get_color_format()
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}验证码已发送到QQ群！{ColorFormat.RESET}")
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}请查看群消息中的@提醒{ColorFormat.RESET}")
                    
                    # 显示验证码输入表单
                    if hasattr(self.plugin, 'show_verification_form'):
                        self.plugin.server.scheduler.run_task(
                            self.plugin,
                            lambda p=player: self.plugin.show_verification_form(p) if self.plugin.is_valid_player(p) else None,
                            delay=20  # 1秒延迟
                        )
                else:
                    ColorFormat = _get_color_format()
                    player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}服务器未连接到QQ群，无法发送验证码！{ColorFormat.RESET}")
                    self.unregister_verification_attempt(qq_number, player.name, False)
                
            except Exception as e:
                self.logger.error(f"处理验证码发送队列失败: {e}")
                # 重试机制
                if attempt < self.max_verification_retries:
                    self.verification_send_queue.append((
                        player, qq_number, verification_code, attempt + 1, timestamp
                    ))
                    self.logger.info(f"验证码发送失败，将重试 (尝试 {attempt + 1}/{self.max_verification_retries})")
                else:
                    self.logger.error(f"验证码发送达到最大重试次数，放弃发送")
                    self.unregister_verification_attempt(qq_number, player.name, False)
                    if self.plugin.is_valid_player(player):
                        ColorFormat = _get_color_format()
                        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证码发送失败，请稍后重试！{ColorFormat.RESET}")
    
    async def _send_verification_with_retry(self, ws, user_id: int, verification_text: str, player, verification_code: str, attempt: int):
        """异步发送验证码（带重试机制）"""
        try:
            target_groups = self.plugin.config_manager.get_config("target_groups", [])
            # 添加类型转换，确保group_id为整数类型
            target_groups = [int(gid) for gid in target_groups]
            qq_str = str(user_id)
            
            # 记录验证码消息等待回调
            self.verification_messages[qq_str] = {
                "echo": f"verification_msg:{qq_str}",
                "message_id": None,
                "timestamp": TimeUtils.get_timestamp(),
                "player_name": player.name
            }
            
            # 向所有目标群组发送验证码消息
            for group_id in target_groups:
                # 构建验证码消息payload
                payload = {
                    "action": "send_group_msg",
                    "params": {
                        "group_id": group_id,
                        "message": [
                            {"type": "at", "data": {"qq": qq_str}},
                            {"type": "text", "data": {"text": f" {verification_text}"}}
                        ]
                    },
                    "echo": f"verification_msg:{qq_str}:{group_id}"
                }
                
                await ws.send(json.dumps(payload))
            
            self.logger.info(f"验证码已发送给QQ {user_id} (玩家: {player.name})")
            
        except Exception as e:
            self.logger.error(f"发送验证码失败 (尝试 {attempt}): {e}")
            raise e