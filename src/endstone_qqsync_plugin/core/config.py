"""
配置管理模块
"""

from pathlib import Path
import tomllib
from typing import Any


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
        self.groups: list[dict[str, Any]] = []

        self.custom_ban_words: list[str] = []

        self._load_or_create_config()
        self._load_or_create_ban_words()

    def _load_or_create_config(self) -> None:
        """加载或自动创建配置文件"""
        old_config_file = self.data_folder / "config.json"

        if old_config_file.exists():
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            if self.config_file.exists():
                backup_toml = self.config_file.with_suffix(".toml.bak")
                try:
                    if backup_toml.exists():
                        backup_toml.unlink()
                    self.config_file.rename(backup_toml)
                    self.logger.info(f"检测到旧版配置且 config.toml 已存在，已将现配置备份为: {backup_toml.name}")
                except Exception as e:
                    self.logger.error(f"备份 config.toml 失败: {e}")
            self._migrate_old_config(old_config_file)
        else:
            if not self.config_file.exists():
                self.config_file.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with open(self.config_file, "w", encoding="utf-8") as f:
                        f.write(DEFAULT_TOML_TEMPLATE)
                    self.logger.info(f"已创建默认 TOML 配置文件: {self.config_file}")
                except Exception as e:
                    self.logger.error(f"创建默认配置文件失败: {e}")

        try:
            with open(self.config_file, "rb") as f:
                data = tomllib.load(f)
            self._apply_config_data(data)
        except Exception as e:
            self.logger.error(f"解析 TOML 配置文件失败: {e}，将使用内存默认值")

    def _migrate_old_config(self, old_file: Path) -> None:
        """从旧的 config.json 迁移配置到 config.toml"""
        import json
        self.logger.info("检测到旧版 JSON 配置，开始自动迁移至 TOML...")
        try:
            with open(old_file, "r", encoding="utf-8") as f:
                old_data = json.load(f)

            # 映射旧配置到内存属性
            self.napcat_ws = old_data.get("napcat_ws", self.napcat_ws)
            self.access_token = old_data.get("access_token", self.access_token)
            self.admins = [str(x) for x in old_data.get("admins", self.admins)]
            self.enable_qq_to_game = bool(old_data.get("enable_qq_to_game", self.enable_qq_to_game))
            self.enable_game_to_qq = bool(old_data.get("enable_game_to_qq", self.enable_game_to_qq))
            self.force_bind_qq = bool(old_data.get("force_bind_qq", self.force_bind_qq))
            self.sync_group_card = bool(old_data.get("sync_group_card", self.sync_group_card))
            self.check_group_member = bool(old_data.get("check_group_member", self.check_group_member))
            self.chat_count_limit = int(old_data.get("chat_count_limit", self.chat_count_limit))
            self.chat_ban_time = int(old_data.get("chat_ban_time", self.chat_ban_time))
            self.api_qq_enable = bool(old_data.get("api_qq_enable", self.api_qq_enable))

            # 解析群组配置
            target_groups = old_data.get("target_groups", [])
            group_names = old_data.get("group_names", {})
            self.groups = []

            if "target_group" in old_data and not target_groups:
                target_groups = [old_data["target_group"]]

            for g_id in target_groups:
                try:
                    group_id = int(g_id)
                    group_name = str(group_names.get(str(g_id), f"群 {g_id}"))
                    self.groups.append({
                        "id": group_id,
                        "name": group_name,
                        "enable_chat": True,
                        "enable_command": True
                    })
                except (ValueError, TypeError):
                    continue

            if not self.groups:
                self.groups.append({
                    "id": 712523104,
                    "name": "默认群组",
                    "enable_chat": True,
                    "enable_command": True
                })

            # 保存为 config.toml 并备份旧文件
            self.save_config()

            backup_file = old_file.with_suffix(".json.bak")
            if backup_file.exists():
                backup_file.unlink()
            old_file.rename(backup_file)
            self.logger.info(f"配置迁移成功，旧文件已备份为: {backup_file.name}")
        except Exception as e:
            self.logger.error(f"配置迁移失败: {e}")

    def _apply_config_data(self, data: dict[str, Any]) -> None:
        """映射解析出的字典到内存属性"""
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

        # 处理群组表数组
        self.groups = []
        raw_groups = data.get("groups", [])
        if isinstance(raw_groups, list):
            for g in raw_groups:
                if isinstance(g, dict) and "id" in g:
                    self.groups.append(
                        {
                            "id": int(g["id"]),
                            "name": str(g.get("name", f"群 {g['id']}")),
                            "enable_chat": bool(g.get("enable_chat", True)),
                            "enable_command": bool(g.get("enable_command", True)),
                        }
                    )

        # 兼容老版过渡逻辑：若无群组则默认添加一个空缺省值
        if not self.groups:
            self.groups.append(
                {
                    "id": 712523104,
                    "name": "默认群组",
                    "enable_chat": True,
                    "enable_command": True,
                }
            )

    def _load_or_create_ban_words(self) -> None:
        """加载自定义屏蔽词文件"""
        if not self.ban_words_file.exists():
            self.ban_words_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(self.ban_words_file, "w", encoding="utf-8") as f:
                    f.write("这是一个自定义违禁词\n这是另一个自定义违禁词\n")
            except Exception as e:
                self.logger.error(f"创建默认自定义屏蔽词文件失败: {e}")

        try:
            with open(self.ban_words_file, "r", encoding="utf-8") as f:
                self.custom_ban_words = [line.strip() for line in f if line.strip()]
            self.logger.info(f"已成功加载 {len(self.custom_ban_words)} 个自定义屏蔽词")
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
            self.logger.info("配置保存成功")
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
