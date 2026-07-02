"""
绑定验证码管理模块
"""

import asyncio
import random
from typing import Any
from ..utils.timing import Timing


class Verification:
    """负责验证码生命周期、防抖频控发送队列、绑定频次限制以及消息撤回逻辑"""

    def __init__(self, plugin: Any, logger: Any) -> None:
        self.plugin = plugin
        self.logger = logger

        # 验证码缓冲数据集
        self.pending_verifications: dict[str, dict[str, Any]] = {}  # 玩家名 -> 待核对信息
        self.verification_codes: dict[str, dict[str, Any]] = {}     # QQ号 -> 待核对信息
        self.verification_messages: dict[str, dict[str, Any]] = {}  # QQ号 -> 发送的消息ID列表
        self.player_bind_attempts: dict[str, float] = {}            # 玩家名 -> 上次申请时间

        # 频控与并发限制数据
        self.verification_queue: dict[str, float] = {}              # QQ号 -> 验证码申请时间
        self.binding_rate_limit: dict[str, float] = {}              # QQ号 -> 失败惩罚锁结束时间
        self.pending_qq_confirmations: dict[str, dict[str, Any]] = {} # 玩家名 -> 待确认的QQ与昵称临时快照
        self.concurrent_bindings: set[str] = set()                  # 当前正处于绑定表单操作中的玩家

        # 发送处理队列
        self.verification_send_queue: list[tuple[Any, str, str, str, int, float]] = []
        self.max_concurrent_bindings = 25
        self.binding_cooldown = 10
        self.max_verification_retries = 3
        self.verification_send_interval = 2.0
        self.last_verification_send_time = 0.0

        # 验证尝试错误次数计数
        self.unified_verification_attempts: dict[str, int] = {}
        self.player_verification_cooldown: dict[str, float] = {}

    def can_send_verification(self, qq_number: str, player_name: str) -> tuple[bool, str]:
        """频控核验：检查当前玩家和QQ号是否可以申请发送验证码"""
        now = Timing.get_timestamp()

        # 60秒申请间隔频控
        if player_name in self.pending_verifications:
            last_t = self.pending_verifications[player_name].get("timestamp", 0)
            cooldown = 60 - (now - last_t)
            if cooldown > 0:
                return False, f"您在60秒内仅能申请一次验证码，请等待 {cooldown} 秒后再试"

        # 验证错误惩罚冷却
        if player_name in self.player_verification_cooldown:
            cooldown = 60 - (now - self.player_verification_cooldown[player_name])
            if cooldown > 0:
                return False, f"验证码输入错误超限冷却中，请等待 {cooldown} 秒后再试"

        # QQ号码临时风控冷却
        if qq_number in self.binding_rate_limit:
            cooldown = self.binding_cooldown - (now - self.binding_rate_limit[qq_number])
            if cooldown > 0:
                return False, f"该QQ号码正在尝试绑定，请等待 {int(cooldown)} 秒重试"

        # 全服并发绑定风控限制
        if len(self.concurrent_bindings) >= self.max_concurrent_bindings:
            return False, "当前系统处理的绑定请求过多，请稍候重试"

        return True, ""

    def register_verification_attempt(self, qq_number: str, player_name: str) -> None:
        """为玩家申请绑定注册状态与风控标签"""
        now = Timing.get_timestamp()
        self.verification_queue[qq_number] = now
        self.concurrent_bindings.add(player_name)

        # 自动回收超过 5 分钟的冗余过期尝试记录，防止缓存泄露
        expired = [qq for qq, t in self.verification_queue.items() if now - t > 300]
        for qq in expired:
            self.verification_queue.pop(qq, None)

    def unregister_verification_attempt(self, qq_number: str, player_name: str, success: bool = True) -> None:
        """注销或完成玩家的绑定操作状态"""
        self.verification_queue.pop(qq_number, None)
        self.concurrent_bindings.discard(player_name)
        if not success:
            self.binding_rate_limit[qq_number] = Timing.get_timestamp()

    def cleanup_old_verification(self, player_name: str) -> None:
        """清理指定玩家现存的挂起验证码"""
        if player_name in self.pending_verifications:
            old_qq = self.pending_verifications[player_name].get("qq")
            del self.pending_verifications[player_name]
            if old_qq:
                self.verification_codes.pop(old_qq, None)

    def cleanup_qq_old_verifications(self, qq_number: str) -> None:
        """清理与该QQ号绑定的所有挂起验证状态"""
        if qq_number in self.verification_codes:
            player = self.verification_codes[qq_number].get("player_name")
            del self.verification_codes[qq_number]
            if player:
                self.pending_verifications.pop(player, None)

    def generate_verification_code(self, player: Any, qq_number: str, nickname: str = "未知昵称") -> bool:
        """为主在线玩家针对目标QQ号创生并缓存6位纯数字验证码，并插入队列"""
        try:
            can_send, err = self.can_send_verification(qq_number, player.name)
            if not can_send:
                player.send_message(f"[QQsync] [风控] {err}")
                return False

            self.cleanup_expired_verifications()
            self.cleanup_qq_old_verifications(qq_number)

            self.register_verification_attempt(qq_number, player.name)

            now = Timing.get_timestamp()
            self.player_bind_attempts[player.name] = now
            code = str(random.randint(100000, 999999))

            self.pending_verifications[player.name] = {
                "qq": qq_number,
                "code": code,
                "timestamp": now,
                "player_xuid": player.xuid,
            }

            self.verification_codes[qq_number] = {
                "code": code,
                "timestamp": now,
                "player_name": player.name,
            }

            # 打印控制台明文日志，便于无端管理核实
            from endstone import ColorFormat

            self.logger.info(
                f"{ColorFormat.AQUA}[验证码申请] 玩家: {ColorFormat.WHITE}{player.name}{ColorFormat.AQUA} "
                f"| QQ: {ColorFormat.WHITE}{qq_number}{ColorFormat.AQUA} | 验证码: {ColorFormat.YELLOW}{code}{ColorFormat.RESET}"
            )

            # 压入排队等待异步发送
            self.verification_send_queue.append((player, player.name, qq_number, code, 1, now))
            return True
        except Exception as e:
            self.logger.error(f"创建验证码遇到内部错误: {e}")
            self.unregister_verification_attempt(qq_number, player.name, False)
            return False

    def verify_code(self, player_name: str, player_xuid: str, input_code: str, source: str = "game") -> tuple[bool, str, dict[str, Any]]:
        """
        校验验证码

        Returns:
            tuple[bool, str, dict]: (是否成功, 回馈描述, 待确认绑定字典)
        """
        if player_name not in self.pending_verifications:
            return False, "验证请求已过期或不存在，请重新使用 /bindqq 开始绑定", {}

        pending = self.pending_verifications[player_name]

        # 身份核验，防止多角色混淆或伪装注入
        if pending.get("player_xuid") and pending["player_xuid"] != player_xuid:
            return False, "验证安全核对失败: 角色身份不匹配，绑定已强制关闭", {}

        # 60秒硬性有效期检验
        now = Timing.get_timestamp()
        qq_number = pending["qq"]
        if now - pending["timestamp"] > 60:
            self.pending_verifications.pop(player_name, None)
            self.verification_codes.pop(qq_number, None)
            return False, "验证码已过期，请重新申请", {}

        if not input_code or not input_code.isdigit() or len(input_code) != 6:
            return False, "请输入格式正确的 6 位纯数字验证码！", {}

        if input_code == pending["code"]:
            # 使用过的一键销毁
            if qq_number in self.verification_codes and self.verification_codes[qq_number].get("used", False):
                return False, "验证码已被激活使用，请重新申请", {}

            if qq_number in self.verification_codes:
                self.verification_codes[qq_number]["used"] = True

            # 异步调度删除QQ消息和广播通知
            def on_success() -> None:
                if self.plugin.ws_client and self.plugin.ws_client.is_connected:
                    asyncio.run_coroutine_threadsafe(
                        self._handle_verification_success(player_name, qq_number),
                        self.plugin._loop,
                    )

            self.plugin.server.scheduler.run_task(self.plugin, on_success, delay=1)

            # 数据清除
            self.pending_verifications.pop(player_name, None)
            self.verification_codes.pop(qq_number, None)
            self.unified_verification_attempts.pop(f"attempts:{player_name}:{qq_number}", None)

            return True, "验证成功", pending
        else:
            # 错误次数扣减
            key = f"attempts:{player_name}:{qq_number}"
            attempts = self.unified_verification_attempts.get(key, 0) + 1
            self.unified_verification_attempts[key] = attempts

            remaining = 3 - attempts
            if remaining > 0:
                return False, f"验证码输入错误！您还有 {remaining} 次重试机会", {}

            # 次数用尽，彻底锁定并清除
            self.pending_verifications.pop(player_name, None)
            self.verification_codes.pop(qq_number, None)
            self.unified_verification_attempts.pop(key, None)

            # 触发撤回
            def on_fail_lock() -> None:
                if self.plugin.ws_client and self.plugin.ws_client.is_connected:
                    asyncio.run_coroutine_threadsafe(
                        self._delete_verification_message(qq_number),
                        self.plugin._loop,
                    )

            self.plugin.server.scheduler.run_task(self.plugin, on_fail_lock, delay=1)

            # 触发冷却锁
            self.player_verification_cooldown[player_name] = now
            self.binding_rate_limit[qq_number] = now

            return False, "验证码重试次数已达上限，系统已对您施加60秒申请风控锁定", {}

    def cleanup_expired_verifications(self) -> None:
        """清理已超时过期的挂起绑定验证码和已发送的消息撤回"""
        now = Timing.get_timestamp()
        expired_players = [p for p, d in self.pending_verifications.items() if now - d["timestamp"] > 60]
        expired_qqs = [qq for qq, d in self.verification_codes.items() if now - d["timestamp"] > 60]

        for p in expired_players:
            self.pending_verifications.pop(p, None)
            self.logger.debug(f"回收玩家过期验证码: {p}")

        for qq in expired_qqs:
            self.verification_codes.pop(qq, None)
            self.logger.debug(f"回收QQ过期验证码: {qq}")

        # 收集需要撤回的消息
        expired_msgs = [qq for qq, d in self.verification_messages.items() if now - d["timestamp"] > 60]
        for qq in expired_msgs:
            def create_retract(q: str) -> Any:
                return lambda: asyncio.run_coroutine_threadsafe(self._delete_verification_message(q), self.plugin._loop) if (self.plugin.ws_client and self.plugin.ws_client.is_connected) else None
            try:
                self.plugin.server.scheduler.run_task(self.plugin, create_retract(qq), delay=1)
            except Exception:
                pass

        # 清理其它缓存
        self._cleanup_expired_caches(now)

    def _cleanup_expired_caches(self, now: float) -> None:
        """清理内存中超时的零散缓存数据"""
        # 待确认昵称信息超过10分钟强退
        expired_conf = [k for k, v in self.pending_qq_confirmations.items() if now - v["timestamp"] > 600]
        for k in expired_conf:
            self.pending_qq_confirmations.pop(k, None)

        # 频控列表整理（超出 10 分钟）
        expired_cooldown = [k for k, t in self.player_verification_cooldown.items() if now - t > 600]
        for k in expired_cooldown:
            self.player_verification_cooldown.pop(k, None)

    def cleanup_player_data(self, player_name: str) -> None:
        """物理清除离线玩家全部的数据挂载缓存，避免内存泄露"""
        self.pending_qq_confirmations.pop(player_name, None)
        self.player_bind_attempts.pop(player_name, None)
        self.concurrent_bindings.discard(player_name)
        self.player_verification_cooldown.pop(player_name, None)

        # 清除待发队列中含有该玩家的包
        self.verification_send_queue = [x for x in self.verification_send_queue if x[1] != player_name]

        # 玩家离线时将验证码和撤回动作做离线闭环
        if player_name in self.pending_verifications:
            qq = self.pending_verifications[player_name]["qq"]
            self.pending_verifications.pop(player_name, None)
            self.verification_codes.pop(qq, None)
            # 撤回群消息
            def retract() -> None:
                if self.plugin.ws_client and self.plugin.ws_client.is_connected:
                    asyncio.run_coroutine_threadsafe(self._delete_verification_message(qq), self.plugin._loop)
            self.plugin.server.scheduler.run_task(self.plugin, retract, delay=1)

    async def _delete_verification_message(self, qq_number: str) -> None:
        """通过 OneBot 接口物理撤回验证码消息"""
        if qq_number not in self.verification_messages:
            return

        msg_info = self.verification_messages[qq_number]
        message_ids = msg_info.get("message_ids", [])

        if message_ids and self.plugin.ws_client and self.plugin.ws_client.is_connected:
            for mid in message_ids:
                try:
                    payload = {
                        "action": "delete_msg",
                        "params": {"message_id": mid},
                        "echo": f"retract_msg:{qq_number}:{mid}",
                    }
                    await self.plugin.ws_client.send_message(payload)
                except Exception as e:
                    self.logger.warning(f"撤回消息 {mid} 失败: {e}")

        self.verification_messages.pop(qq_number, None)

    async def _handle_verification_success(self, player_name: str, qq_number: str) -> None:
        """验证通过后，在 QQ 群内广播消息并修改群名片"""
        # 1. 撤回原验证码消息
        await self._delete_verification_message(qq_number)

        if not (self.plugin.ws_client and self.plugin.ws_client.is_connected):
            return

        # 2. 拼接绑定提示并进行 At 广播
        sync_desc = ""
        if self.plugin.config_manager.sync_group_card:
            sync_desc = f"\n群昵称已自动修改为: {player_name}"

        broadcast_text = f"\n[成功] 您的 QQ 绑定已激活成功！\n绑定角色: {player_name}\n绑定QQ: {qq_number}{sync_desc}"

        # 广播给 TOML 内所有群聊
        for group in self.plugin.config_manager.groups:
            try:
                payload = {
                    "action": "send_group_msg",
                    "params": {
                        "group_id": group["id"],
                        "message": [
                            {"type": "at", "data": {"qq": qq_number}},
                            {"type": "text", "data": {"text": broadcast_text}},
                        ],
                    },
                    "echo": f"bind_broadcast:{qq_number}:{group['id']}",
                }
                await self.plugin.ws_client.send_message(payload)
            except Exception as e:
                self.logger.error(f"发送绑定成功群广播失败 (群: {group['id']}): {e}")

    def handle_message_response(self, echo: str, message_id: int) -> None:
        """API 回执路由：捕获验证码消息的回包 message_id 并存储"""
        try:
            if echo.startswith("verification_msg:"):
                # echo 结构: verification_msg:qq_number:group_id
                parts = echo.split(":")
                if len(parts) >= 2:
                    qq = parts[1]
                    self.store_verification_message(qq, message_id)
        except Exception as e:
            self.logger.error(f"处理API响应回包失败: {e}")

    def handle_api_response(self, echo: str, status: str, data: dict[str, Any] | None = None) -> None:
        """处理其它 OneBot API 操作的响应（留空或记录日志）"""
        pass

    def store_verification_message(self, qq_number: str, message_id: int) -> None:
        """记录发送成功的验证码消息 ID，以备超时或激活后撤回"""
        if qq_number not in self.verification_messages:
            self.verification_messages[qq_number] = {
                "message_ids": [],
                "timestamp": Timing.get_timestamp(),
            }
        mids = self.verification_messages[qq_number]["message_ids"]
        if message_id not in mids:
            mids.append(message_id)

    def process_verification_send_queue(self) -> None:
        """防抖定时发送任务逻辑"""
        if not self.verification_send_queue:
            return

        now = Timing.get_timestamp()
        if now - self.last_verification_send_time < self.verification_send_interval:
            return

        player, name, qq, code, attempt, timestamp = self.verification_send_queue.pop(0)

        # 检查是否在线
        if not self.plugin.is_valid_player(player):
            self.unregister_verification_attempt(qq, name, False)
            return

        if self.plugin.ws_client and self.plugin.ws_client.is_connected:
            text = (
                f"\n验证码: {code}\n游戏ID: {name}\n"
                f"[提示] 请在游戏内UI或对话框输入完成核验，\n或在群内直接回复 `/verify {code}`\n验证码60秒内有效"
            )

            # 向所有群异步发送
            async def send_all() -> None:
                for group in self.plugin.config_manager.groups:
                    try:
                        payload = {
                            "action": "send_group_msg",
                            "params": {
                                "group_id": group["id"],
                                "message": [
                                    {"type": "at", "data": {"qq": qq}},
                                    {"type": "text", "data": {"text": text}},
                                ],
                            },
                            "echo": f"verification_msg:{qq}:{group['id']}",
                        }
                        await self.plugin.ws_client.send_message(payload)
                    except Exception as ex:
                        self.logger.error(f"向群 {group['id']} 发送验证码失败: {ex}")

            asyncio.run_coroutine_threadsafe(send_all(), self.plugin._loop)
            self.last_verification_send_time = now

            from endstone import ColorFormat

            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}验证码消息已推送到 QQ 群内，请注意查看@消息！{ColorFormat.RESET}")
        else:
            from endstone import ColorFormat

            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}服务器同 QQ 机器人断开连接，无法发送验证码！{ColorFormat.RESET}")
            self.unregister_verification_attempt(qq, name, False)
