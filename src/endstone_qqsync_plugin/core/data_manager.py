"""
数据管理模块
负责QQ绑定数据的存储、查询和管理
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from ..utils.time_utils import TimeUtils


class DataManager:
    """数据管理器"""
    
    def __init__(self, data_folder: Path, logger):
        self.data_folder = data_folder
        self.logger = logger
        self.binding_file = data_folder / "data.json"
        self._binding_data: Dict[str, Any] = {}
        self._auto_save_enabled = True
        
        # 新的计时器系统变量
        self._online_timer_start_times: Dict[str, int] = {}  # 玩家在线计时开始时间
        self._last_timer_update: int = 0  # 上次计时器更新时间
        
        self._init_binding_data()
    
    def _init_binding_data(self):
        """初始化QQ绑定数据文件"""
        # 如果绑定数据文件不存在，创建空数据
        if not self.binding_file.exists():
            self.binding_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.binding_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=2, ensure_ascii=False)
            self.logger.info(f"已创建QQ绑定数据文件: {self.binding_file}")
        
        # 读取绑定数据
        try:
            with open(self.binding_file, 'r', encoding='utf-8') as f:
                self._binding_data = json.load(f)
        except Exception as e:
            self.logger.error(f"读取QQ绑定数据失败: {e}")
            self._binding_data = {}
        
        # 更新旧数据结构兼容性
        self._update_data_structure()
        
        from endstone import ColorFormat
        self.logger.info(f"{ColorFormat.AQUA}QQ绑定数据已加载，已绑定玩家: {len(self._binding_data)}{ColorFormat.RESET}")
    
    def _update_data_structure(self):
        """更新数据结构以保持兼容性"""
        data_updated = False
        invalid_bindings = []
        
        for player_name, data in self._binding_data.items():
            # 添加缺失的字段
            required_fields = {
                "total_playtime": 0,
                "last_join_time": None,
                "last_quit_time": None,
                "session_count": 0
            }
            
            for field, default_value in required_fields.items():
                if field not in data:
                    data[field] = default_value
                    data_updated = True
            
            # 检查并清理无效的QQ绑定
            qq_number = data.get("qq", "")
            bind_time = data.get("bind_time", 0)
            
            # 只清理那些QQ为空且没有绑定时间的记录
            if (not qq_number or not qq_number.strip()) and not bind_time:
                invalid_bindings.append(player_name)
                self.logger.warning(f"发现无效绑定：玩家 {player_name} 的QQ号为空且无绑定历史，将被清理")
        
        # 清理无效绑定
        for player_name in invalid_bindings:
            del self._binding_data[player_name]
            data_updated = True
        
        if invalid_bindings:
            self.logger.info(f"已清理 {len(invalid_bindings)} 个无效的QQ绑定")
        
        if data_updated:
            self.save_data()
            if invalid_bindings:
                self.logger.info("已更新绑定数据结构并清理无效绑定")
            else:
                self.logger.info("已更新绑定数据结构以支持在线时间统计")
    
    def save_data(self):
        """保存QQ绑定数据到文件"""
        try:
            # 创建临时文件，避免写入过程中的数据损坏
            temp_file = self.binding_file.with_suffix('.tmp')
            
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self._binding_data, f, indent=2, ensure_ascii=False)
            
            # 原子性替换文件
            temp_file.replace(self.binding_file)
            
        except Exception as e:
            self.logger.error(f"保存QQ绑定数据失败: {e}")
            # 如果临时文件存在，清理它
            temp_file = self.binding_file.with_suffix('.tmp')
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except:
                    pass
    
    def trigger_save(self, reason: str = "数据变更"):
        """触发数据保存"""
        if self._auto_save_enabled:
            self.save_data()
            self.logger.info(f"数据保存: {reason}")
    
    # 玩家绑定相关方法
    def is_player_bound(self, player_name: str, player_xuid: str = None) -> bool:
        """检查玩家是否已绑定QQ"""
        # 如果提供了XUID，优先通过XUID查找
        if player_xuid:
            player_data = self._get_player_by_xuid(player_xuid)
            if player_data:
                qq_number = player_data.get("qq", "")
                return bool(qq_number and qq_number.strip())
            else:
                # 通过XUID没有找到，继续用玩家名查找（向后兼容）
                if player_name in self._binding_data:
                    qq_number = self._binding_data[player_name].get("qq", "")
                    return bool(qq_number and qq_number.strip())
                return False
        
        # 仅基于玩家名的检查
        if player_name not in self._binding_data:
            return False
        
        qq_number = self._binding_data[player_name].get("qq", "")
        return bool(qq_number and qq_number.strip())
    
    def _get_player_by_xuid(self, xuid: str) -> Dict[str, Any]:
        """根据XUID获取玩家绑定信息"""
        for name, data in self._binding_data.items():
            if data.get("xuid") == xuid:
                return data
        return {}
    
    def get_player_qq(self, player_name: str) -> str:
        """获取玩家绑定的QQ号"""
        return self._binding_data.get(player_name, {}).get("qq", "")
    
    def get_qq_player(self, qq_number: str) -> str:
        """根据QQ号获取绑定的玩家名"""
        for name, data in self._binding_data.items():
            current_qq = data.get("qq", "")
            if current_qq and current_qq.strip() == qq_number:
                return name
        return ""
    
    def get_qq_player_history(self, qq_number: str) -> str:
        """根据QQ号获取历史绑定的玩家名（包括已解绑的）"""
        for name, data in self._binding_data.items():
            # 首先检查当前绑定的QQ号
            if data.get("qq") == qq_number:
                return name
            # 检查原QQ号（用于被解绑或封禁的玩家历史查询）
            if data.get("original_qq") == qq_number:
                return name
        return ""
    
    def get_player_by_xuid(self, xuid: str) -> Dict[str, Any]:
        """根据XUID获取玩家绑定信息"""
        return self._get_player_by_xuid(xuid)
    
    def bind_player_qq(self, player_name: str, player_xuid: str, qq_number: str) -> bool:
        """绑定玩家QQ"""
        # 验证参数
        if not qq_number or not qq_number.strip():
            self.logger.error(f"尝试绑定空QQ号给玩家 {player_name}，操作被拒绝")
            return False
        
        qq_clean = qq_number.strip()
        if not qq_clean.isdigit() or len(qq_clean) < 5 or len(qq_clean) > 11:
            self.logger.error(f"尝试绑定无效QQ号 {qq_clean} 给玩家 {player_name}，QQ号必须是5-11位数字")
            return False
        
        if not player_name or not player_name.strip():
            self.logger.error(f"尝试绑定QQ {qq_clean} 给空玩家名，操作被拒绝")
            return False
        
        # 检查是否已有该玩家的数据
        if player_name in self._binding_data:
            # 保留现有的游戏数据，更新绑定信息
            player_data = self._binding_data[player_name]
            old_qq = player_data.get("qq", "")
            
            # 更新绑定信息
            player_data["qq"] = qq_clean
            player_data["xuid"] = player_xuid
            
            if old_qq:
                # 重新绑定
                player_data["rebind_time"] = int(TimeUtils.get_timestamp())
                player_data["previous_qq"] = old_qq
                self.logger.info(f"玩家 {player_name} 重新绑定QQ: {old_qq} → {qq_clean}")
            else:
                # 首次绑定或解绑后重新绑定
                if "unbind_time" in player_data:
                    player_data["rebind_time"] = int(TimeUtils.get_timestamp())
                    self.logger.info(f"玩家 {player_name} 解绑后重新绑定QQ: {qq_clean}")
                else:
                    player_data["bind_time"] = int(TimeUtils.get_timestamp())
                    self.logger.info(f"玩家 {player_name} 首次绑定QQ: {qq_clean}")
        else:
            # 全新的玩家数据
            self._binding_data[player_name] = {
                "name": player_name,
                "xuid": player_xuid,
                "qq": qq_clean,
                "bind_time": int(TimeUtils.get_timestamp()),
                "total_playtime": 0,
                "last_join_time": None,
                "last_quit_time": None,
                "session_count": 0
            }
            self.logger.info(f"玩家 {player_name} 已绑定QQ: {qq_clean}")
        
        self.trigger_save(f"绑定QQ: {player_name} → {qq_clean}")
        return True
    
    def unbind_player_qq(self, player_name: str, admin_name: str = "system") -> bool:
        """解绑玩家QQ（保留游戏数据）"""
        if player_name not in self._binding_data:
            return False
        
        player_data = self._binding_data[player_name]
        original_qq = player_data.get("qq", "")
        
        if not original_qq or not original_qq.strip():
            return False
        
        # 保留所有游戏数据，只清空QQ相关信息
        player_data["qq"] = ""
        player_data["unbind_time"] = int(TimeUtils.get_timestamp())
        player_data["unbind_by"] = admin_name
        player_data["original_qq"] = original_qq
        
        self.trigger_save(f"解绑QQ: {player_name} (原QQ: {original_qq})")
        self.logger.info(f"玩家 {player_name} 的QQ绑定已被 {admin_name} 解除 (原QQ: {original_qq})，游戏数据已保留")
        return True
    
    def update_player_name(self, old_name: str, new_name: str, xuid: str) -> bool:
        """更新玩家名称（处理改名情况）"""
        if old_name in self._binding_data:
            # 保存原有数据
            player_data = self._binding_data[old_name].copy()
            # 更新名称
            player_data["name"] = new_name
            player_data["last_name_update"] = int(TimeUtils.get_timestamp())
            
            # 删除旧记录，添加新记录
            del self._binding_data[old_name]
            self._binding_data[new_name] = player_data
            
            self.trigger_save(f"玩家改名: {old_name} → {new_name}")
            self.logger.info(f"玩家改名: {old_name} → {new_name} (XUID: {xuid})")
            return True
        return False
    
    # 游戏统计相关方法
    def update_player_join(self, player_name: str, player_xuid: str = None):
        """更新玩家加入时间和进服次数（为所有玩家记录，不检查QQ绑定）"""
        current_time = int(TimeUtils.get_timestamp())
        
        # 确保玩家数据存在，如果不存在则创建
        if player_name not in self._binding_data:
            self._binding_data[player_name] = {
                "name": player_name,
                "xuid": player_xuid or "",
                "qq": "",
                "total_playtime": 0,
                "last_join_time": current_time,
                "last_quit_time": None,
                "session_count": 0
            }
        
        # 更新加入时间和会话计数
        self._binding_data[player_name]["last_join_time"] = current_time
        self._binding_data[player_name]["session_count"] = self._binding_data[player_name].get("session_count", 0) + 1
        
        # 更新XUID（如果提供了新的XUID）
        if player_xuid and not self._binding_data[player_name].get("xuid"):
            self._binding_data[player_name]["xuid"] = player_xuid
        
        self.trigger_save(f"玩家加入: {player_name}")
    
    def update_player_quit(self, player_name: str):
        """更新玩家离开时间（为所有玩家记录，不检查QQ绑定，不处理在线时长累计）"""
        if player_name not in self._binding_data:
            return
        
        current_time = int(TimeUtils.get_timestamp())
        self._binding_data[player_name]["last_quit_time"] = current_time
        
        # 注意：在线时长累计现在由计时器系统处理，这里只记录退出时间
        
        self.trigger_save(f"玩家离开: {player_name}")
    
    def get_player_playtime_info(self, player_name: str, online_players: List[Any]) -> Dict[str, Any]:
        """使用计时器系统获取玩家在线时间信息"""
        if player_name not in self._binding_data:
            return {}
        
        data = self._binding_data[player_name]
        
        current_time = int(TimeUtils.get_timestamp())
        total_playtime = data.get("total_playtime", 0)
        
        # 检查玩家是否在线且正在计时
        is_online = False
        current_session_time = 0
        if player_name in self._online_timer_start_times:
            is_online = True
            start_time = self._online_timer_start_times[player_name]
            current_session_time = current_time - start_time
        
        total_with_current = total_playtime + current_session_time
        
        return {
            "total_playtime": total_with_current,
            "session_count": data.get("session_count", 0),
            "last_join_time": data.get("last_join_time"),
            "last_quit_time": data.get("last_quit_time"),
            "is_online": is_online,
            "current_session_time": current_session_time,
            "bind_time": data.get("bind_time")
        }
    
    # 封禁相关方法
    def is_player_banned(self, player_name: str) -> bool:
        """检查玩家是否被封禁"""
        if player_name not in self._binding_data:
            return False
        return self._binding_data[player_name].get("is_banned", False)
    
    def ban_player(self, player_name: str, admin_name: str = "system", reason: str = "") -> bool:
        """封禁玩家"""
        # 确保玩家数据存在
        if player_name not in self._binding_data:
            self._binding_data[player_name] = {
                "name": player_name,
                "xuid": "",
                "qq": "",
                "total_playtime": 0,
                "last_join_time": None,
                "last_quit_time": None,
                "session_count": 0
            }
        
        # 设置封禁状态
        player_data = self._binding_data[player_name]
        player_data["is_banned"] = True
        player_data["ban_time"] = int(TimeUtils.get_timestamp())
        player_data["ban_by"] = admin_name
        player_data["ban_reason"] = reason or "管理员封禁"
        
        # 如果玩家已绑定QQ，解除绑定
        if player_data.get("qq"):
            original_qq = player_data["qq"]
            player_data["qq"] = ""
            player_data["unbind_time"] = int(TimeUtils.get_timestamp())
            player_data["unbind_by"] = admin_name
            player_data["unbind_reason"] = "封禁时自动解绑"
            player_data["original_qq"] = original_qq
            self.logger.info(f"玩家 {player_name} 被封禁时自动解除QQ绑定 (原QQ: {original_qq})")
        
        self.trigger_save(f"封禁玩家: {player_name} (原因: {reason or '管理员封禁'})")
        self.logger.info(f"玩家 {player_name} 已被 {admin_name} 封禁，原因：{reason or '管理员封禁'}")
        return True
    
    def unban_player(self, player_name: str, admin_name: str = "system") -> bool:
        """解封玩家"""
        if player_name not in self._binding_data:
            return False
        
        player_data = self._binding_data[player_name]
        if not player_data.get("is_banned", False):
            return False
        
        # 解除封禁
        player_data["is_banned"] = False
        player_data["unban_time"] = int(TimeUtils.get_timestamp())
        player_data["unban_by"] = admin_name
        
        self.trigger_save(f"解封玩家: {player_name}")
        self.logger.info(f"玩家 {player_name} 已被 {admin_name} 解封")
        return True
    
    def get_banned_players(self) -> List[Dict[str, Any]]:
        """获取所有被封禁的玩家列表"""
        banned_players = [
            {
                "name": player_name,
                "ban_time": data.get("ban_time"),
                "ban_by": data.get("ban_by", "unknown"),
                "ban_reason": data.get("ban_reason", "无原因")
            }
            for player_name, data in self._binding_data.items()
            if data.get("is_banned", False)
        ]
        return banned_players
    
    def get_player_binding_history(self, player_name: str) -> Dict[str, Any]:
        """获取玩家绑定历史信息"""
        if player_name not in self._binding_data:
            return {}
        
        data = self._binding_data[player_name]
        
        history = {
            "current_qq": data.get("qq", ""),
            "is_bound": bool(data.get("qq", "").strip()),
            "bind_time": data.get("bind_time"),
            "unbind_time": data.get("unbind_time"),
            "rebind_time": data.get("rebind_time"),
            "unbind_by": data.get("unbind_by"),
            "original_qq": data.get("original_qq"),
            "previous_qq": data.get("previous_qq"),
            "total_playtime": data.get("total_playtime", 0),
            "session_count": data.get("session_count", 0),
        }
        
        # 计算绑定状态
        if history["is_bound"]:
            if history["rebind_time"]:
                history["status"] = "重新绑定"
            else:
                history["status"] = "已绑定"
        else:
            if history["unbind_time"]:
                history["status"] = "已解绑"
            else:
                history["status"] = "从未绑定"
        
        return history
    
    def get_complete_player_binding_status(self, player_name: str, player_xuid: str) -> Dict[str, Any]:
        """获取玩家完整的绑定状态信息"""
        result = {
            "is_bound": False,
            "qq_number": "",
            "binding_source": "",
            "data_consistent": True,
            "issues": []
        }
        
        # 检查基于玩家名的绑定
        name_bound = False
        name_qq = ""
        if player_name in self._binding_data:
            name_qq = self._binding_data[player_name].get("qq", "")
            name_bound = bool(name_qq and name_qq.strip())
        
        # 检查基于XUID的绑定
        xuid_data = self._get_player_by_xuid(player_xuid)
        xuid_bound = False
        xuid_qq = ""
        if xuid_data:
            xuid_qq = xuid_data.get("qq", "")
            xuid_bound = bool(xuid_qq and xuid_qq.strip())
        
        # 分析绑定状态
        if name_bound and xuid_bound:
            if name_qq == xuid_qq:
                result["is_bound"] = True
                result["qq_number"] = name_qq
                result["binding_source"] = "both"
            else:
                result["is_bound"] = False
                result["data_consistent"] = False
                result["issues"].append(f"QQ号不一致: 玩家名对应{name_qq}, XUID对应{xuid_qq}")
        elif name_bound and not xuid_bound:
            result["is_bound"] = name_bound
            result["qq_number"] = name_qq
            result["binding_source"] = "name"
            result["issues"].append("仅玩家名有绑定记录，XUID无对应数据")
        elif not name_bound and xuid_bound:
            result["is_bound"] = xuid_bound
            result["qq_number"] = xuid_qq
            result["binding_source"] = "xuid"
            result["issues"].append("仅XUID有绑定记录，当前玩家名无对应数据")
        else:
            result["is_bound"] = False
            result["binding_source"] = "none"
        
        return result
    
    @property
    def binding_data(self) -> Dict[str, Any]:
        """获取完整绑定数据的副本"""
        return self._binding_data.copy()

    # 新的计时器系统方法
    def start_player_timer(self, player_name: str, player_xuid: str = None):
        """开始玩家在线计时"""
        # 检查是否已经在计时中，避免重复计时
        if player_name in self._online_timer_start_times:
            return
        
        current_time = int(TimeUtils.get_timestamp())
        self._online_timer_start_times[player_name] = current_time
        
        # 确保玩家数据存在，如果不存在则创建
        if player_name not in self._binding_data:
            self._binding_data[player_name] = {
                "name": player_name,
                "xuid": player_xuid or "",
                "qq": "",
                "total_playtime": 0,
                "last_join_time": current_time,
                "last_quit_time": None,
                "session_count": 0
            }
        
        # 更新XUID（如果提供了新的XUID）
        if player_xuid and not self._binding_data[player_name].get("xuid"):
            self._binding_data[player_name]["xuid"] = player_xuid
        
        self.logger.info(f"玩家 {player_name} 开始在线计时")

    def stop_player_timer(self, player_name: str):
        """停止玩家在线计时"""
        if player_name not in self._online_timer_start_times:
            return
        
        start_time = self._online_timer_start_times[player_name]
        current_time = int(TimeUtils.get_timestamp())
        session_time = current_time - start_time
        
        if session_time > 0 and player_name in self._binding_data:
            # 累加到总在线时间
            self._binding_data[player_name]["total_playtime"] = self._binding_data[player_name].get("total_playtime", 0) + session_time
            self.logger.info(f"玩家 {player_name} 停止在线计时，本次会话时长: {session_time}秒")
        
        # 移除计时器记录
        del self._online_timer_start_times[player_name]

    def update_online_timers(self, online_players: List[Any]):
        """更新所有在线玩家的计时器"""
        current_time = int(TimeUtils.get_timestamp())
        
        # 获取当前在线的玩家名列表
        online_player_names = set()
        for player in online_players:
            if hasattr(player, 'name') and hasattr(player, 'xuid'):
                online_player_names.add(player.name)
        
        # 为新上线但未开始计时的玩家开始计时
        for player in online_players:
            if (hasattr(player, 'name') and hasattr(player, 'xuid') and 
                player.name not in self._online_timer_start_times):
                self.start_player_timer(player.name, player.xuid)
        
        # 停止已离线玩家的计时
        offline_players = []
        for player_name in list(self._online_timer_start_times.keys()):
            if player_name not in online_player_names:
                offline_players.append(player_name)
        
        for player_name in offline_players:
            self.stop_player_timer(player_name)
        
        # 每5分钟保存一次在线时长数据（防止意外关机丢失数据）
        if current_time - self._last_timer_update >= 300:  # 5分钟
            self._save_timer_progress()
            self._last_timer_update = current_time

    def _save_timer_progress(self):
        """保存当前在线玩家的计时进度"""
        current_time = int(TimeUtils.get_timestamp())
        
        for player_name, start_time in self._online_timer_start_times.items():
            if player_name in self._binding_data:
                # 计算从开始计时到现在的时间
                session_time = current_time - start_time
                if session_time > 0:
                    # 累加到总在线时间
                    self._binding_data[player_name]["total_playtime"] = self._binding_data[player_name].get("total_playtime", 0) + session_time
                    # 重置开始时间
                    self._online_timer_start_times[player_name] = current_time
        
        # 保存数据
        self.save_data()
        if self._online_timer_start_times:
            self.logger.info(f"已保存 {len(self._online_timer_start_times)} 个在线玩家的计时进度")

    def cleanup_timer_system(self):
        """清理计时器系统（在插件禁用时调用）"""
        if self._online_timer_start_times:
            self.logger.info("正在清理在线计时器系统...")
            # 保存所有在线玩家的最终计时进度
            self._save_timer_progress()
            # 清空计时器
            self._online_timer_start_times.clear()
            self.logger.info("计时器系统清理完成")
