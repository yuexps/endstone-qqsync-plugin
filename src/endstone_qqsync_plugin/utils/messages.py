"""
消息流解析与敏感词中间件过滤系统
"""

import base64
import re
from typing import Any


# 常见表情中英文字符串替换映射表
EMOJI_MAP: dict[str, str] = {
    "😀": "[笑脸]", "😁": "[开心]", "😂": "[笑哭]", "🤣": "[大笑]", "😃": "[微笑]",
    "😄": "[开心]", "😅": "[汗笑]", "😆": "[眯眼笑]", "😉": "[眨眼]", "😊": "[微笑]",
    "😋": "[流口水]", "😎": "[酷]", "😍": "[花眼]", "😘": "[飞吻]", "🥰": "[三颗心]",
    "😗": "[亲吻]", "😙": "[亲吻]", "😚": "[亲吻]", "☺": "[微笑]", "🙂": "[微笑]",
    "🤗": "[拥抱]", "🤩": "[星眼]", "🤔": "[思考]", "🤨": "[怀疑]", "😐": "[面无表情]",
    "😑": "[无语]", "😶": "[无言]", "🙄": "[白眼]", "😏": "[坏笑]", "😣": "[困扰]",
    "😥": "[失望]", "😮": "[惊讶]", "🤐": "[闭嘴]", "😯": "[惊讶]", "😪": "[困倦]",
    "😫": "[疲倦]", "😴": "[睡觉]", "😌": "[安心]", "😛": "[吐舌]", "😜": "[眨眼吐舌]",
    "😝": "[闭眼吐舌]", "🤤": "[流口水]", "😒": "[无聊]", "😓": "[冷汗]", "😔": "[沮丧]",
    "😕": "[困惑]", "🙃": "[倒脸]", "🤑": "[财迷]", "😲": "[震惊]", "☹": "[皱眉]",
    "🙁": "[皱眉]", "😖": "[困扰]", "😞": "[失望]", "😟": "[担心]", "😤": "[愤怒]",
    "😢": "[流泪]", "😭": "[大哭]", "😦": "[皱眉]", "😧": "[痛苦]", "😨": "[害怕]",
    "😩": "[疲倦]", "🤯": "[爆头]", "😬": "[咧嘴]", "😰": "[冷汗]", "😱": "[尖叫]",
    "🥵": "[热]", "🥶": "[冷]", "😳": "[脸红]", "🤪": "[疯狂]", "😵": "[晕]",
    "😡": "[愤怒]", "😠": "[生气]", "🤬": "[咒骂]", "😷": "[口罩]", "🤒": "[生病]",
    "🤕": "[受伤]", "🤢": "[恶心]", "🤮": "[呕吐]", "🤧": "[喷嚏]", "😇": "[天使]",
    "🥳": "[庆祝]", "🥺": "[请求]", "🤠": "[牛仔]", "🤡": "[小丑]", "🤥": "[说谎]",
    "🤫": "[嘘]", "🤭": "[捂嘴笑]", "🧐": "[单片眼镜]", "🤓": "[书呆子]",
    "👍": "[赞]", "👎": "[踩]", "👌": "[OK]", "✌": "[胜利]", "🤞": "[交叉手指]",
    "🤟": "[爱你]", "🤘": "[摇滚]", "🤙": "[打电话]", "👈": "[左指]", "👉": "[右指]",
    "👆": "[上指]", "👇": "[下指]", "☝": "[食指]", "✋": "[举手]", "🤚": "[举手背]",
    "🖐": "[张开手]", "🖖": "[瓦肯礼]", "👋": "[挥手]", "🤛": "[左拳]", "🤜": "[右拳]",
    "👊": "[拳头]", "✊": "[拳头]", "👏": "[拍手]", "🙌": "[举双手]", "👐": "[张开双手]",
    "🤲": "[捧手]", "🙏": "[祈祷]", "✍": "[写字]", "💪": "[肌肉]",
    "❤": "[红心]", "🧡": "[橙心]", "💛": "[黄心]", "💚": "[绿心]", "💙": "[蓝心]",
    "💜": "[紫心]", "🖤": "[黑心]", "🤍": "[白心]", "🤎": "[棕心]", "💔": "[心碎]",
    "❣": "[心叹号]", "💕": "[两颗心]", "💞": "[旋转心]", "💓": "[心跳]", "💗": "[增长心]",
    "💖": "[闪亮心]", "💘": "[心箭]", "💝": "[心礼盒]", "💟": "[心装饰]",
    "🔥": "[火]", "💯": "[100分]", "💢": "[愤怒]", "💥": "[爆炸]", "💫": "[星星]",
    "💦": "[汗滴]", "💨": "[风]", "🕳": "[洞]", "💣": "[炸弹]", "💤": "[睡觉]",
    "👀": "[眼睛]", "🗨": "[对话框]", "💭": "[思考泡泡]",
    "🐶": "[小狗]", "🐱": "[小猫]", "🐭": "[老鼠]", "🐹": "[仓鼠]", "🐰": "[兔子]",
    "🦊": "[狐狸]", "🐻": "[熊]", "🐼": "[熊猫]", "🐨": "[考拉]", "🐯": "[老虎]",
    "🦁": "[狮子]", "🐮": "[牛]", "🐷": "[猪]", "🐽": "[猪鼻]", "🐸": "[青蛙]",
    "🐵": "[猴脸]", "🙈": "[非礼勿视]", "🙉": "[非礼勿听]", "🙊": "[非礼勿言]",
}

# 敏感词库 Base64 原始字符串
ENCODED_SENSITIVE_WORDS: list[str] = [
    "5Lmg6L+R5bmz", "5q+b5rO95Lic", "6YKT5bCP5bmz", "5rGf5rO95rCR", "6IOh6ZSm5rab", "5p2O5YWL5by6",
    "5Lit5Y2X5rW3", "5aSp5a6J6Zeo", "5YWt5Zub", "NjQ=", "5rOV6L2u5Yqf", "5aSn5rOV",
    "5rCR6L+Q", "5Y+w54us", "6JeP54us", "55aG54us", "ZnVjaw==", "c2hpdA==",
    "5YWxr5Lqn5YWa", "5rCR5Li7", "6Ieq55Sx", "5Lq65p2D", "5pq05pS/", "54us6KOB",
    "5pS/5bqc", "5a6Y5ZGY", "6IWQ6LSl", "5Y+N5YWa", "6YCg5Y+N", "6Z2p5ZG9",
    "6Imy5oOF", "5rer56e9", "6buR6Imy", "6KO45L2T", "5oCn54ix", "5YGa54ix",
    "5by65aW4", "6L2u5aW4", "5oCn5Lqk", "6Zi06YGT", "6Zi06IyO", "5Lmz5oi/",
    "5oCn5Zmo5a6Y", "5omL5rer", "6Ieq5oWw", "5Y+j5Lqk", "6IKb5Lqk", "576k5Lqk",
    "YXY=", "5oiQ5Lq6", "5LiJ57qn", "5oOF6Imy", "5r+A5oOF", "6K+x5oOR",
    "5bqK5oiP", "5pil6I2v", "5YKs5oOF", "5aOu6Zyz", "5oCn55eF", "5qKF5q+S",
    "5p2A5Lq6", "6LCL5p2A", "5pqX5p2A", "5p6q5Ye7", "54iG54K4", "5oGQ5oCW",
    "6KGA6IWl", "5pq05Yqb", "5q275Lqh", "6Ieq5p2A", "5LuW5p2A", "5bGg5p2A",
    "56CN5aS0", "5Ymy5ZaJ", "5Yi65p2A", "5q+S5p2A", "54K45by5", "5omL5qa05by5",
    "6LWM5Y2a", "6LWM5Zy6", "5Y2a5b2p", "5b2p56Wo", "5YWt5ZCI5b2p", "5pe25pe25b2p",
    "6LWM6ZKx", "6LWM5rOo", "5LiL5rOo", "5oq85rOo", "5byA55uY", "5bqE5a62",
    "6ICB6JmO5py6", "55m+5a625LmQ", "MjHngrk=", "5b635bee5omR5YWL", "6K+I6aqX", "6aqX6ZKx",
    "5Lyg6ZSA", "6Z2e5rOV6ZuG6LWE", "5rSX6ZKx", "6buR6ZKx", "6auY5Yip6LS3", "5YCf6LS3",
    "5aWx6Lev6LS3", "5qCh5Zut6LS3", "6KO46LS3", "572R6LS3", "5Yi35Y2V", "5YW86IGM",
    "5Luj5Yi3", "6L+U5Yip", "5q+S5ZOB", "5aSn6bq7", "5rW35rSb5Zug", "5Yaw5q+S",
    "5pGH5aS05Li4", "a+eyiQ==", "5Y+v5Y2h5Zug", "6bim54mH", "5ZCX5ZWh", "6bq76YaJ5YmC",
    "5YW05aWL5YmC", "6Ie05bm75YmC", "6Ieq5q6L", "6Ieq5Lyk", "5Ymy6IWV", "6Lez5qW8",
    "5LiK5ZCK", "5pyN5q+S", "6YKq5pWZ", "6L+UtL+h", "5Y2g5Y2c", "566X5ZG9",
    "6aOG5rC0", "56Se5amG",
]

# 内存敏感词单例缓存，避免运行期重复进行 Base64 解码
_DECODED_SENSITIVE_WORDS_CACHE: set[str] = set()


def get_sensitive_words() -> set[str]:
    """获取所有敏感词解密集合（包含惰性加载缓存）"""
    global _DECODED_SENSITIVE_WORDS_CACHE
    if not _DECODED_SENSITIVE_WORDS_CACHE:
        words = set()
        for val in ENCODED_SENSITIVE_WORDS:
            try:
                decoded = base64.b64decode(val).decode("utf-8")
                words.add(decoded)
            except Exception:
                continue
        _DECODED_SENSITIVE_WORDS_CACHE = words
    return _DECODED_SENSITIVE_WORDS_CACHE.copy()


# Unicode EMOJI 正则编译定义
EMOJI_PATTERN: re.Pattern = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # 表情符号
    "\U0001F300-\U0001F5FF"  # 符号和象形
    "\U0001F680-\U0001F6FF"  # 交通和地图
    "\U0001F1E0-\U0001F1FF"  # 国旗
    "\U00002600-\U000026FF"  # 杂项
    "\U00002700-\U000027BF"  # 装饰
    "\U0001F900-\U0001F9FF"  # 补充符号
    "\U0001FA70-\U0001FAFF"  # 符号扩展-A
    "\U00002300-\U000023FF"  # 技术
    "\U0001F000-\U0001F02F"  # 麻将
    "\U0001F0A0-\U0001F0FF"  # 扑克
    "]+",
    flags=re.UNICODE,
)


class MessageMiddleware:
    """消息过滤中间件基类（责任链设计模式）"""

    def __init__(self, next_middleware: "MessageMiddleware | None" = None) -> None:
        self.next_middleware = next_middleware

    def process(self, content: str, ctx: dict[str, Any]) -> tuple[str, bool]:
        """
        处理单条消息节点

        Args:
            content: 输入的消息文本
            ctx: 上下文参数字典

        Returns:
            tuple[str, bool]: (处理完毕的消息文本, 是否立即阻断并丢弃消息)
        """
        if self.next_middleware:
            return self.next_middleware.process(content, ctx)
        return content, False


class CQCodeFilter(MessageMiddleware):
    """QQ 侧消息 CQ 码与 Emoji 占位替换中间件"""

    def process(self, content: str, ctx: dict[str, Any]) -> tuple[str, bool]:
        # 只处理 QQ 发送到游戏的方向
        if ctx.get("direction") != "qq_to_game":
            return super().process(content, ctx)

        if not content:
            return "", False

        # 解析 OneBot 中的 [CQ:type,key=val]
        def _replace_cq(match: re.Match) -> str:
            cq_type = match.group(1)
            mapping = {
                "image": "[图片]",
                "video": "[视频]",
                "record": "[语音]",
                "face": "[表情]",
                "reply": "[回复]",
                "forward": "[转发]",
                "file": "[文件]",
                "share": "[分享]",
                "location": "[位置]",
                "music": "[音乐]",
                "xml": "[卡片]",
                "json": "[卡片]",
            }
            if cq_type in mapping:
                return mapping[cq_type]
            if cq_type == "at":
                params = match.group(2) or ""
                if "qq=all" in params:
                    return "@全体成员"
                qq_match = re.search(r"qq=(\d+)", params)
                if qq_match:
                    return f"@{qq_match.group(1)}"
                return "@某人"
            return "[非文本]"

        cq_pattern = r"\[CQ:([^,\]]+)(?:,([^\]]*))?\]"
        processed = re.sub(cq_pattern, _replace_cq, content)

        # 映射常用文本 emoji
        for emoji, desc in EMOJI_MAP.items():
            processed = processed.replace(emoji, desc)

        # 清除未匹配到的特殊 unicode 表情
        processed = EMOJI_PATTERN.sub("[表情]", processed)
        processed = processed.strip()

        if not processed:
            return "[空消息]", False

        return super().process(processed, ctx)


class SensitiveFilter(MessageMiddleware):
    """游戏消息发送到 QQ 的敏感词屏蔽过滤器"""

    def process(self, content: str, ctx: dict[str, Any]) -> tuple[str, bool]:
        # 仅处理游戏发送到 QQ 的过滤
        if ctx.get("direction") != "game_to_qq":
            return super().process(content, ctx)

        if not content:
            return "", False

        custom_ban_words = ctx.get("custom_ban_words", [])
        sensitive_words = get_sensitive_words()

        # 扩充自定义屏蔽词
        if custom_ban_words:
            sensitive_words.update(custom_ban_words)

        has_sensitive = False
        processed = content

        for word in sensitive_words:
            if word.lower() in processed.lower():
                has_sensitive = True
                replacement = "*" * len(word)
                processed = re.sub(re.escape(word), replacement, processed, flags=re.IGNORECASE)

        if has_sensitive:
            ctx["has_sensitive"] = True

        return super().process(processed, ctx)


class MessageCleanFilter(MessageMiddleware):
    """通用空白字符与首尾字符清理中间件"""

    def process(self, content: str, ctx: dict[str, Any]) -> tuple[str, bool]:
        if not content:
            return "", False

        # 移除不可见控制字符，限制单行空白为单个空格
        processed = re.sub(r"\s+", " ", content.strip())
        processed = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", processed)

        # 长度限制截断
        max_length = ctx.get("max_length", 150)
        if len(processed) > max_length:
            processed = processed[: max_length - 3] + "..."

        return super().process(processed, ctx)


# 封装统一的处理管道调用入口
class MessagePipeline:
    """封装好的消息过滤链条管道入口"""

    def __init__(self) -> None:
        # 组装责任链：CQ码过滤 -> 敏感词过滤 -> 空白格式化清理
        self.chain = CQCodeFilter(SensitiveFilter(MessageCleanFilter()))

    def filter_message(self, text: str, direction: str, custom_ban_words: list[str] | None = None, max_length: int = 150) -> tuple[str, dict[str, Any]]:
        """
        通过管道过滤单条消息

        Args:
            text: 原始文本消息
            direction: 消息流向，可选择 'qq_to_game' 或 'game_to_qq'
            custom_ban_words: 自定义敏感词屏蔽词列表
            max_length: 消息允许的最大截断长度

        Returns:
            tuple[str, dict]: (过滤格式化后的文本, 上下文变动字典)
        """
        ctx: dict[str, Any] = {
            "direction": direction,
            "custom_ban_words": custom_ban_words or [],
            "max_length": max_length,
            "has_sensitive": False,
        }
        res, blocked = self.chain.process(text, ctx)
        if blocked:
            return "", ctx
        return res, ctx
