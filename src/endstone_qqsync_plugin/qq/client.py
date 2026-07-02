"""
WebSocket 客户端与网络层服务模块
"""

import asyncio
import json
import random
from typing import Any
from ..utils.imports import import_websockets
from ..utils.timing import Timing

websockets = import_websockets()


class WebSocketClient:
    """提供高韧性长连接、自动断线重连、写缓冲区补发的 OneBot v11 WebSocket 客户端"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.logger = plugin.logger

        self.ws: Any = None
        self._running = False

        # 网络连通状态 Event，用来做发送协程挂起
        self._connected_event = asyncio.Event()

        # 出队写缓冲区（限制大小 50，避免断线堆积耗尽内存）
        self.send_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=50)

    @property
    def is_connected(self) -> bool:
        """检查长连接是否处于开放状态 (state=1)"""
        return self.ws is not None and getattr(self.ws, "state", 0) == 1

    async def connect_forever(self) -> None:
        """无限重连事件循环主挂起协程"""
        if self._running:
            return
        self._running = True

        napcat_ws = self.plugin.config_manager.napcat_ws
        token = self.plugin.config_manager.access_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        self.logger.info(f"正在准备握手连接 OneBot WS 服务端: {napcat_ws}")

        attempt = 0
        # 启动出队发送常驻协程
        sender_task = asyncio.create_task(self._sender_loop())

        while self._running:
            try:
                async with websockets.connect(
                    napcat_ws,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=10,
                ) as websocket:
                    self.ws = websocket
                    self._connected_event.set()
                    attempt = 0  # 重置重连次数

                    self.logger.info("OneBot WS 连接握手已成功建立")

                    # 连接成功后，异步获取群成员列表缓存以做退群检测

                    async def init_data() -> None:
                        # 1. 发送群成员查询
                        for g in self.plugin.config_manager.groups:
                            await self.send_message(
                                {
                                    "action": "get_group_member_list",
                                    "params": {"group_id": g["id"]},
                                    "echo": f"get_group_member_list:{g['id']}",
                                }
                            )
                        # 2. 广播服务器启动通知
                        if hasattr(self.plugin, "_send_startup_message") and self.plugin._send_startup_message:
                            await self.broadcast_to_groups("[QQSync] 游戏服务器已启动！")
                            self.plugin._send_startup_message = False

                    asyncio.create_task(init_data())

                    # 常驻心跳与接收消息并行执行
                    await asyncio.gather(self._heartbeat(), self._receive_loop())

            except Exception as e:
                self.ws = None
                self._connected_event.clear()
                attempt += 1

                if self._running:
                    # 指数退避加随机扰动 Jitter
                    delay = min(30.0, 1.5**attempt + random.uniform(0.0, 1.0))
                    self.logger.warning(f"OneBot WS 连接遭遇异常 (第 {attempt} 次重试): {e}")
                    self.logger.info(f"将在 {delay:.2f} 秒后重新尝试建立握手...")
                    await asyncio.sleep(delay)
                else:
                    break

        sender_task.cancel()
        self.logger.info("OneBot WS 连接任务已完全终止")

    async def _heartbeat(self) -> None:
        """心跳检查"""
        try:
            while self._running and self.is_connected:
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass

    async def _sender_loop(self) -> None:
        """常驻消息出队发送循环（支持无锁挂起与断线缓冲区重压入）"""
        try:
            while self._running:
                # 等待网络连通
                await self._connected_event.wait()

                data = await self.send_queue.get()
                try:
                    if self.is_connected:
                        await self.ws.send(json.dumps(data))
                    else:
                        raise ConnectionError("网络已离线，暂存缓冲区")
                except Exception:
                    # 重新压回队列。若满，剔除最旧的一条，腾出空间压入当前失败消息
                    if self.send_queue.full():
                        try:
                            self.send_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    await self.send_queue.put(data)
                    await asyncio.sleep(2.0)  # 频控退避，防止死循环空耗
                finally:
                    try:
                        self.send_queue.task_done()
                    except ValueError:
                        pass
        except asyncio.CancelledError:
            pass

    async def _receive_loop(self) -> None:
        """长连接消息接收循环"""
        try:
            async for raw in self.ws:
                try:
                    data = json.loads(raw)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    self.logger.error(f"解析消息回包逻辑出错: {e}")
        except Exception:
            pass
        finally:
            self.ws = None
            self._connected_event.clear()

    async def send_message(self, payload: dict[str, Any]) -> None:
        """公开发送接口：向缓冲区队列投递原始 Payload 并触发异步发送"""
        if self.send_queue.full():
            try:
                self.send_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await self.send_queue.put(payload)

    async def send_group_message(self, group_id: int, message: Any) -> None:
        """向指定群聊投递 OneBot 发送数据包"""
        payload = {
            "action": "send_group_msg",
            "params": {"group_id": group_id, "message": message},
            "echo": f"group_msg:{group_id}:{Timing.get_timestamp()}",
        }
        await self.send_message(payload)

    async def broadcast_to_groups(self, message: Any, only_chat_enabled: bool = False) -> None:
        """遍历配置向所有满足条件的群组进行消息广播"""
        for g in self.plugin.config_manager.groups:
            if only_chat_enabled and not g.get("enable_chat", True):
                continue
            await self.send_group_message(g["id"], message)

    async def _handle_message(self, data: dict[str, Any]) -> None:
        """处理分发接收到的 OneBot 核心数据回包"""
        post_type = data.get("post_type")
        echo = data.get("echo", "")

        # 1. 过滤 API 结果回执
        if "echo" in data and data.get("status") == "ok":
            res_data = data.get("data", {})
            if isinstance(res_data, dict):
                msg_id = res_data.get("message_id")
                if msg_id and echo.startswith("verification_msg:"):
                    self.plugin.verification_manager.handle_message_response(echo, int(msg_id))

            # 更新群成员列表缓存回包
            if echo.startswith("get_group_member_list:"):
                group_id = int(echo.split(":")[1])
                member_list = data.get("data", [])
                if isinstance(member_list, list):
                    members = {str(m.get("user_id", "")) for m in member_list if m.get("user_id")}
                    self.plugin.group_members[group_id] = members
                    self.logger.info(f"已更新群聊 {group_id} 成员缓存，共计 {len(members)} 人")

            # 更新 QQ 个人用户信息昵称
            elif echo.startswith("fetch_nickname:"):
                # fetch_nickname:qq_number:timestamp
                qq = echo.split(":")[1]
                nickname = res_data.get("nickname", "未知昵称")
                # 更新至验证模块的 pending_qq_confirmations 缓存
                if qq and nickname:
                    for p_name, cache in self.plugin.verification_manager.pending_qq_confirmations.items():
                        if cache.get("qq") == qq:
                            cache["nickname"] = nickname
                            break

        # 2. 消息事件处理
        elif post_type == "message":
            if data.get("message_type") == "group":
                group_id = int(data.get("group_id", 0))
                user_id = int(data.get("user_id", 0))
                raw_msg = data.get("raw_message", "")
                sender = data.get("sender", {})
                nickname = sender.get("nickname", "未知")
                card = sender.get("card", "")

                target_gids = [g["id"] for g in self.plugin.config_manager.groups]
                if group_id not in target_gids:
                    return

                display_name = card if card else nickname
                bound_player = self.plugin.data_manager.get_qq_player(str(user_id))
                if bound_player:
                    display_name = bound_player

                self.logger.info(f"[MSG] [群 {group_id}] [QQ {user_id}] [{card if card else nickname}] - {raw_msg}")

                # 6位数字且有验证状态 -> 触发快捷验证码核对
                if raw_msg.isdigit() and len(raw_msg) == 6:
                    qq_str = str(user_id)
                    if qq_str in self.plugin.verification_manager.verification_codes:
                        reply = await self.plugin.group_command_handler.cmd_verify(self, user_id, [raw_msg], group_id, display_name)
                        if reply:
                            await self.send_group_message(group_id, f"@{display_name} {reply}")
                        return

                # 命令事件分发
                if raw_msg.startswith("/") or raw_msg.split()[0] in ["verify", "bind"]:
                    # 判断该群是否允许执行命令
                    for g in self.plugin.config_manager.groups:
                        if g["id"] == group_id and g.get("enable_command", True):
                            await self.plugin.group_command_handler.handle_command(self, user_id, raw_msg, display_name, group_id)
                            break
                    return

                # 聊天消息同步到游戏
                if self.plugin.config_manager.enable_qq_to_game:
                    for g in self.plugin.config_manager.groups:
                        if g["id"] == group_id and g.get("enable_chat", True):
                            # 流经中间件责任链处理
                            filtered, _ = self.plugin.msg_pipeline.filter_message(
                                text=raw_msg,
                                direction="qq_to_game",
                                max_length=150,
                            )
                            if filtered and filtered != "[空消息]":
                                game_msg = f"[{g['name']}] {display_name}: {filtered}" if g.get("name") else f"{display_name}: {filtered}"
                                from endstone import ColorFormat

                                def push_game() -> None:
                                    formatted = f"{ColorFormat.GREEN}[QQ群] {ColorFormat.AQUA}{game_msg}{ColorFormat.RESET}"
                                    for p in self.plugin.server.online_players:
                                        p.send_message(formatted)

                                self.plugin.server.scheduler.run_task(self.plugin, push_game, delay=1)
                            break

        # 3. 成员变动Notice通知事件处理
        elif post_type == "notice":
            notice_type = data.get("notice_type")
            user_id = str(data.get("user_id", ""))
            group_id = int(data.get("group_id", 0))

            target_gids = [g["id"] for g in self.plugin.config_manager.groups]
            if group_id not in target_gids:
                return

            if notice_type == "group_increase":
                if group_id in self.plugin.group_members:
                    self.plugin.group_members[group_id].add(user_id)
                self.logger.info(f"QQ用户 {user_id} 加入群聊 {group_id}")

            elif notice_type == "group_decrease":
                if group_id in self.plugin.group_members:
                    self.plugin.group_members[group_id].discard(user_id)
                self.logger.info(f"QQ用户 {user_id} 退出群聊 {group_id}")

                # 重新应用权限策略以应对可能退群的绑定玩家
                bound_player = self.plugin.data_manager.get_qq_player(user_id)
                if bound_player:
                    def force_apply() -> None:
                        for p in self.plugin.server.online_players:
                            if p.name == bound_player:
                                self.plugin.permission_manager.check_and_apply_permissions(p)
                                break
                    self.plugin.server.scheduler.run_task(self.plugin, force_apply, delay=1)

    def stop(self) -> None:
        """优雅下线：断开 WebSocket，通知缓冲写协程退出"""
        self.logger.info("正在安全断开 OneBot WS 客户端连接...")
        self._running = False
        self._connected_event.clear()

        # 取消主异步任务
        if hasattr(self.plugin, "_task") and self.plugin._task:
            try:
                self.plugin._task.cancel()
            except Exception:
                pass

        if self.ws:
            try:
                if hasattr(self.plugin, "_loop") and self.plugin._loop and self.plugin._loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.ws.close(), self.plugin._loop)
            except Exception as e:
                self.logger.warning(f"关闭 WebSocket 连接时发生异常: {e}")

        self.ws = None
