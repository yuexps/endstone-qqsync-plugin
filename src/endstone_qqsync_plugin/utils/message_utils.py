"""
消息处理工具模块
用于处理QQ消息的emoji表情符号和CQ码转换
"""

import re


def remove_emoji_for_game(text):
    """
    将emoji表情符号转换为文本描述，供游戏内显示使用
    """
    if not text:
        return text
    
    # 常见emoji映射表
    emoji_map = {
        '😀': '[笑脸]', '😁': '[开心]', '😂': '[笑哭]', '🤣': '[大笑]', '😃': '[微笑]',
        '😄': '[开心]', '😅': '[汗笑]', '😆': '[眯眼笑]', '😉': '[眨眼]', '😊': '[微笑]',
        '😋': '[流口水]', '😎': '[酷]', '😍': '[花眼]', '😘': '[飞吻]', '🥰': '[三颗心]',
        '😗': '[亲吻]', '😙': '[亲吻]', '😚': '[亲吻]', '☺': '[微笑]', '🙂': '[微笑]',
        '🤗': '[拥抱]', '🤩': '[星眼]', '🤔': '[思考]', '🤨': '[怀疑]', '😐': '[面无表情]',
        '😑': '[无语]', '😶': '[无言]', '🙄': '[白眼]', '😏': '[坏笑]', '😣': '[困扰]',
        '😥': '[失望]', '😮': '[惊讶]', '🤐': '[闭嘴]', '😯': '[惊讶]', '😪': '[困倦]',
        '😫': '[疲倦]', '😴': '[睡觉]', '😌': '[安心]', '😛': '[吐舌]', '😜': '[眨眼吐舌]',
        '😝': '[闭眼吐舌]', '🤤': '[流口水]', '😒': '[无聊]', '😓': '[冷汗]', '😔': '[沮丧]',
        '😕': '[困惑]', '🙃': '[倒脸]', '🤑': '[财迷]', '😲': '[震惊]', '☹': '[皱眉]',
        '🙁': '[皱眉]', '😖': '[困扰]', '😞': '[失望]', '😟': '[担心]', '😤': '[愤怒]',
        '😢': '[流泪]', '😭': '[大哭]', '😦': '[皱眉]', '😧': '[痛苦]', '😨': '[害怕]',
        '😩': '[疲倦]', '🤯': '[爆头]', '😬': '[咧嘴]', '😰': '[冷汗]', '😱': '[尖叫]',
        '🥵': '[热]', '🥶': '[冷]', '😳': '[脸红]', '🤪': '[疯狂]', '😵': '[晕]',
        '😡': '[愤怒]', '😠': '[生气]', '🤬': '[咒骂]', '😷': '[口罩]', '🤒': '[生病]',
        '🤕': '[受伤]', '🤢': '[恶心]', '🤮': '[呕吐]', '🤧': '[喷嚏]', '😇': '[天使]',
        '🥳': '[庆祝]', '🥺': '[请求]', '🤠': '[牛仔]', '🤡': '[小丑]', '🤥': '[说谎]',
        '🤫': '[嘘]', '🤭': '[捂嘴笑]', '🧐': '[单片眼镜]', '🤓': '[书呆子]',
        
        # 手势
        '👍': '[赞]', '👎': '[踩]', '👌': '[OK]', '✌': '[胜利]', '🤞': '[交叉手指]',
        '🤟': '[爱你]', '🤘': '[摇滚]', '🤙': '[打电话]', '👈': '[左指]', '👉': '[右指]',
        '👆': '[上指]', '👇': '[下指]', '☝': '[食指]', '✋': '[举手]', '🤚': '[举手背]',
        '🖐': '[张开手]', '🖖': '[瓦肯礼]', '👋': '[挥手]', '🤛': '[左拳]', '🤜': '[右拳]',
        '👊': '[拳头]', '✊': '[拳头]', '👏': '[拍手]', '🙌': '[举双手]', '👐': '[张开双手]',
        '🤲': '[捧手]', '🙏': '[祈祷]', '✍': '[写字]', '💪': '[肌肉]',
        
        # 心形
        '❤': '[红心]', '🧡': '[橙心]', '💛': '[黄心]', '💚': '[绿心]', '💙': '[蓝心]',
        '💜': '[紫心]', '🖤': '[黑心]', '🤍': '[白心]', '🤎': '[棕心]', '💔': '[心碎]',
        '❣': '[心叹号]', '💕': '[两颗心]', '💞': '[旋转心]', '💓': '[心跳]', '💗': '[增长心]',
        '💖': '[闪亮心]', '💘': '[心箭]', '💝': '[心礼盒]', '💟': '[心装饰]',
        
        # 常用符号
        '🔥': '[火]', '💯': '[100分]', '💢': '[愤怒]', '💥': '[爆炸]', '💫': '[星星]',
        '💦': '[汗滴]', '💨': '[风]', '🕳': '[洞]', '💣': '[炸弹]', '💤': '[睡觉]',
        '👀': '[眼睛]', '🗨': '[对话框]', '💭': '[思考泡泡]',
        
        # 动物（常见的）
        '🐶': '[小狗]', '🐱': '[小猫]', '🐭': '[老鼠]', '🐹': '[仓鼠]', '🐰': '[兔子]',
        '🦊': '[狐狸]', '🐻': '[熊]', '🐼': '[熊猫]', '🐨': '[考拉]', '🐯': '[老虎]',
        '🦁': '[狮子]', '🐮': '[牛]', '🐷': '[猪]', '🐽': '[猪鼻]', '🐸': '[青蛙]',
        '🐵': '[猴脸]', '🙈': '[非礼勿视]', '🙉': '[非礼勿听]', '🙊': '[非礼勿言]',
    }
    
    # 替换已知的emoji
    result = text
    for emoji, description in emoji_map.items():
        result = result.replace(emoji, description)
    
    # 使用正则表达式移除其他unicode emoji
    emoji_pattern = re.compile(
        '['
        '\U0001F600-\U0001F64F'  # 表情符号
        '\U0001F300-\U0001F5FF'  # 符号和象形文字
        '\U0001F680-\U0001F6FF'  # 交通和地图符号
        '\U0001F1E0-\U0001F1FF'  # 国旗
        '\U00002600-\U000026FF'  # 杂项符号
        '\U00002700-\U000027BF'  # 装饰符号
        '\U0001F900-\U0001F9FF'  # 补充符号和象形文字
        '\U0001FA70-\U0001FAFF'  # 符号和象形文字扩展-A
        '\U00002300-\U000023FF'  # 杂项技术符号
        '\U0001F000-\U0001F02F'  # 麻将符号
        '\U0001F0A0-\U0001F0FF'  # 扑克符号
        ']+',
        flags=re.UNICODE
    )
    
    # 将未映射的emoji替换为[表情]
    result = emoji_pattern.sub('[表情]', result)
    
    return result


def parse_qq_message(message_data):
    """
    解析QQ消息，将非文本内容转换为对应的标识符
    
    Args:
        message_data (dict): QQ消息数据
        
    Returns:
        str: 处理后的消息文本
    """
    
    # 获取原始消息文本
    raw_message = message_data.get("raw_message", "")
    
    if raw_message:
        # 使用正则表达式解析CQ码
        def replace_cq_code(match):
            cq_type = match.group(1)
            if cq_type == "image":
                return "[图片]"
            elif cq_type == "video":
                return "[视频]"
            elif cq_type == "record":
                return "[语音]"
            elif cq_type == "face":
                return "[表情]"
            elif cq_type == "at":
                # 提取@的QQ号
                params = match.group(2)
                if "qq=all" in params:
                    return "@全体成员"
                else:
                    qq_match = re.search(r'qq=(\d+)', params)
                    if qq_match:
                        return f"@{qq_match.group(1)}"
                    return "@某人"
            elif cq_type == "reply":
                return "[回复]"
            elif cq_type == "forward":
                return "[转发]"
            elif cq_type == "file":
                return "[文件]"
            elif cq_type == "share":
                return "[分享]"
            elif cq_type == "location":
                return "[位置]"
            elif cq_type == "music":
                return "[音乐]"
            elif cq_type == "xml" or cq_type == "json":
                return "[卡片]"
            else:
                return "[非文本]"
        
        # 匹配CQ码格式: [CQ:type,param1=value1,param2=value2]
        cq_pattern = r'\[CQ:([^,\]]+)(?:,([^\]]*))?\]'
        processed_message = re.sub(cq_pattern, replace_cq_code, raw_message)
        
        # 处理emoji表情符号，转换为游戏内可显示的文本
        processed_message = remove_emoji_for_game(processed_message)
        
        # 如果处理后的消息不为空，返回处理结果
        if processed_message.strip():
            return processed_message.strip()
    
    # 如果都没有内容，返回空消息标识
    return "[空消息]"


def clean_message_text(text: str) -> str:
    """
    清理消息文本，移除不必要的字符
    
    Args:
        text (str): 原始文本
        
    Returns:
        str: 清理后的文本
    """
    if not text:
        return text
    
    # 移除多余的空白字符
    text = re.sub(r'\s+', ' ', text.strip())
    
    # 移除控制字符
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    
    return text


def truncate_message(message: str, max_length: int = 500) -> str:
    """
    截断过长的消息
    
    Args:
        message (str): 原始消息
        max_length (int): 最大长度
        
    Returns:
        str: 截断后的消息
    """
    if not message or len(message) <= max_length:
        return message
    
    return message[:max_length - 3] + "..."


def _decode_sensitive_words():
    """解码敏感词列表"""
    import base64
    
    # Base64编码的敏感词数据
    encoded_data = [
        '5Lmg6L+R5bmz', '5q+b5rO95Lic', '6YKT5bCP5bmz', '5rGf5rO95rCR', '6IOh6ZSm5rab', '5p2O5YWL5by6',
        '5Lit5Y2X5rW3', '5aSp5a6J6Zeo', '5YWt5Zub', 'NjQ=', '5rOV6L2u5Yqf', '5aSn5rOV',
        '5rCR6L+Q', '5Y+w54us', '6JeP54us', '55aG54us', 'ZnVjaw==', 'c2hpdA==',
        '5YWx5Lqn5YWa', '5rCR5Li7', '6Ieq55Sx', '5Lq65p2D', '5pq05pS/', '54us6KOB',
        '5pS/5bqc', '5a6Y5ZGY', '6IWQ6LSl', '5Y+N5YWa', '6YCg5Y+N', '6Z2p5ZG9',
        '6Imy5oOF', '5rer56e9', '6buE6Imy', '6KO45L2T', '5oCn54ix', '5YGa54ix',
        '5by65aW4', '6L2u5aW4', '5oCn5Lqk', '6Zi06YGT', '6Zi06IyO', '5Lmz5oi/',
        '5oCn5Zmo5a6Y', '5omL5rer', '6Ieq5oWw', '5Y+j5Lqk', '6IKb5Lqk', '576k5Lqk',
        'YXY=', '5oiQ5Lq6', '5LiJ57qn', '5oOF6Imy', '5r+A5oOF', '6K+x5oOR',
        '5bqK5oiP', '5pil6I2v', '5YKs5oOF', '5aOu6Zyz', '5oCn55eF', '5qKF5q+S',
        '5p2A5Lq6', '6LCL5p2A', '5pqX5p2A', '5p6q5Ye7', '54iG54K4', '5oGQ5oCW',
        '6KGA6IWl', '5pq05Yqb', '5q275Lqh', '6Ieq5p2A', '5LuW5p2A', '5bGg5p2A',
        '56CN5aS0', '5Ymy5ZaJ', '5Yi65p2A', '5q+S5p2A', '54K45by5', '5omL5qa05by5',
        '6LWM5Y2a', '6LWM5Zy6', '5Y2a5b2p', '5b2p56Wo', '5YWt5ZCI5b2p', '5pe25pe25b2p',
        '6LWM6ZKx', '6LWM5rOo', '5LiL5rOo', '5oq85rOo', '5byA55uY', '5bqE5a62',
        '6ICB6JmO5py6', '55m+5a625LmQ', 'MjHngrk=', '5b635bee5omR5YWL', '6K+I6aqX', '6aqX6ZKx',
        '5Lyg6ZSA', '6Z2e5rOV6ZuG6LWE', '5rSX6ZKx', '6buR6ZKx', '6auY5Yip6LS3', '5YCf6LS3',
        '5aWx6Lev6LS3', '5qCh5Zut6LS3', '6KO46LS3', '572R6LS3', '5Yi35Y2V', '5YW86IGM',
        '5Luj5Yi3', '6L+U5Yip', '5q+S5ZOB', '5aSn6bq7', '5rW35rSb5Zug', '5Yaw5q+S',
        '5pGH5aS05Li4', 'a+eyiQ==', '5Y+v5Y2h5Zug', '6bim54mH', '5ZCX5ZWh', '6bq76YaJ5YmC',
        '5YW05aWL5YmC', '6Ie05bm75YmC', '6Ieq5q6L', '6Ieq5Lyk', '5Ymy6IWV', '6Lez5qW8',
        '5LiK5ZCK', '5pyN5q+S', '6YKq5pWZ', '6L+35L+h', '5Y2g5Y2c', '566X5ZG9',
        '6aOG5rC0', '56Se5amG',
    ]
    
    # 解码敏感词
    words = set()
    for encoded in encoded_data:
        try:
            decoded = base64.b64decode(encoded).decode('utf-8')
            words.add(decoded)
        except:
            continue
    
    return words


def get_sensitive_words():
    """获取敏感词集合"""
    return _decode_sensitive_words()


def filter_sensitive_content(text: str, custom_ban_words=None) -> tuple:
    """
    过滤敏感内容，用于游戏消息转发到QQ
    
    Args:
        custom_ban_words: 自定义敏感词
        text (str): 原始文本
        
    Returns:
        tuple: (过滤后的文本, 是否包含敏感内容)
    """
    if custom_ban_words is None:
        custom_ban_words = []
    if not text:
        return text, False
    
    sensitive_words = get_sensitive_words()
    # 合并自定义敏感词
    if custom_ban_words:
        sensitive_words.update(custom_ban_words)
    has_sensitive = False
    
    # 检查并替换敏感词
    for word in sensitive_words:
        if word.lower() in text.lower():
            has_sensitive = True
            # 使用正则表达式进行大小写不敏感的替换
            replacement = '*' * len(word)
            text = re.sub(re.escape(word), replacement, text, flags=re.IGNORECASE)
    
    return text, has_sensitive
