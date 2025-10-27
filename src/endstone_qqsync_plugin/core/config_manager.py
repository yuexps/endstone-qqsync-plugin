"""
配置管理模块
负责插件配置的加载、保存和管理
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Union


class ConfigManager:
    """配置管理器"""
    
    def __init__(self, data_folder: Path, logger):
        self.data_folder = data_folder
        self.logger = logger
        self.config_file = data_folder / "config.json"
        self._config: Dict[str, Any] = {}
        self._color_format = None  # 延迟加载ColorFormat
        self.default_config = {
            "napcat_ws": "ws://127.0.0.1:3001",
            "access_token": "",
            "target_groups": ["712523104"],  # 改为支持多个群聊
            "group_names": {},  # 群组名称映射(可选) {"group_id": "group_name"}，用于在多群场景下区分消息来源
            "admins": ["2899659758"],
            "enable_qq_to_game": True,
            "enable_game_to_qq": True,
            "force_bind_qq": True,
            "sync_group_card": True,
            "check_group_member": True,
            "chat_count_limit": 20,
            "chat_ban_time": 300,
            "api_qq_enable": False
        }
        self._init_config()
    
    @property
    def color_format(self):
        """延迟加载ColorFormat以避免循环依赖"""
        if self._color_format is None:
            from endstone import ColorFormat
            self._color_format = ColorFormat
        return self._color_format
    
    def _init_config(self):
        """初始化配置文件"""
        # 如果配置文件不存在，创建默认配置
        if not self.config_file.exists():
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.default_config, f, indent=2, ensure_ascii=False)
            self.logger.info(f"已创建默认配置文件: {self.config_file}")
        
        # 读取配置
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self._config = json.load(f)
        except Exception as e:
            self.logger.error(f"读取配置文件失败: {e}")
            self._config = self.default_config.copy()
        
        # 检查并合并新的配置项
        config_updated = False
        for key, value in self.default_config.items():
            if key not in self._config:
                self._config[key] = value
                config_updated = True
                self.logger.info(f"添加新配置项: {key}")
        
        # 兼容旧配置格式 - 如果存在target_group单个配置，转换为target_groups数组
        if "target_group" in self._config:
            if "target_groups" not in self._config:
                self._config["target_groups"] = [self._config["target_group"]]
                config_updated = True
                self.logger.info("已将旧配置项 target_group 转换为 target_groups")
            # 删除旧配置项
            del self._config["target_group"]
            config_updated = True
            self.logger.info("已清理旧配置项 target_group")
        
        # 确保 group_names 配置项存在
        if "group_names" not in self._config:
            self._config["group_names"] = {}
            config_updated = True
            self.logger.info("添加新配置项: group_names")
        
        # 生成动态帮助信息
        self._config["help_msg"] = self._generate_help_message()
        
        # 如果有新配置项，保存到文件
        if config_updated:
            self.save_config()
        
        self._log_config_info()
    
    def _get_help_commands(self, include_bind: bool = True, include_admin: bool = False, mark_sections: bool = False) -> str:
        """获取帮助命令文本的通用方法"""
        basic_commands = [
            "/help — 显示本帮助信息",
            "/list — 查看在线玩家列表", 
            "/tps — 查看服务器性能指标",
            "/info — 查看服务器综合信息"
        ]
        
        bind_commands = [
            "/bindqq — 查看QQ绑定状态",
            "/verify <验证码> — 验证QQ绑定"
        ]
        
        admin_commands = [
            "/cmd <命令> — 执行服务器命令",
            "/who <玩家名|QQ号> — 查询玩家详细信息",
            "/unbindqq <玩家名|QQ号> — 解绑玩家的QQ绑定", 
            "/ban <玩家名> [原因] — 封禁玩家",
            "/unban <玩家名> — 解除玩家封禁",
            "/banlist — 查看封禁列表",
            "/tog_qq — 切换QQ消息转发开关",
            "/tog_game — 切换游戏转发开关",
            "/reload — 重新加载配置文件"
        ]
        
        # 构建命令列表
        result = ["QQsync群服互通 - 命令："]
        
        # 查询命令分节
        if mark_sections and include_admin:
            result.append("\n[查询命令]（所有用户可用）：")
        else:
            result.append("\n[查询命令]：")
            
        result.extend(basic_commands)
        
        if include_bind:
            result.extend(bind_commands)
            
        if include_admin:
            # 管理命令分节
            if mark_sections:
                result.append("\n[管理命令]（仅管理员可用）：")
            else:
                result.append("\n[管理命令]：")
            
            # 过滤管理员命令（如果没有绑定功能，则移除绑定相关命令）
            filtered_admin = admin_commands if include_bind else [cmd for cmd in admin_commands if "QQ" not in cmd]
            result.extend(filtered_admin)
            
        return "\n".join(result)

    def _generate_help_message(self) -> str:
        """根据当前配置动态生成帮助信息"""
        force_bind_enabled = self.get_config("force_bind_qq", True)
        return self._get_help_commands(include_bind=force_bind_enabled, include_admin=True)
    
    def get_help_text(self) -> str:
        """获取普通用户帮助文本"""
        force_bind_enabled = self.get_config("force_bind_qq", True)
        return self._get_help_commands(include_bind=force_bind_enabled, include_admin=False)
    
    def get_help_text_with_admin(self) -> str:
        """获取包含管理员命令的帮助文本"""
        force_bind_enabled = self.get_config("force_bind_qq", True)
        return self._get_help_commands(include_bind=force_bind_enabled, include_admin=True, mark_sections=True)
    
    def _log_config_info(self):
        """记录配置信息"""
        ColorFormat = self.color_format
        
        self.logger.info(f"{ColorFormat.AQUA}配置文件已加载{ColorFormat.RESET}")
        self.logger.info(f"{ColorFormat.GOLD}NapCat WebSocket: {ColorFormat.WHITE}{self._config.get('napcat_ws')}{ColorFormat.RESET}")
        self.logger.info(f"{ColorFormat.GOLD}目标QQ群: {ColorFormat.WHITE}{self._config.get('target_groups')}{ColorFormat.RESET}")
        self.logger.info(f"{ColorFormat.GOLD}管理员列表: {ColorFormat.WHITE}{self._config.get('admins')}{ColorFormat.RESET}")
        
        force_bind_enabled = self._config.get('force_bind_qq', True)
        sync_card_enabled = self._config.get('sync_group_card', True)
        check_group_enabled = self._config.get('check_group_member', True)
        
        if force_bind_enabled:
            self.logger.info(f"{ColorFormat.GOLD}强制QQ绑定: {ColorFormat.WHITE}启用 {ColorFormat.YELLOW}(未绑定玩家将限制为访客权限){ColorFormat.RESET}")
            
            if sync_card_enabled:
                self.logger.info(f"{ColorFormat.GOLD}同步群昵称: {ColorFormat.WHITE}启用 {ColorFormat.YELLOW}(绑定成功后自动设置群昵称){ColorFormat.RESET}")
            else:
                self.logger.info(f"{ColorFormat.GOLD}同步群昵称: {ColorFormat.WHITE}禁用{ColorFormat.RESET}")
            
            if check_group_enabled:
                self.logger.info(f"{ColorFormat.GOLD}退群检测: {ColorFormat.WHITE}启用 {ColorFormat.YELLOW}(退群玩家将自动设为访客权限){ColorFormat.RESET}")
            else:
                self.logger.info(f"{ColorFormat.GOLD}退群检测: {ColorFormat.WHITE}禁用{ColorFormat.RESET}")
        else:
            self.logger.info(f"{ColorFormat.GOLD}强制QQ绑定: {ColorFormat.WHITE}禁用 {ColorFormat.YELLOW}(所有玩家享有完整权限){ColorFormat.RESET}")
            self.logger.info(f"{ColorFormat.GOLD}同步群昵称: {ColorFormat.WHITE}禁用 {ColorFormat.GRAY}(依赖强制QQ绑定){ColorFormat.RESET}")
            self.logger.info(f"{ColorFormat.GOLD}退群检测: {ColorFormat.WHITE}禁用 {ColorFormat.GRAY}(依赖强制QQ绑定){ColorFormat.RESET}")
    
    def get_config(self, key: str, default=None) -> Any:
        """获取配置项"""
        return self._config.get(key, default)
    
    def set_config(self, key: str, value: Any):
        """设置配置项"""
        self._config[key] = value
    
    def save_config(self):
        """保存配置到文件"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            self.logger.info("配置已保存")
        except Exception as e:
            self.logger.error(f"保存配置失败: {e}")
    
    def reload_config(self) -> bool:
        """重新加载配置文件"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self._config = json.load(f)
            
            # 检查并合并新的配置项（与_init_config保持一致）
            config_updated = False
            for key, value in self.default_config.items():
                if key not in self._config:
                    self._config[key] = value
                    config_updated = True
                    self.logger.info(f"添加新配置项: {key}")
            
            # 兼容旧配置格式 - 如果存在target_group单个配置，转换为target_groups数组
            if "target_group" in self._config:
                if "target_groups" not in self._config:
                    self._config["target_groups"] = [self._config["target_group"]]
                    config_updated = True
                    self.logger.info("已将旧配置项 target_group 转换为 target_groups")
                # 删除旧配置项
                del self._config["target_group"]
                config_updated = True
                self.logger.info("已清理旧配置项 target_group")
            
            # 确保 group_names 配置项存在
            if "group_names" not in self._config:
                self._config["group_names"] = {}
                config_updated = True
                self.logger.info("添加新配置项: group_names")
            
            # 重新生成动态帮助信息
            self._config["help_msg"] = self._generate_help_message()
            
            # 如果有新配置项，保存到文件
            if config_updated:
                self.save_config()
            
            ColorFormat = self.color_format
            reload_msg = f"{ColorFormat.GREEN}配置已重新加载{ColorFormat.RESET}"
            self.logger.info(reload_msg)
            return True
        except Exception as e:
            self.logger.error(f"重新加载配置失败: {e}")
            return False
    
    @property
    def config(self) -> Dict[str, Any]:
        """获取完整配置"""
        return self._config.copy()