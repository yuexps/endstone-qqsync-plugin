"""
UI界面模块
负责处理游戏内的表单界面
"""

import asyncio
import json
from endstone.form import ModalForm, MessageForm, Label, TextInput, Header, Divider
from endstone import ColorFormat
from ..utils.time_utils import TimeUtils
from ..utils.helpers import is_valid_qq_number


class UIManager:
    """UI管理器"""
    
    def __init__(self, plugin):
        self.plugin = plugin
        self.logger = plugin.logger
    
    def show_qq_binding_form(self, player):
        """显示QQ绑定表单"""
        if not self._is_valid_player(player):
            self.logger.warning("尝试对已失效的玩家对象显示绑定表单，操作已跳过")
            return
            
        try:
            # 根据是否启用强制绑定显示不同的内容
            if self.plugin.config_manager.get_config("force_bind_qq", True):
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
            
            form.on_submit = lambda p, form_data: self._handle_qq_form_submit(p, form_data) if self._is_valid_player(p) else None
            close_message = f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}您可以稍后通过命令 /bindqq 进行QQ绑定{ColorFormat.RESET}"
            if not self.plugin.config_manager.get_config("force_bind_qq", True):
                close_message = f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}QQ绑定已取消，您可以正常游戏{ColorFormat.RESET}"
            form.on_close = lambda p: p.send_message(close_message) if self._is_valid_player(p) else None
            
            player.send_form(form)
            
        except Exception as e:
            self.logger.error(f"显示QQ绑定表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}绑定表单加载失败，请使用命令 /bindqq{ColorFormat.RESET}")
    
    def show_qq_confirmation_form(self, player, qq_number: str, nickname: str):
        """显示QQ信息确认表单"""
        if not self._is_valid_player(player):
            self.logger.warning("尝试对已失效的玩家对象显示确认表单，操作已跳过")
            return
            
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
            def handle_submit(p, button_index):
                if not self._is_valid_player(p):
                    return
                if button_index == 0:  # 确认绑定
                    self._handle_qq_confirmation(p, True, qq_number, nickname)
                else:  # 重新输入
                    self._handle_qq_confirmation(p, False, qq_number, nickname)
            
            form.on_submit = handle_submit
            form.on_close = lambda p: self._handle_qq_confirmation(p, False, qq_number, nickname) if self._is_valid_player(p) else None
            
            player.send_form(form)
            
        except Exception as e:
            self.logger.error(f"显示QQ确认表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}确认表单加载失败，请重试！{ColorFormat.RESET}")
    
    def show_verification_form(self, player):
        """显示验证码输入表单"""
        if not self._is_valid_player(player):
            self.logger.warning("尝试对已失效的玩家对象显示验证表单，操作已跳过")
            return
            
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
            
            form.on_submit = lambda p, form_data: self._handle_verification_submit(p, form_data) if self._is_valid_player(p) else None
            form.on_close = lambda p: self._handle_verification_close(p) if self._is_valid_player(p) else None
            
            player.send_form(form)
            
        except Exception as e:
            self.logger.error(f"显示验证码表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证表单加载失败！{ColorFormat.RESET}")
    
    def _handle_qq_form_submit(self, player, form_data):
        """处理QQ绑定表单提交"""
        if not self._is_valid_player(player):
            self.logger.warning("尝试处理已失效玩家的表单提交，操作已跳过")
            return
            
        try:
            # 解析表单数据
            qq_input = self._extract_form_input(form_data)
            
            if not is_valid_qq_number(qq_input):
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}请输入有效的QQ号（5-11位数字）！{ColorFormat.RESET}")
                if self.plugin.config_manager.get_config("force_bind_qq", True):
                    self.plugin.server.scheduler.run_task(
                        self.plugin,
                        lambda p=player: self.show_qq_binding_form(p) if self._is_valid_player(p) else None,
                        delay=20
                    )
                return
            
            # 检查玩家是否被封禁
            if self.plugin.data_manager.is_player_banned(player.name):
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}[拒绝] 您已被封禁，无法绑定QQ！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}如有疑问请联系管理员{ColorFormat.RESET}")
                return
            
            # 检查QQ号是否已被其他玩家绑定
            existing_player = self.plugin.data_manager.get_qq_player(qq_input)
            if existing_player:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}该QQ号已被玩家 {existing_player} 绑定！{ColorFormat.RESET}")
                return
            
            # 检查QQ号是否在群内
            if (self.plugin.config_manager.get_config("force_bind_qq", True) and 
                self.plugin.config_manager.get_config("check_group_member", True)):
                if hasattr(self.plugin, 'group_members') and self.plugin.group_members:
                    if qq_input not in self.plugin.group_members:
                        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}该QQ号不在目标群内，无法绑定！{ColorFormat.RESET}")
                        # 获取所有目标群组
                        target_groups = self.plugin.config_manager.get_config("target_groups", [])
                        group_names = self.plugin.config_manager.get_config("group_names", {})
                        
                        # 构建群列表消息
                        if target_groups:
                            group_list = []
                            for group_id in target_groups:
                                group_name = group_names.get(str(group_id), "")
                                if group_name:
                                    group_list.append(f"{group_id} ({group_name})")
                                else:
                                    group_list.append(str(group_id))
                            
                            groups_text = "、".join(group_list)
                            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}请先加入以下任一QQ群：{groups_text}{ColorFormat.RESET}")
                        return
            
            # 开始获取QQ昵称和验证流程
            self._start_verification_process(player, qq_input)
            
        except Exception as e:
            self.logger.error(f"处理QQ绑定表单失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}绑定过程出错，请重试！{ColorFormat.RESET}")
    
    def _handle_qq_confirmation(self, player, confirmed: bool, qq_number: str = None, nickname: str = None):
        """处理QQ信息确认结果"""
        if not self._is_valid_player(player):
            self.logger.warning("尝试处理已失效玩家的确认结果，操作已跳过")
            return
        
        try:
            # 清理待确认信息
            if player.name in self.plugin.verification_manager.pending_qq_confirmations:
                if not qq_number:
                    qq_info = self.plugin.verification_manager.pending_qq_confirmations[player.name]
                    qq_number = qq_info["qq"]
                    nickname = qq_info.get("nickname", "未知昵称")
                del self.plugin.verification_manager.pending_qq_confirmations[player.name]
            
            if not confirmed:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}QQ绑定已取消{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您可以使用命令 /bindqq 重新开始绑定{ColorFormat.RESET}")
                return
            
            if not qq_number:
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}确认信息已过期，请重新开始绑定{ColorFormat.RESET}")
                return
            
            # 生成验证码
            if self.plugin.verification_manager.generate_verification_code(player, qq_number, nickname):
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}正在发送验证码到群组@您...{ColorFormat.RESET}")
                
                # 显示验证码输入表单
                self.plugin.server.scheduler.run_task(
                    self.plugin,
                    lambda p=player: self.show_verification_form(p) if self._is_valid_player(p) else None,
                    delay=20
                )
            
        except Exception as e:
            self.logger.error(f"处理QQ确认失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}确认过程出错，请重试！{ColorFormat.RESET}")
    
    def _handle_verification_submit(self, player, form_data):
        """处理验证码提交"""
        if not self._is_valid_player(player):
            self.logger.warning("尝试处理已失效玩家的验证码提交，操作已跳过")
            return
        
        try:
            # 解析验证码
            verification_input = self._extract_form_input(form_data)
            
            # 验证验证码
            success, message, pending_info = self.plugin.verification_manager.verify_code(
                player.name, player.xuid, verification_input, "game"
            )
            
            if success:
                # 绑定成功 - 数据绑定由 data_manager 处理
                self.plugin.data_manager.bind_player_qq(player.name, player.xuid, pending_info["qq"])
                
                # 注意：验证数据清理、验证码撤回和成功播报已在 verification_manager.verify_code() 中统一处理
                
                # 发送成功消息给玩家
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.GREEN}[成功] QQ绑定成功！{ColorFormat.RESET}")
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您的QQ {pending_info['qq']} 已与游戏账号绑定{ColorFormat.RESET}")
                
                # 恢复玩家权限
                if self.plugin.config_manager.get_config("force_bind_qq", True):
                    self.plugin.server.scheduler.run_task(
                        self.plugin,
                        lambda: self.plugin.permission_manager.restore_player_permissions(player),
                        delay=2
                    )
                
                # 设置QQ群昵称 - 在所有配置的群组中设置
                if (hasattr(self.plugin, '_current_ws') and self.plugin._current_ws and 
                    self.plugin.config_manager.get_config("force_bind_qq", True) and 
                    self.plugin.config_manager.get_config("sync_group_card", True)):
                    
                    from ..websocket.handlers import set_group_card_in_all_groups
                    asyncio.run_coroutine_threadsafe(
                        set_group_card_in_all_groups(self.plugin._current_ws, user_id=int(pending_info["qq"]), card=player.name),
                        self.plugin._loop
                    )
            else:
                # 验证失败
                player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}{message}{ColorFormat.RESET}")
                
                # 如果还有重试机会，重新显示表单
                if "还可以尝试" in message:
                    self.plugin.server.scheduler.run_task(
                        self.plugin,
                        lambda p=player: self.show_verification_form(p) if self._is_valid_player(p) else None,
                        delay=10
                    )
            
        except Exception as e:
            self.logger.error(f"处理验证码提交失败: {e}")
            player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}验证过程出错，请重试！{ColorFormat.RESET}")
    
    def _handle_verification_close(self, player):
        """处理验证表单关闭"""
        if not self._is_valid_player(player):
            self.logger.warning("尝试处理已失效玩家的验证表单关闭，操作已跳过")
            return
        
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}游戏内绑定已取消{ColorFormat.RESET}")
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.AQUA}您仍可在QQ群中输入验证码完成绑定{ColorFormat.RESET}")
    
    def _start_verification_process(self, player, qq_number: str):
        """开始验证流程"""
        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.YELLOW}正在获取QQ昵称信息...{ColorFormat.RESET}")
        
        # 临时存储待确认的QQ信息
        self.plugin.verification_manager.pending_qq_confirmations[player.name] = {
            "qq": qq_number,
            "nickname": "未知昵称",  # 默认昵称，WebSocket响应会更新这个值
            "timestamp": TimeUtils.get_timestamp()
        }
        
        # 异步获取QQ昵称
        if hasattr(self.plugin, '_current_ws') and self.plugin._current_ws:
            asyncio.run_coroutine_threadsafe(
                self._get_qq_nickname_and_confirm(self.plugin._current_ws, player, qq_number),
                self.plugin._loop
            )
        else:
            # 如果WebSocket未连接，直接显示确认表单
            self.show_qq_confirmation_form(player, qq_number, "未知昵称")
    
    async def _get_qq_nickname_and_confirm(self, ws, player, qq_number):
        """异步获取QQ昵称并显示确认表单"""
        try:
            # 发送获取用户信息请求
            payload = {
                "action": "get_stranger_info",
                "params": {
                    "user_id": int(qq_number),
                    "no_cache": True
                },
                "echo": f"get_stranger_info_{qq_number}_{int(TimeUtils.get_timestamp())}"
            }
            
            # 尝试获取QQ昵称，失败则使用默认昵称
            nickname = "未知昵称"
            try:
                self.logger.info(f"发送获取QQ昵称请求: {qq_number}")
                await ws.send(json.dumps(payload))
                
                # 等待更长时间让响应到达，并多次检查
                for attempt in range(10):  # 最多等待3秒（每次0.3秒）
                    await asyncio.sleep(0.3)
                    
                    # 检查是否通过WebSocket响应更新了昵称
                    if player.name in self.plugin.verification_manager.pending_qq_confirmations:
                        updated_info = self.plugin.verification_manager.pending_qq_confirmations[player.name]
                        updated_nickname = updated_info.get("nickname", "未知昵称")
                        
                        # 如果昵称已更新且不是默认值，则使用更新后的昵称
                        if updated_nickname != "未知昵称":
                            nickname = updated_nickname
                            self.logger.info(f"✅ 获取到QQ {qq_number} 昵称: {nickname}")
                            break
                    else:
                        self.logger.warning(f"玩家 {player.name} 的待确认信息已丢失")
                        break
                
                # 如果仍然是默认昵称，记录警告
                if nickname == "未知昵称":
                    self.logger.warning(f"获取QQ {qq_number} 昵称超时，使用默认昵称")
                        
            except Exception as e:
                self.logger.warning(f"获取QQ昵称失败: {e}")
            
            # 在主线程中显示确认表单
            def show_form():
                try:
                    if self._is_valid_player(player):
                        self.show_qq_confirmation_form(player, qq_number, nickname)
                except Exception as e:
                    self.logger.error(f"显示QQ确认表单失败: {e}")
                    if self._is_valid_player(player):
                        player.send_message(f"{ColorFormat.GRAY}[QQsync] {ColorFormat.RED}显示确认表单失败，请重试！{ColorFormat.RESET}")
            
            # 使用调度器在主线程执行
            self.plugin.server.scheduler.run_task(self.plugin, show_form, delay=1)
            
        except Exception as e:
            self.logger.error(f"获取QQ昵称过程出错: {e}")
            # 显示默认确认表单
            def show_form():
                if self._is_valid_player(player):
                    self.show_qq_confirmation_form(player, qq_number, "未知昵称")
            self.plugin.server.scheduler.run_task(self.plugin, show_form, delay=1)
    
    def _extract_form_input(self, form_data: str) -> str:
        """从表单数据中提取输入内容"""
        try:
            # 尝试解析JSON格式
            data_list = json.loads(form_data)
            # TextInput总是在表单控件的最后位置，从末尾获取
            return data_list[-1] if len(data_list) > 0 else ""
        except (json.JSONDecodeError, IndexError):
            # 如果不是JSON，按逗号分割
            data_parts = form_data.split(',') if form_data else []
            return data_parts[-1].strip() if len(data_parts) > 0 else ""
    
    def _is_valid_player(self, player) -> bool:
        """检查玩家对象是否有效且在线 - 委托给插件实例"""
        return self.plugin.is_valid_player(player)
