"""
WebSocket 客户端与网络层服务模块
"""

import asyncio
import json
import random
from typing import Any
from ..utils.imports import import_websockets
from ..utils.timing import Timing
from .commands import CommandContext
from ..core.verification import VERIFICATION_CODE_LENGTH

# 发送队列缓冲上限
_SEND_QUEUE_MAXSIZE = 50
# 心跳间隔（秒）
_HEARTBEAT_INTERVAL_SECONDS = 30
# 重连退避基数与上限（秒）
_RECONNECT_BACKOFF_BASE = 1.5
_RECONNECT_BACKOFF_MAX_SECONDS = 30.0
# 转发到游戏内的聊天文本截断长度
_GAME_CHAT_MAX_LENGTH = 150

websockets = import_websockets()


class WebSocketClient:
    """OneBot v11 WebSocket 客户端，支持断线重连与发送缓冲"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.logger = plugin.logger

        self.ws: Any = None
        self._running = False

        # 网络连通状态 Event，用来做发送协程挂起
        self._connected_event = asyncio.Event()

        # 出队写缓冲区（限制大小 50，避免断线堆积耗尽内存）
        self.send_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_SEND_QUEUE_MAXSIZE)

    @property
    def is_connected(self) -> bool:
        """检查长连接是否处于开放状态 (state=1)"""
        return self.ws is not None and getattr(self.ws, "state", 0) == 1

    async def connect_forever(self) -> None:
        """WebSocket 无限重连主循环"""
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
            attempt = await self._run_connection_attempt(napcat_ws, headers, attempt)
            if attempt is None:
                break

        sender_task.cancel()
        self.logger.info("OneBot WS 连接任务已完全终止")

    async def _run_connection_attempt(self, napcat_ws: str, headers: dict[str, str], attempt: int) -> int | None:
        """执行一次握手连接并维持收发；返回新的重试次数，None 表示退出重连"""
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

                self.logger.info("OneBot WS 连接已建立")

                # 连接建立后拉取群成员列表用于退群检测
                asyncio.create_task(self._initialize_session())

                # 常驻心跳与接收消息并行执行
                await asyncio.gather(self._heartbeat(), self._receive_loop())

            return attempt
        except Exception as e:
            self.ws = None
            self._connected_event.clear()
            return await self._handle_connection_error(e, attempt)

    async def _handle_connection_error(self, error: Exception, attempt: int) -> int | None:
        """累计重试次数并按退避等待；返回新的重试次数，None 表示退出重连"""
        attempt += 1

        if not self._running:
            return None

        # 指数退避加随机扰动 Jitter
        delay = min(_RECONNECT_BACKOFF_MAX_SECONDS, _RECONNECT_BACKOFF_BASE**attempt + random.uniform(0.0, 1.0))
        self.logger.warning(f"OneBot WS 连接遭遇异常 (第 {attempt} 次重试): {error}")
        self.logger.info(f"将在 {delay:.2f} 秒后重新尝试建立握手...")
        await asyncio.sleep(delay)
        return attempt

    async def _initialize_session(self) -> None:
        """连接建立后拉取群成员列表缓存并广播服务器启动通知"""
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

    async def _heartbeat(self) -> None:
        """心跳检查"""
        try:
            while self._running and self.is_connected:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return

    async def _sender_loop(self) -> None:
        """消息出队发送循环"""
        try:
            while self._running:
                # 等待网络连通
                await self._connected_event.wait()

                await self._flush_one_payload()
        except asyncio.CancelledError:
            return

    async def _flush_one_payload(self) -> None:
        """取出队首数据包发送，失败则重新压回缓冲区"""
        data = await self.send_queue.get()
        try:
            if self.is_connected:
                await self.ws.send(json.dumps(data))
            else:
                raise ConnectionError("网络已离线，暂存缓冲区")
        except Exception:
            await self._requeue_failed_payload(data)
        finally:
            self._mark_payload_done()

    async def _requeue_failed_payload(self, data: dict[str, Any]) -> None:
        """重新压回队列。若满，剔除最旧的一条，腾出空间压入当前失败消息"""
        if self.send_queue.full():
            self._discard_oldest_payload()
        await self.send_queue.put(data)
        await asyncio.sleep(2.0)  # 频控退避，防止死循环空耗

    def _mark_payload_done(self) -> None:
        """标记队列中一条消息已处理完毕"""
        try:
            self.send_queue.task_done()
        except ValueError:
            self.logger.debug("发送队列计数已归零，跳过 task_done")

    def _discard_oldest_payload(self) -> None:
        """队列满时剔除最旧的一条消息腾出空间"""
        try:
            self.send_queue.get_nowait()
        except asyncio.QueueEmpty:
            self.logger.debug("发送队列已空，跳过丢弃最旧消息")

    async def _receive_loop(self) -> None:
        """长连接消息接收循环"""
        try:
            async for raw in self.ws:
                await self._dispatch_raw_message(raw)
        except Exception as e:
            self.logger.warning(f"WS 接收循环异常终止: {e}")
        finally:
            self.ws = None
            self._connected_event.clear()

    async def _dispatch_raw_message(self, raw: Any) -> None:
        """解析单条原始回包并交由消息处理逻辑分发"""
        try:
            data = json.loads(raw)
            await self._handle_message(data)
        except json.JSONDecodeError:
            self.logger.debug("收到非 JSON 格式回包，已忽略")
        except Exception as e:
            self.logger.error(f"解析消息回包逻辑出错: {e}")

    async def send_message(self, payload: dict[str, Any]) -> None:
        """公开发送接口：向缓冲区队列投递原始 Payload 并触发异步发送"""
        if self.send_queue.full():
            self._discard_oldest_payload()
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
        # 1. 过滤 API 结果回执
        if "echo" in data and data.get("status") == "ok":
            self._handle_api_response(data, data.get("echo", ""))
            return

        post_type = data.get("post_type")

        # 2. 消息事件处理
        if post_type == "message":
            if data.get("message_type") == "group":
                await self._handle_group_message(data)
            return

        # 3. 成员变动 Notice 通知事件处理
        if post_type == "notice":
            await self._handle_notice(data)

    def _handle_api_response(self, data: dict[str, Any], echo: str) -> None:
        """处理 API 调用结果回执：验证回执、群成员缓存、昵称缓存"""
        res_data = data.get("data", {})
        self._cache_verification_message_id(res_data, echo)

        if echo.startswith("get_group_member_list:"):
            self._cache_group_members(data, echo)
        elif echo.startswith("fetch_nickname:"):
            self._cache_pending_nickname(res_data, echo)

    def _cache_verification_message_id(self, res_data: Any, echo: str) -> None:
        """缓存验证码消息 ID 供后续撤回"""
        if not isinstance(res_data, dict):
            return

        msg_id = res_data.get("message_id")
        if msg_id and echo.startswith("verification_msg:"):
            self.plugin.verification_manager.handle_message_response(echo, int(msg_id))

    def _cache_group_members(self, data: dict[str, Any], echo: str) -> None:
        """缓存群成员列表用于退群检测"""
        group_id = int(echo.split(":")[1])
        member_list = data.get("data", [])
        if not isinstance(member_list, list):
            return

        members = {str(m.get("user_id", "")) for m in member_list if m.get("user_id")}
        self.plugin.group_members[group_id] = members
        self.logger.info(f"已更新群聊 {group_id} 成员缓存，共计 {len(members)} 人")

    def _cache_pending_nickname(self, res_data: Any, echo: str) -> None:
        """缓存待确认 QQ 的昵称"""
        # 回执格式：fetch_nickname:QQ号:时间戳
        qq = echo.split(":")[1]
        nickname = res_data.get("nickname", "未知昵称")
        if qq and nickname:
            self._update_pending_nickname(qq, nickname)

    def _update_pending_nickname(self, qq: str, nickname: str) -> None:
        """更新验证模块中待确认 QQ 的昵称缓存"""
        for p_name, cache in self.plugin.verification_manager.pending_qq_confirmations.items():
            if cache.get("qq") == qq:
                cache["nickname"] = nickname
                break

    async def _handle_group_message(self, data: dict[str, Any]) -> None:
        """处理群聊消息事件：快捷验证、命令分发、聊天同步"""
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
        if raw_msg.isdigit() and len(raw_msg) == VERIFICATION_CODE_LENGTH:
            if await self._try_quick_verify(user_id, raw_msg, group_id, display_name):
                return

        # 命令事件分发
        if raw_msg.startswith("/") or raw_msg.split()[0] in ["verify", "bind"]:
            await self._dispatch_group_command(user_id, raw_msg, group_id, display_name)
            return

        # 聊天消息同步到游戏
        if self.plugin.config_manager.enable_qq_to_game:
            self._sync_group_chat_to_game(raw_msg, group_id, display_name)

    async def _try_quick_verify(self, user_id: int, raw_msg: str, group_id: int, display_name: str) -> bool:
        """核对快捷验证码并回执，命中验证码时返回 True"""
        qq_str = str(user_id)
        if qq_str in self.plugin.verification_manager.verification_codes:
            ctx = CommandContext(
                ws_client=self,
                user_id=user_id,
                group_id=group_id,
                display_name=display_name,
                raw_msg=raw_msg,
                args=[raw_msg],
            )
            reply = await self.plugin.group_command_handler.cmd_verify(ctx)
            if reply:
                await self.send_group_message(group_id, f"@{display_name} {reply}")
            return True
        return False

    async def _dispatch_group_command(self, user_id: int, raw_msg: str, group_id: int, display_name: str) -> None:
        """在允许执行命令的群内分发命令事件"""
        # 判断该群是否允许执行命令
        for g in self.plugin.config_manager.groups:
            if g["id"] == group_id and g.get("enable_command", True):
                ctx = CommandContext(
                    ws_client=self,
                    user_id=user_id,
                    group_id=group_id,
                    display_name=display_name,
                    raw_msg=raw_msg,
                )
                await self.plugin.group_command_handler.handle_command(ctx)
                break

    def _sync_group_chat_to_game(self, raw_msg: str, group_id: int, display_name: str) -> None:
        """将开启聊天同步的群消息同步到游戏"""
        for g in self.plugin.config_manager.groups:
            if g["id"] == group_id and g.get("enable_chat", True):
                self._push_group_chat_to_game(g, raw_msg, display_name)
                break

    def _push_group_chat_to_game(self, group: dict[str, Any], raw_msg: str, display_name: str) -> None:
        """过滤群聊消息并调度游戏内广播"""
        # 流经中间件责任链处理
        filtered, _ = self.plugin.msg_pipeline.filter_message(
            text=raw_msg,
            direction="qq_to_game",
            max_length=_GAME_CHAT_MAX_LENGTH,
        )
        if filtered and filtered != "[空消息]":
            game_msg = f"[{group['name']}] {display_name}: {filtered}" if group.get("name") else f"{display_name}: {filtered}"
            from endstone import ColorFormat

            def push_game() -> None:
                formatted = f"{ColorFormat.GREEN}[QQ群] {ColorFormat.AQUA}{game_msg}{ColorFormat.RESET}"
                for p in self.plugin.server.online_players:
                    p.send_message(formatted)

            self.plugin.server.scheduler.run_task(self.plugin, push_game, delay=1)

    async def _handle_notice(self, data: dict[str, Any]) -> None:
        """处理群成员变动的 Notice 通知事件"""
        notice_type = data.get("notice_type")
        user_id = str(data.get("user_id", ""))
        group_id = int(data.get("group_id", 0))

        target_gids = [g["id"] for g in self.plugin.config_manager.groups]
        if group_id not in target_gids:
            return

        if notice_type == "group_increase":
            self._mark_group_member_joined(group_id, user_id)
        elif notice_type == "group_decrease":
            self._mark_group_member_left(group_id, user_id)

    def _mark_group_member_joined(self, group_id: int, user_id: str) -> None:
        """记录群成员加入缓存"""
        if group_id in self.plugin.group_members:
            self.plugin.group_members[group_id].add(user_id)
        self.logger.info(f"QQ用户 {user_id} 加入群聊 {group_id}")

    def _mark_group_member_left(self, group_id: int, user_id: str) -> None:
        """记录群成员退出缓存，并重算其绑定玩家的权限"""
        if group_id in self.plugin.group_members:
            self.plugin.group_members[group_id].discard(user_id)
        self.logger.info(f"QQ用户 {user_id} 退出群聊 {group_id}")

        # 重新应用权限策略以应对可能退群的绑定玩家
        bound_player = self.plugin.data_manager.get_qq_player(user_id)
        if bound_player:
            self._schedule_permission_reapply(bound_player)

    def _schedule_permission_reapply(self, bound_player: str) -> None:
        """调度退群绑定玩家的权限重新应用"""
        def force_apply() -> None:
            for p in self.plugin.server.online_players:
                if p.name == bound_player:
                    self.plugin.permission_manager.check_and_apply_permissions(p)
                    break
        self.plugin.server.scheduler.run_task(self.plugin, force_apply, delay=1)

    def stop(self) -> None:
        """断开 WebSocket 并停止发送协程"""
        self.logger.info("正在断开 OneBot WS 连接...")
        self._running = False
        self._connected_event.clear()

        # 取消主异步任务
        self._cancel_plugin_task()
        self._close_websocket()

        self.ws = None

    def _cancel_plugin_task(self) -> None:
        """取消插件持有的主异步任务"""
        if not (hasattr(self.plugin, "_task") and self.plugin._task):
            return
        try:
            self.plugin._task.cancel()
        except Exception as e:
            self.logger.debug(f"主异步任务已结束，无需取消: {e}")

    def _close_websocket(self) -> None:
        """在事件循环中关闭 WebSocket 连接"""
        if not self.ws:
            return
        try:
            if hasattr(self.plugin, "_loop") and self.plugin._loop and self.plugin._loop.is_running():
                asyncio.run_coroutine_threadsafe(self.ws.close(), self.plugin._loop)
        except Exception as e:
            self.logger.warning(f"关闭 WebSocket 连接时发生异常: {e}")
