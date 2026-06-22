"""
WebSocket客户端模块
负责与QQ机器人的WebSocket连接管理
"""

import asyncio
import json
from typing import Optional, TYPE_CHECKING

# 导入websockets库（通过统一的导入工具）
from ..utils.imports import import_websockets
websockets = import_websockets()

if TYPE_CHECKING:
    from websockets import WebSocketServerProtocol


class WebSocketClient:
    """WebSocket客户端"""
    
    def __init__(self, plugin):
        self.plugin = plugin
        self.logger = plugin.logger
        self.ws: Optional['WebSocketServerProtocol'] = None
        self._running = False
    
    async def connect_forever(self):
        """持续连接NapCat WS"""
        if self._running:
            self.logger.warning("NapCat WS 客户端已在运行")
            return
            
        self._running = True
        
        # 获取配置
        napcat_ws = self.plugin.config_manager.get_config("napcat_ws")
        access_token = self.plugin.config_manager.get_config("access_token")
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
        
        self.logger.info("QQsync 启动，准备连接 NapCat WS…")
        self.logger.info(f"连接地址: {napcat_ws}")
        
        delay = 1
        consecutive_failures = 0
        max_consecutive_failures = 5
        
        while self._running:
            try:
                # 使用旧版本的连接参数
                async with websockets.connect(
                    napcat_ws, 
                    additional_headers=headers,
                    ping_interval=20,  # 20秒ping间隔
                    ping_timeout=10,   # 10秒ping超时
                    close_timeout=10   # 10秒关闭超时
                ) as websocket:
                    self.ws = websocket
                    self.plugin._current_ws = websocket
                    consecutive_failures = 0  # 重置失败计数
                    
                    self.logger.info("已连接 NapCat WS")
                    
                    # 连接成功后立即获取所有群成员列表
                    try:
                        from .handlers import get_all_groups_member_list
                        await get_all_groups_member_list(websocket)
                    except Exception as e:
                        self.logger.warning(f"获取群成员列表失败: {e}")
                    
                    # 发送服务器启动消息（如果插件刚启动）
                    try:
                        if hasattr(self.plugin, '_send_startup_message') and self.plugin._send_startup_message:
                            from .handlers import send_group_msg_to_all_groups
                            server_start_msg = "[QQSync] 服务器已启动！"
                            await send_group_msg_to_all_groups(websocket, server_start_msg)
                            self.plugin._send_startup_message = False  # 只发送一次
                    except Exception as e:
                        self.logger.warning(f"发送启动消息失败: {e}")
                    
                    # 启动心跳和消息处理
                    await asyncio.gather(
                        self._heartbeat(),
                        self._message_loop()
                    )
                    
            except Exception as e:
                self.ws = None
                self.plugin._current_ws = None
                consecutive_failures += 1
                
                if self._running:
                    # 根据连续失败次数调整重连策略
                    if consecutive_failures <= max_consecutive_failures:
                        self.logger.warning(f"NapCat WS 连接失败 (尝试{consecutive_failures}/{max_consecutive_failures}): {e}")
                        # 指数退避，但最大不超过30秒
                        delay = min(30, delay * 1.5 if consecutive_failures > 1 else 5)
                    else:
                        self.logger.error(f"NapCat WS 连接持续失败，暂停重连30秒: {e}")
                        delay = 30  # 持续失败时使用固定延迟
                        consecutive_failures = 0  # 重置计数，给系统恢复机会
                    
                    self.logger.info(f"🔄 将在 {delay:.1f} 秒后重试连接...")
                    await asyncio.sleep(delay)
                else:
                    break
            else:
                delay = 1

        self.logger.info("NapCat WS 客户端已停止运行")

    async def _heartbeat(self):
        """发送心跳包 - 使用state属性检查连接状态"""
        try:
            while self._running and self.ws:
                try:
                    # 直接使用state属性检查连接状态（1=OPEN）
                    if self.ws.state != 1:
                        break
                    
                    # 简单的心跳，30秒间隔
                    await asyncio.sleep(30)
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as e:
                    self.logger.warning(f"心跳失败: {e}")
                    raise
        except asyncio.CancelledError:
            pass
    
    async def _message_loop(self):
        """消息处理循环"""
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    self.logger.warning(f"收到无效的JSON消息: {message}")
                except Exception as e:
                    self.logger.error(f"处理消息失败: {e}")
        except websockets.exceptions.ConnectionClosed:
            self.logger.warning("NapCat WS 连接已关闭")
        except Exception as e:
            self.logger.error(f"消息循环错误: {e}")
    
    async def _handle_message(self, data: dict):
        """处理接收到的消息"""
        try:
            # 导入处理函数
            from .handlers import handle_message, handle_api_response, handle_group_member_change
            
            # 消息类型判断
            post_type = data.get("post_type")
            
            # 处理普通消息事件
            if post_type == "message":
                await handle_message(self.ws, data)
            # 处理API响应（包含echo字段）
            elif "echo" in data:
                await handle_api_response(data)
            # 处理通知事件（群成员变动等）
            elif post_type == "notice":
                notice_type = data.get("notice_type")
                if notice_type in ["group_increase", "group_decrease"]:
                    await handle_group_member_change(data)
                else:
                    self.logger.debug(f"未处理的通知类型: {notice_type}")
            # 处理元事件（心跳响应等）
            elif post_type == "meta_event":
                meta_event_type = data.get("meta_event_type")
                if meta_event_type == "heartbeat":
                    self.logger.debug("收到心跳响应")
                elif meta_event_type == "lifecycle":
                    sub_type = data.get("sub_type", "")
                    self_id = data.get("self_id", "")
                    if sub_type == "connect":
                        self.logger.info(f"OneBot v11 连接确认 (Bot ID: {self_id})")
                    elif sub_type == "enable":
                        self.logger.info(f"OneBot v11 已启用 (Bot ID: {self_id})")
                    elif sub_type == "disable":
                        self.logger.warning(f"OneBot v11 已禁用 (Bot ID: {self_id})")
                    else:
                        self.logger.info(f"OneBot v11 生命周期事件: {sub_type} (Bot ID: {self_id})")
                else:
                    self.logger.info(f"未处理的元事件类型: {meta_event_type}")
            # 处理请求事件
            elif post_type == "request":
                request_type = data.get("request_type")
                self.logger.info(f"收到请求事件: {request_type}")
            else:
                self.logger.debug(f"未识别的消息类型: {data}")
                
        except Exception as e:
            self.logger.error(f"处理消息失败: {e}")
    
    def stop(self):
        """停止WebSocket连接"""
        self.logger.info("正在停止 NapCat WS 客户端")
        self._running = False
        
        # 1. 显式取消处于休眠挂起中的重载/重连主协程
        if hasattr(self.plugin, '_task') and self.plugin._task:
            try:
                self.plugin._task.cancel()
            except Exception:
                pass
                
        if self.ws:
            try:
                # 检查子线程的事件循环是否在运行，进行线程安全跨线程关闭投递
                if hasattr(self.plugin, '_loop') and self.plugin._loop and self.plugin._loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.ws.close(), self.plugin._loop)
                    self.logger.info("已提交线程安全的关闭 WebSocket 连接协程任务")
                else:
                    self.logger.warning("子线程事件循环未在运行，将放弃优雅关闭 WebSocket 连接")
            except Exception as e:
                self.logger.warning(f"停止WebSocket客户端时出错: {e}")
        
        self.ws = None
        self.plugin._current_ws = None
    
    @property 
    def is_connected(self) -> bool:
        """检查是否已连接 - 直接使用state属性"""
        if self.ws is None:
            return False
        return self.ws.state == 1  # 1 = OPEN状态
    
    async def send_message(self, data: dict):
        """发送消息 - 直接使用state属性"""
        if not self.ws:
            raise ConnectionError("NapCat WS 未连接")
        
        # 检查连接状态（1 = OPEN状态）
        if self.ws.state != 1:
            raise ConnectionError("NapCat WS 未处于开放状态")
        
        await self.ws.send(json.dumps(data))