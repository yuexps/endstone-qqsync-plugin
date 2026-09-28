"""
配置管理模块
"""

from pathlib import Path
import tomllib
from typing import Any

# 默认群组 QQ 号
_DEFAULT_GROUP_ID = 712523104


DEFAULT_TOML_TEMPLATE = """# QQsync 群服互通插件配置文件

napcat_ws = "ws://127.0.0.1:3001"  # NapCat WebSocket 服务器地址（正向WS）
access_token = ""  # 访问令牌（可选，若无则保持空）
admins = ["2899659758"]  # 管理员 QQ 号列表
enable_qq_to_game = true  # QQ 消息转发到游戏
enable_game_to_qq = true  # 游戏消息转发到 QQ
force_bind_qq = true  # 强制 QQ 绑定（启用身份验证系统）
sync_group_card = true  # 自动同步群昵称为玩家名
check_group_member = true  # 启用退群检测功能

# 聊天刷屏检测配置
chat_count_limit = 20  # 1分钟内最多发送消息数（-1则不限制）
chat_ban_time = 300  # 刷屏后禁言时间（秒）
api_qq_enable = false  # QQ 消息 API（默认关闭）

# 群发消息提示词自定义模板
msg_first_join = "[首次加入] 欢迎新玩家 {player} 首次进入服务器！"  # 首次加入提示词（支持 {player}）
msg_join = "[+] {player} 上线了 (第 {sessions} 次登录)"  # 玩家上线提示词（支持 {player}、{sessions}）
msg_quit = "[-] {player} 下线了 ({time})"  # 玩家下线提示词（支持 {player}、{time}、{total_time}）

# 群组配置表数组（支持配置多群，各群拥有独立的事件和指令开关）
[[groups]]
id = 712523104  # 目标 QQ 群号
name = "主群"  # 群组名称映射（用于区分消息来源）
enable_chat = true  # 是否开启该群聊天同步
enable_command = true  # 是否开启该群指令响应
"""


class Config:
    """插件 TOML 配置对象"""

    def __init__(self, data_folder: Path, logger: Any) -> None:
        self.data_folder = data_folder
        self.logger = logger
        self.config_file = data_folder / "config.toml"
        self.ban_words_file = data_folder / "custom_ban_words.txt"

        # 内存配置属性初始化
        self.napcat_ws: str = "ws://127.0.0.1:3001"
        self.access_token: str = ""
        self.admins: list[str] = []
        self.enable_qq_to_game: bool = True
        self.enable_game_to_qq: bool = True
        self.force_bind_qq: bool = True
        self.sync_group_card: bool = True
        self.check_group_member: bool = True
        self.chat_count_limit: int = 20
        self.chat_ban_time: int = 300
        self.api_qq_enable: bool = False
        self.msg_first_join: str = "[首次加入] 欢迎新玩家 {player} 首次进入服务器！"
        self.msg_join: str = "[+] {player} 上线了 (第 {sessions} 次登录)"
        self.msg_quit: str = "[-] {player} 下线了 ({time})"
        self.groups: list[dict[str, Any]] = []

        self.custom_ban_words: list[str] = []

        self._load_or_create_config()
        self._load_or_create_ban_words()

    def _load_or_create_config(self) -> None:
        """加载或创建配置文件"""
        if not self.config_file.exists():
            self._create_default_config()

        self._read_config_file()


    def _create_default_config(self) -> None:
        """创建默认 TOML 配置文件"""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                f.write(DEFAULT_TOML_TEMPLATE)
            self.logger.info(f"已创建默认 TOML 配置文件: {self.config_file}")
        except Exception as e:
            self.logger.error(f"创建默认配置文件失败: {e}")

    def _read_config_file(self) -> None:
        """读取 TOML 配置文件并应用到内存属性"""
        try:
            with open(self.config_file, "rb") as f:
                data = tomllib.load(f)
            self._apply_config_data(data)
        except Exception as e:
            self.logger.error(f"解析 TOML 配置文件失败: {e}，将使用内存默认值")





    def _apply_common_config(self, data: dict[str, Any]) -> None:
        """映射通用配置项到内存属性"""
        self.napcat_ws = data.get("napcat_ws", self.napcat_ws)
        self.access_token = data.get("access_token", self.access_token)
        self.admins = [str(x) for x in data.get("admins", self.admins)]
        self.enable_qq_to_game = bool(data.get("enable_qq_to_game", self.enable_qq_to_game))
        self.enable_game_to_qq = bool(data.get("enable_game_to_qq", self.enable_game_to_qq))
        self.force_bind_qq = bool(data.get("force_bind_qq", self.force_bind_qq))
        self.sync_group_card = bool(data.get("sync_group_card", self.sync_group_card))
        self.check_group_member = bool(data.get("check_group_member", self.check_group_member))
        self.chat_count_limit = int(data.get("chat_count_limit", self.chat_count_limit))
        self.chat_ban_time = int(data.get("chat_ban_time", self.chat_ban_time))
        self.api_qq_enable = bool(data.get("api_qq_enable", self.api_qq_enable))

    def _apply_config_data(self, data: dict[str, Any]) -> None:
        """映射解析出的字典到内存属性"""
        self._apply_common_config(data)
        self.msg_first_join = str(data.get("msg_first_join", self.msg_first_join))
        self.msg_join = str(data.get("msg_join", self.msg_join))
        self.msg_quit = str(data.get("msg_quit", self.msg_quit))

        # 处理群组表数组
        self._apply_groups(data)

    def _apply_groups(self, data: dict[str, Any]) -> None:
        """解析群组表数组配置"""
        raw_groups = data.get("groups", [])
        parsed_groups = [self._build_group(g) for g in raw_groups] if isinstance(raw_groups, list) else []
        self.groups = [g for g in parsed_groups if g]

        # 未配置群组时回退到默认群组
        if not self.groups:
            self.groups.append(self._build_default_group())

    def _build_group(self, g: Any) -> dict[str, Any] | None:
        """构造单个 TOML 群组配置"""
        if isinstance(g, dict) and "id" in g:
            return {
                "id": int(g["id"]),
                "name": str(g.get("name", f"群 {g['id']}")),
                "enable_chat": bool(g.get("enable_chat", True)),
                "enable_command": bool(g.get("enable_command", True)),
            }
        return None

    @staticmethod
    def _build_default_group() -> dict[str, Any]:
        """构造默认群组配置"""
        return {
            "id": _DEFAULT_GROUP_ID,
            "name": "默认群组",
            "enable_chat": True,
            "enable_command": True,
        }

    def _load_or_create_ban_words(self) -> None:
        """加载自定义屏蔽词文件"""
        if not self.ban_words_file.exists():
            self._create_default_ban_words_file()

        self._read_ban_words_file()

    def _create_default_ban_words_file(self) -> None:
        """创建默认自定义屏蔽词文件"""
        self.ban_words_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.ban_words_file, "w", encoding="utf-8") as f:
                f.write("这是一个自定义屏蔽词\n这是另一个自定义屏蔽词\n")
        except Exception as e:
            self.logger.error(f"创建默认自定义屏蔽词文件失败: {e}")

    def _read_ban_words_file(self) -> None:
        """读取自定义屏蔽词文件内容"""
        try:
            with open(self.ban_words_file, "r", encoding="utf-8") as f:
                self.custom_ban_words = [line.strip() for line in f if line.strip()]
            self.logger.info(f"已加载 {len(self.custom_ban_words)} 个自定义屏蔽词")
        except Exception as e:
            self.logger.error(f"读取自定义屏蔽词文件失败: {e}")
            self.custom_ban_words = []

    def save_config(self) -> None:
        """将当前内存配置项序列化为 TOML 文本存盘"""
        lines = [
            "# QQsync 群服互通插件配置文件\n",
            f'napcat_ws = "{self.napcat_ws}"  # NapCat WebSocket 服务器地址（正向WS）',
            f'access_token = "{self.access_token}"  # 访问令牌（可选，若无则保持空）',
            f"admins = {repr(self.admins)}  # 管理员 QQ 号列表",
            f"enable_qq_to_game = {str(self.enable_qq_to_game).lower()}  # QQ 消息转发到游戏",
            f"enable_game_to_qq = {str(self.enable_game_to_qq).lower()}  # 游戏消息转发到 QQ",
            f"force_bind_qq = {str(self.force_bind_qq).lower()}  # 强制 QQ 绑定（启用身份验证系统）",
            f"sync_group_card = {str(self.sync_group_card).lower()}  # 自动同步群昵称为玩家名",
            f"check_group_member = {str(self.check_group_member).lower()}  # 启用退群检测功能\n",
            "# 聊天刷屏检测配置",
            f"chat_count_limit = {self.chat_count_limit}  # 1分钟内最多发送消息数（-1则不限制）",
            f"chat_ban_time = {self.chat_ban_time}  # 刷屏后禁言时间（秒）",
            f"api_qq_enable = {str(self.api_qq_enable).lower()}  # QQ 消息 API（默认关闭）\n",
            "# 群发消息提示词自定义模板",
            f'msg_first_join = "{self.msg_first_join}"  # 首次加入提示词（支持 {{player}}）',
            f'msg_join = "{self.msg_join}"  # 玩家上线提示词（支持 {{player}}、{{sessions}}）',
            f'msg_quit = "{self.msg_quit}"  # 玩家下线提示词（支持 {{player}}、{{time}}、{{total_time}}）\n',
            "# 群组配置表数组（支持配置多群，各群拥有独立的事件和指令开关）",
        ]

        for g in self.groups:
            lines.extend(
                [
                    "[[groups]]",
                    f"id = {g['id']}  # 目标 QQ 群号",
                    f'name = "{g["name"]}"  # 群组名称映射（用于区分消息来源）',
                    f"enable_chat = {str(g['enable_chat']).lower()}  # 是否开启该群聊天同步",
                    f"enable_command = {str(g['enable_command']).lower()}  # 是否开启该群指令响应\n",
                ]
            )

        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.logger.info("配置已保存")
        except Exception as e:
            self.logger.error(f"写入 TOML 配置文件失败: {e}")

    def reload_config(self) -> bool:
        """重载 TOML 配置文件"""
        try:
            self._load_or_create_config()
            self._load_or_create_ban_words()
            return True
        except Exception as e:
            self.logger.error(f"重载 TOML 配置文件失败: {e}")
            return False

    def get_help_text(self, is_admin: bool = False) -> str:
        """获取玩家或管理员的群内指令帮助清单"""
        basic_commands = [
            "/help — 显示本帮助信息",
            "/list — 查看游戏在线玩家",
            "/tps — 查看服务器性能指标",
            "/info — 查看系统与硬件参数",
        ]
        bind_commands = [
            "/bind — 查看QQ绑定状态",
            "/verify <验证码> — 验证QQ绑定",
        ]
        admin_commands = [
            "/cmd <命令> — 执行后台控制台命令",
            "/bindqq <游戏名> <QQ> — 强制绑定玩家",
            "/check <玩家名|QQ> — 查询玩家档案",
            "/unbindqq <玩家名|QQ> — 强行解除玩家绑定",
            "/ban <玩家名> [原因] — 封禁玩家",
            "/unban <玩家名> — 解封玩家",
            "/banlist — 查看封禁黑名单",
            "/tog_qq — 切换QQ同步开关",
            "/tog_game — 切换游戏同步开关",
            "/reload — 重新载入TOML配置",
        ]

        lines = ["【QQsync 群服互通命令说明】\n[查询指令]（所有成员）："]
        lines.extend(f" • {cmd}" for cmd in basic_commands)

        if self.force_bind_qq:
            lines.append("\n[绑定指令]：")
            lines.extend(f" • {cmd}" for cmd in bind_commands)

        if is_admin:
            lines.append("\n[管理指令]（仅管理员）：")
            lines.extend(f" • {cmd}" for cmd in admin_commands)

        return "\n".join(lines)
