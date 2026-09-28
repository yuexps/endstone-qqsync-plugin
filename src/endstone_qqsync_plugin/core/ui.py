"""
游戏内 UI 绑定表单模块
"""

import asyncio
import json
from typing import Any
from endstone.form import ModalForm, MessageForm, Label, TextInput, Divider
from endstone import ColorFormat
from ..utils.helpers import is_valid_qq_number
from ..utils.timing import Timing

# 昵称缓存轮询间隔（秒）
_NICKNAME_POLL_INTERVAL_SECONDS = 0.3


class UI:
    """构建并分发绑定流程表单"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.logger = plugin.logger

    def show_qq_binding_form(self, player: Any) -> None:
        """弹出第一步：QQ号码输入表单"""
        if not self.plugin.is_valid_player(player):
            return

        try:
            form = ModalForm(
                title="QQsync群服互通 - 身份验证",
                controls=self._build_binding_controls(),
                submit_button="下一步",
                icon="textures/ui/icon_multiplayer",
            )

            # 绑定提交和关闭回调
            form.on_submit = lambda p, data: self._handle_qq_form_submit(p, data) if self.plugin.is_valid_player(p) else None

            close_msg = self._build_binding_close_message()
            form.on_close = lambda p: p.send_message(close_msg) if self.plugin.is_valid_player(p) else None

            player.send_form(form)
        except Exception as e:
            self.logger.error(f"下发QQ绑定表单失败: {e}")
            player.send_message(
                f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}表单加载失败，请在对话框中使用指令 /bindqq 重新发起{ColorFormat.RESET}"
            )

    def _build_binding_controls(self) -> list[Any]:
        """按配置组装QQ绑定表单的引导语与输入框"""
        # 根据配置动态组装引导语
        if self.plugin.config_manager.force_bind_qq:
            controls: list[Any] = [
                Divider(),
                Label("为了获得完整的游戏权限，请在此绑定您的QQ号"),
                Label("绑定后您的聊天消息将与QQ群同步"),
                Label("享受群服一体化交互体验"),
                Divider(),
            ]
        else:
            controls = [
                Divider(),
                Label("您可以自由选择绑定QQ号，获得群服消息互通"),
                Label("不绑定不影响您的正常游戏行为"),
                Divider(),
            ]

        controls.append(
            TextInput(
                label="请输入您的QQ号",
                placeholder="例如: 2899659758 (5-11位纯数字)",
                default_value="",
            )
        )
        return controls

    def _build_binding_close_message(self) -> str:
        """按配置生成绑定表单关闭时的提示"""
        if not self.plugin.config_manager.force_bind_qq:
            return f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}QQ绑定已取消，已恢复您的正常游戏权限{ColorFormat.RESET}"
        return f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}您可以稍后在游戏内输入指令 /bindqq 重新开启绑定流程{ColorFormat.RESET}"

    def show_qq_confirmation_form(self, player: Any, qq_number: str, nickname: str) -> None:
        """弹出第二步：确认解析出的 QQ 用户昵称信息表单"""
        if not self.plugin.is_valid_player(player):
            return

        try:
            content = f"请核对您的QQ账号信息：\n\nQQ号: {qq_number}\n昵称: {nickname}"

            form = MessageForm(
                title="核对QQ信息",
                content=content,
                button1="确认绑定",
                button2="返回修改",
            )

            def handle_submit(p: Any, btn_index: int) -> None:
                if not self.plugin.is_valid_player(p):
                    return
                # MessageForm 交互: 0 = button1, 1 = button2
                if btn_index == 0:
                    self._handle_qq_confirmation(p, True, qq_number, nickname)
                else:
                    self._handle_qq_confirmation(p, False, qq_number, nickname)

            form.on_submit = handle_submit
            form.on_close = lambda p: self._handle_qq_confirmation(p, False, qq_number, nickname) if self.plugin.is_valid_player(p) else None

            player.send_form(form)
        except Exception as e:
            self.logger.error(f"下发QQ确认表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}表单初始化失败，请重新尝试绑定{ColorFormat.RESET}")

    def show_verification_form(self, player: Any) -> None:
        """弹出第三步：验证码输入校验表单"""
        if not self.plugin.is_valid_player(player):
            return

        try:
            form = ModalForm(
                title="QQ验证码激活",
                controls=[
                    Divider(),
                    Label("系统已通过QQ群@您发送了验证码！"),
                    Label("请输入您在QQ群内收到的6位数字验证码"),
                    Label("验证码60秒内有效"),
                    Divider(),
                    TextInput(
                        label="请输入验证码",
                        placeholder="6位数字验证码",
                        default_value="",
                    ),
                ],
                submit_button="激活绑定",
                icon="textures/ui/icon_book_writable",
            )

            form.on_submit = lambda p, data: self._handle_verification_submit(p, data) if self.plugin.is_valid_player(p) else None
            form.on_close = lambda p: self._handle_verification_close(p) if self.plugin.is_valid_player(p) else None

            player.send_form(form)
        except Exception as e:
            self.logger.error(f"下发验证码输入表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}表单激活失败，请重试！{ColorFormat.RESET}")

    def _handle_qq_form_submit(self, player: Any, form_data: str) -> None:
        """处理第一步QQ号码输入的表单递交"""
        if not self.plugin.is_valid_player(player):
            return

        try:
            qq_input = self._extract_form_input(form_data)

            if not is_valid_qq_number(qq_input):
                self._notify_invalid_qq_input(player)
                return

            if not self._passes_binding_checks(player, qq_input):
                return

            self._start_verification_process(player, qq_input)
        except Exception as e:
            self.logger.error(f"处理绑定表单提交逻辑失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}处理流程出错，请稍后使用 /bindqq 重新重试{ColorFormat.RESET}")

    def _notify_invalid_qq_input(self, player: Any) -> None:
        """提示QQ号格式错误，强制绑定时重新下发绑定表单"""
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}请输入格式正确的QQ号（5-11位纯数字）！{ColorFormat.RESET}")
        if self.plugin.config_manager.force_bind_qq:
            self.plugin.server.scheduler.run_task(
                self.plugin,
                lambda p=player: self.show_qq_binding_form(p) if self.plugin.is_valid_player(p) else None,
                delay=20,
            )

    def _passes_binding_checks(self, player: Any, qq_input: str) -> bool:
        """核验黑名单、QQ号占用与入群资格"""
        # 黑名单校验
        if self.plugin.data_manager.is_player_banned(player.name, player.xuid):
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}[拒绝] 该游戏角色处于封禁期，无法绑定QQ{ColorFormat.RESET}")
            return False

        # QQ号排他性检查
        existing = self.plugin.data_manager.get_qq_player(qq_input)
        if existing:
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}该QQ号已被游戏角色 {existing} 占用绑定！{ColorFormat.RESET}")
            return False

        return self._verify_group_membership(player, qq_input)

    def _verify_group_membership(self, player: Any, qq_input: str) -> bool:
        """核验QQ号是否在配置的群聊内"""
        # 入群资格强制核验
        if not (self.plugin.config_manager.force_bind_qq and self.plugin.config_manager.check_group_member):
            return True

        if not (hasattr(self.plugin, "group_members") and self.plugin.group_members):
            return True

        if self._is_qq_in_any_group(qq_input):
            return True

        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}检测到该QQ号不在配置的群聊内，绑定终止{ColorFormat.RESET}")
        # 输出允许加入的群列表
        group_texts = [f"{g['id']} ({g['name']})" for g in self.plugin.config_manager.groups]
        player.send_message(
            f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}请先用此QQ加入群: {', '.join(group_texts)}{ColorFormat.RESET}"
        )
        return False

    def _is_qq_in_any_group(self, qq_input: str) -> bool:
        """检查QQ号是否存在于任一已缓存的群成员列表"""
        for group in self.plugin.config_manager.groups:
            group_id = group["id"]
            if group_id in self.plugin.group_members and qq_input in self.plugin.group_members[group_id]:
                return True
        return False

    def _handle_qq_confirmation(self, player: Any, confirmed: bool, qq_number: str, nickname: str) -> None:
        """处理第二步确认界面的按钮反馈"""
        if not self.plugin.is_valid_player(player):
            return

        try:
            # 清理确认中的临时数据
            if player.name in self.plugin.verification_manager.pending_qq_confirmations:
                del self.plugin.verification_manager.pending_qq_confirmations[player.name]

            if not confirmed:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}QQ绑定流程已取消{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您随时可以使用命令 /bindqq 重新开始绑定{ColorFormat.RESET}")
                return

            # 申请并发送验证码
            if self.plugin.verification_manager.generate_verification_code(player, qq_number, nickname):
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}验证码已发送至QQ群，请注意查收@消息{ColorFormat.RESET}")
                # 延迟20tick弹出输入验证码表单
                self.plugin.server.scheduler.run_task(
                    self.plugin,
                    lambda p=player: self.show_verification_form(p) if self.plugin.is_valid_player(p) else None,
                    delay=20,
                )
        except Exception as e:
            self.logger.error(f"确认QQ绑定时发生错误: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}操作遇到内部错误，绑定已终止{ColorFormat.RESET}")

    def _handle_verification_submit(self, player: Any, form_data: str) -> None:
        """处理第三步验证码激活表单的校验逻辑"""
        if not self.plugin.is_valid_player(player):
            return

        try:
            code = self._extract_form_input(form_data)

            # 交由 verification 模块核验
            success, message, pending_info = self.plugin.verification_manager.verify_code(
                player.name, player.xuid, code, "game"
            )

            if success:
                self._apply_successful_binding(player, pending_info)
            else:
                self._notify_verification_failure(player, message)
        except Exception as e:
            self.logger.error(f"处理验证码提交发生错误: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证遇到内部错误，绑定已终止{ColorFormat.RESET}")

    def _apply_successful_binding(self, player: Any, pending_info: dict[str, Any]) -> None:
        """绑定完成后写库、恢复权限并同步群名片"""
        # 绑定完成，更新数据库
        self.plugin.data_manager.bind_player_qq(player.name, player.xuid, pending_info["qq"])
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] QQ绑定验证通过！{ColorFormat.RESET}")
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您的角色已同 QQ {pending_info['qq']} 绑定关联{ColorFormat.RESET}")

        # 恢复玩家权限
        if self.plugin.config_manager.force_bind_qq:
            self.plugin.server.scheduler.run_task(
                self.plugin,
                lambda: self.plugin.permission_manager.restore_player_permissions(player),
                delay=4,
            )

        # 调用 OneBot API 设置群名片
        if self.plugin.ws_client and self.plugin.ws_client.is_connected and self.plugin.config_manager.sync_group_card:
            self._sync_bound_group_card(pending_info["qq"], player.name)

    def _sync_bound_group_card(self, qq_number: str, player_name: str) -> None:
        """异步同步绑定玩家的群名片"""
        from ..qq.commands import sync_group_card_for_all
        asyncio.run_coroutine_threadsafe(
            sync_group_card_for_all(self.plugin.ws_client, int(qq_number), player_name),
            self.plugin._loop,
        )

    def _notify_verification_failure(self, player: Any, message: str) -> None:
        """提示验证失败，尚有余次时延时重新拉起验证码表单"""
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}{message}{ColorFormat.RESET}")
        # 包含“可以尝试”说明还有剩余机会，延时重新拉起表单
        if "还可以尝试" in message:
            self.plugin.server.scheduler.run_task(
                self.plugin,
                lambda p=player: self.show_verification_form(p) if self.plugin.is_valid_player(p) else None,
                delay=20,
            )

    def _handle_verification_close(self, player: Any) -> None:
        """处理验证码输入框被玩家手动关闭的行为"""
        if not self.plugin.is_valid_player(player):
            return
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}游戏内绑定操作已被取消{ColorFormat.RESET}")
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您仍然可以通过在群内发送 `/verify 验证码` 来激活绑定{ColorFormat.RESET}")

    def _start_verification_process(self, player: Any, qq_number: str) -> None:
        """触发获取群昵称并弹出确认页的异步协程"""
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}正在向 OneBot 请求您的群内昵称数据...{ColorFormat.RESET}")

        # 临时写入待核对缓存
        self.plugin.verification_manager.pending_qq_confirmations[player.name] = {
            "qq": qq_number,
            "nickname": "未知昵称",
            "timestamp": Timing.get_timestamp(),
        }

        # 异步线程获取用户信息并反馈主线程展示
        if self.plugin.ws_client and self.plugin.ws_client.is_connected:
            asyncio.run_coroutine_threadsafe(
                self._fetch_nickname_and_confirm(player, qq_number),
                self.plugin._loop,
            )
        else:
            # 如果 WebSocket 未连接，则直接使用未知昵称下发确认表单
            self.show_qq_confirmation_form(player, qq_number, "未知昵称")

    async def _fetch_nickname_and_confirm(self, player: Any, qq_number: str) -> None:
        """向 OneBot 请求接口获取昵称，并调度主线程拉起表单"""
        nickname = "未知昵称"
        try:
            await self._request_stranger_info(qq_number)
            nickname = await self._poll_cached_nickname(player)
        except Exception as e:
            self.logger.warning(f"获取QQ昵称网络请求发生错误: {e}")

        # 回归主线程弹出确认 UI
        def run_main() -> None:
            if self.plugin.is_valid_player(player):
                self.show_qq_confirmation_form(player, qq_number, nickname)

        self.plugin.server.scheduler.run_task(self.plugin, run_main, delay=1)

    async def _request_stranger_info(self, qq_number: str) -> None:
        """向 OneBot 请求指定QQ的陌生人信息"""
        payload = {
            "action": "get_stranger_info",
            "params": {"user_id": int(qq_number), "no_cache": True},
            "echo": f"fetch_nickname:{qq_number}:{int(Timing.get_timestamp())}",
        }
        await self.plugin.ws_client.send_message(payload)

    async def _poll_cached_nickname(self, player: Any) -> str:
        """轮询3秒等待 handlers 接收并更新缓存中的昵称"""
        for _ in range(10):
            await asyncio.sleep(_NICKNAME_POLL_INTERVAL_SECONDS)
            nickname = self._read_cached_nickname(player.name)
            if nickname:
                return nickname
        return "未知昵称"

    def _read_cached_nickname(self, player_name: str) -> str:
        """读取待确认缓存中已更新的昵称，未更新时返回空串"""
        cached = self.plugin.verification_manager.pending_qq_confirmations.get(player_name)
        if not cached:
            return ""
        if cached.get("nickname") != "未知昵称":
            return cached["nickname"]
        return ""

    def _extract_form_input(self, form_data: str) -> str:
        """解析表单文本输入框内容"""
        try:
            data = json.loads(form_data)
            return str(data[-1]).strip() if data else ""
        except Exception:
            parts = form_data.split(",") if form_data else []
            return parts[-1].strip() if parts else ""
