from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools
from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest
from astrbot.api.provider import LLMResponse
import astrbot.api.message_components as Comp
from astrbot.api.message_components import Video, Image, At
from astrbot.core.message.components import Reply
import re
import asyncio
import base64
import json
import time
import aiohttp
from datetime import datetime
from pathlib import Path

from .core.gitee_draw import GiteeDrawService
from .core.gemini_draw import GeminiDrawService
from .core.grok_draw import GrokDrawService
from .core.grok_video_service import GrokVideoService
from .core.video_manager import VideoManager
from .core.image_manager import ImageManager
from .core.defaults import (
    DEFAULT_ENVIRONMENTS,
    DEFAULT_CAMERAS,
    TPL_HEADER,
    TPL_CHAR,
    TPL_MIDDLE,
    TPL_FOOTER,
)
from .web_server import WebServer


class PortraitPlugin(Star):
    """人物特征Prompt注入器,增强美化画图,内置Gitee AI文生图"""

    def __init__(self, context: Context, config: dict | None):
        super().__init__(context)

        # === Config validation (Issue 3 fix) ===
        if not isinstance(config, dict):
            logger.warning(f"[Portrait] Invalid config type {type(config).__name__}; using defaults")
            config = {}
        self.config = config
        self.data_dir = StarTools.get_data_dir()

        # 动态配置文件路径（由 WebUI 管理）
        self.dynamic_config_path = self.data_dir / "dynamic_config.json"
        # 主配置持久化路径
        self.config_persist_path = self.data_dir / "webui_config.json"

        # 加载持久化的 WebUI 配置（覆盖默认值）
        self._load_persisted_config()

        # 加载动态配置（环境和摄影模式）
        self._dynamic_config = self._load_dynamic_config()

        # === v1.9.0: 生命周期管理 ===
        # 防止重载时旧实例复活
        self._is_terminated = False
        # 后台任务追踪（用于生命周期清理）
        self._bg_tasks = set()

        # v1.6.0: One-Shot 单次注入策略
        # 仅在检测到绘图意图时注入 Visual Context，节省 Token 并避免上下文污染
        # === Issue 1 fix: Refactored to list format for easier maintenance ===
        trigger_keywords = [
            # 基础绘图意图
            r'画', r'拍', r'照', r'自拍', r'全身', r'穿搭', r'爆照', r'形象', r'样子',
            # 英文触发词
            r'draw', r'photo', r'selfie', r'picture', r'image', r'shot', r'snap',
            r'ootd', r'outfit', r'look',
            # 查看/发图表达：看看、康康、瞧瞧、瞅瞅、给我看、来张
            r'[看康瞧瞅]{2}',
            r'(?:给我|让我)[看康瞧瞅]',
            r'(?:发|来)[张个一]',
            # 状态询问与连续请求
            r'在干(?:嘛|啥|什么)',
            r'干什么呢',
            r'现在.{0,3}样子',
            r'再来一', r'再拍', r'再画',
            # 场景位置：在画室、在卧室、在厨房、在客厅等
            r'在(?:画室|卧室|厨房|客厅|浴室|阳台|书房|办公室|学校|教室|公园|海边|床上|沙发|窗边|镜子前|家|房间|茶水间|走廊|楼梯|天台|餐厅|咖啡厅)',
            # 姿态动作：坐着/站着/躺着/蹲着/跪着/趴着
            r'[坐站躺蹲跪趴]着',
            # 日常活动：吃饭/睡觉/看书/玩手机/做饭/喝饮品
            r'(?:吃饭|睡觉|看书|玩手机|做饭)',
            r'喝(?:水|咖啡|茶)',
        ]
        self.trigger_regex = re.compile(f"({'|'.join(trigger_keywords)})", re.IGNORECASE)

        # === 预编译角色相关关键词正则（性能优化）===
        # 英文关键词（需要词边界避免误匹配）
        english_keywords = [
            'girl', 'woman', 'lady', 'female', 'person', 'human',
            'selfie', 'portrait', 'headshot', 'profile', 'cosplay',
            'face', 'body', 'eyes', 'ootd',
        ]
        # 中文关键词（直接匹配）
        chinese_keywords = [
            # 中文 - 人物
            '女孩', '女生', '女性', '人物', '人像', '美女', '小姐姐',
            # 中文 - 自拍/照片相关
            '自拍', '肖像', '头像', '形象', '写真', '爆照',
            # 中文 - 身体部位（更精确）
            '脸蛋', '眼睛', '腿部', '身材',
            # 中文 - 穿搭/外貌/服装（保留不可由正则稳定泛化的独立词）
            '衣服', '裙子', '裤子', '发型', '头发', '妆容',
            '女仆装', '女仆', '旗袍', 'JK', 'jk', '制服', '泳装', '比基尼',
            '睡衣', '内衣', '婚纱', '晚礼服', '汉服', 'lolita', '洛丽塔',
            '丝袜', '黑丝', '白丝', '过膝袜', '短裙', '长裙', '连衣裙',
            '校服', '护士装', '和服', '猫耳', '兔耳',
            # 中文 - 常见独立表达（其余变体交由 pattern_keywords 覆盖）
            '本人', '真人', '查岗', '照片',
        ]
        # 模糊匹配模式（正则表达式）
        pattern_keywords = [
            # 图片请求：再来一张/换一张/重新拍
            r'再来一[张个]?',  # 匹配：再来一张 / 再来一个
            r'再(?:拍|画|发|给)一[张个]?',  # 匹配：再拍一张 / 再画一个 / 再发一张
            r'换(?:一)?张',  # 匹配：换张 / 换一张
            r'重新(?:画|拍|发)',  # 匹配：重新画 / 重新拍 / 重新发
            # 发图/要图：发一张、来个图、给我看
            r'[发来给要](?:一)?[张个](?:照片|图)?',  # 匹配：发一张照片 / 来个图 / 给张
            r'[发来给要](?:我)?(?:看|康|瞧|瞅)',  # 匹配：发我看 / 来康康 / 给我瞧
            r'(?:给我|让我)(?:看|康|瞧|瞅)',  # 匹配：给我看 / 让我看
            # 穿搭/外观：穿搭、今日穿搭、今天穿搭、ootd
            r'(?:今[日天])?穿搭',  # 匹配：穿搭 / 今日穿搭 / 今天穿搭
            r'ootd',  # 匹配：英文穿搭词 ootd
            r'全身(?:照|图|像)?',  # 匹配：全身 / 全身照 / 全身图
            # 查看表达：看看你、康康、瞧瞧、瞅瞅
            r'[看康瞧瞅]{2}(?:你|下|一下)?',  # 匹配：看看 / 康康你 / 瞧瞧一下
            # 照片/图片请求：看照片、发图片、来张照片
            r'(?:看|发|来|要).{0,6}(?:照片|图)',  # 匹配：看照片 / 发个图 / 来张照片
            r'(?:照片|图).{0,6}(?:给我|让我|给你|发来|看看|康康)',  # 匹配：照片给我看 / 图发来
            r'拍(?:一)?[张个]?(?:照|照片)',  # 匹配：拍照 / 拍一张照 / 拍个照片
            # 状态与外貌询问：在干嘛、长什么样
            r'在干(?:嘛|啥|什么)(?:呢)?',  # 匹配：在干嘛 / 在干什么呢
            r'干嘛呢',  # 匹配：干嘛呢
            r'长什么样(?:子)?',  # 匹配：长什么样 / 长什么样子
            r'什么样子',  # 匹配：什么样子
            # 场景与姿态：在画室、在卧室、坐着、站着
            r'在(?:画室|卧室|厨房|客厅|浴室|阳台|书房|办公室|学校|教室|公园|海边|床上|沙发|窗边|镜子前|家|房间|茶水间|走廊|楼梯|天台|餐厅|咖啡厅)',  # 匹配常见角色所处场景
            r'[坐站躺蹲跪趴]着',  # 匹配：坐着 / 站着 / 躺着等
        ]
        # 合并为单个正则：英文用词边界，中文直接匹配
        english_patterns = [rf'\b{re.escape(kw)}\b' for kw in english_keywords]
        chinese_patterns = [re.escape(kw) for kw in chinese_keywords]
        all_patterns = english_patterns + chinese_patterns + pattern_keywords
        self._char_keyword_regex = re.compile(
            '|'.join(all_patterns),
            re.IGNORECASE
        )

        # === 高频路径正则预编译（性能优化）===
        self._portrait_status_pattern = re.compile(
            r'\s*<portrait_status>.*?</portrait_status>\s*',
            re.DOTALL,
        )
        self._character_state_pattern = re.compile(
            r'<character_state>(.*?)</character_state>',
            re.DOTALL,
        )
        self._state_outfit_pattern = re.compile(
            r'穿着[：:]\s*(.+?)(?=\n日程[：:]|\n时间[：:]|$)',
            re.DOTALL,
        )
        self._state_schedule_pattern = re.compile(r'日程[：:]\s*(.+?)$', re.DOTALL)
        self._schedule_time_pattern = re.compile(
            r'(\d{1,2}:\d{2})\s+(.+?)(?=\n\d{1,2}:\d{2}|$)',
            re.DOTALL,
        )
        self._video_cmd_pattern = re.compile(r'[./]?视频\s+(.+)', re.DOTALL)
        self._img_url_pattern = re.compile(
            r'(\d+_[a-f0-9]+\.(jpg|jpeg|png|gif|webp))',
            re.IGNORECASE,
        )
        # 回应性词汇正则（用户回应角色消息）
        response_patterns = [
            r'吃饱', r'吃完', r'好吃', r'好喝', r'好看', r'真棒', r'辛苦',
            r'早安', r'晚安', r'午安', r'早上好', r'晚上好', r'下午好',
            r'起床', r'睡觉', r'睡了', r'醒了', r'累了', r'困了',
            r'开心', r'高兴', r'难过', r'伤心', r'生气',
            r'干嘛呢', r'在干嘛', r'做什么呢', r'忙什么',
            r'怎么了', r'怎么样', r'还好吗', r'好点没',
            r'宝宝', r'宝贝', r'亲爱的', r'老婆', r'老公', r'媳妇',
            r'乖', r'棒', r'厉害', r'可爱', r'漂亮', r'好美',
            r'想你', r'爱你', r'喜欢你', r'抱抱', r'亲亲', r'摸摸',
            r'然后呢', r'接下来', r'后来呢', r'继续',
        ]
        self._response_regex = re.compile('|'.join(response_patterns), re.IGNORECASE)
        # 上下文角色活动关键词正则
        context_keywords = [
            r'吃', r'喝', r'做饭', r'下厨', r'烹饪',
            r'穿', r'换衣', r'打扮',
            r'睡', r'躺', r'起床', r'休息',
            r'洗', r'刷', r'泡澡', r'洗澡',
            r'看', r'读', r'玩', r'听',
            r'画', r'写', r'工作', r'学习',
            r'拍', r'照', r'自拍',
            r'发', r'给你', r'送你',
        ]
        self._context_regex = re.compile('|'.join(context_keywords))

        # 读取用户配置（留空则不注入，使用 AstrBot 默认人格）
        p_char_id = self.config.get("char_identity", "") or ""
        # 存储角色外貌配置，用于在画图时自动添加
        self.char_identity = p_char_id.replace("> **", "").replace("**", "").strip()

        # 读取开关配置
        self.enable_env_injection = self.config.get("enable_env_injection", True)
        self.enable_camera_injection = self.config.get("enable_camera_injection", True)
        # 是否自动添加角色外貌到 prompt
        self.auto_prepend_identity = self.config.get("auto_prepend_identity", True)

        # === 初始化 full_prompt（复用 rebuild 方法避免重复代码）===
        self.full_prompt = ""
        self.rebuild_full_prompt()

        # === v1.8.1: 注入轮次控制 ===
        # 每个会话的剩余注入次数 {session_id: remaining_count}
        self.injection_counter = {}
        # 会话最后活跃时间，用于清理过期条目 {session_id: timestamp}
        self.injection_last_active = {}
        # 从配置读取注入轮次，默认为 1（单次注入）
        self.injection_rounds = max(1, self.config.get("injection_rounds", 1))
        # 会话过期时间（秒），默认 1 小时
        self.session_ttl = 3600

        # === 高频路径缓存（性能优化）===
        self._banana_prefixes_cache: set[str] | None = None
        self._banana_prefixes_cache_time: float = 0.0
        self._banana_prefixes_cache_ttl: float = 60.0
        self._last_session_cleanup_ts: float = 0.0
        self._session_cleanup_interval: float = 60.0

        # === v2.9.4: 消息ID与图片路径映射（用于删图命令）===
        # {message_id: image_path}
        self.sent_images: dict[str, Path] = {}
        # 最大记录数，防止内存无限增长
        self.max_sent_images = 100

        # === v2.9.5: 冷却时间控制 ===
        # 冷却时间（秒），0 表示无冷却
        self.cooldown_seconds = max(0, self.config.get("cooldown_seconds", 0))
        # 用户最后使用时间 {user_id: timestamp}
        self.user_last_use: dict[str, float] = {}

        # === v3.0.0: Grok AI 配置（图片+视频共用）===
        grok_conf = self.config.get("grok_config", {}) or {}
        cache_conf = self.config.get("cache_config", {}) or {}

        # Grok 图片生成服务
        self.grok_draw = GrokDrawService(
            data_dir=self.data_dir,
            api_key=grok_conf.get("api_key", "") or "",
            base_url=grok_conf.get("base_url", "https://api.x.ai") or "https://api.x.ai",
            model=grok_conf.get("image_model", "grok-imagine-1.0") or "grok-imagine-1.0",
            default_size=grok_conf.get("size", "1024x1024") or "1024x1024",
            timeout=grok_conf.get("timeout", 180) or 180,
            max_retries=grok_conf.get("max_retries", 2) or 2,
            proxy=self.config.get("proxy", "") or None,
            max_storage_mb=cache_conf.get("max_storage_mb", 500) or 500,
            max_count=cache_conf.get("max_count", 100) or 100,
        )

        # Grok 视频生成服务（共用 API Key 和 Base URL）
        # 从 grok_config 读取预设词（字符串格式 "预设名:提示词"）
        raw_video_presets = grok_conf.get("video_presets", []) or []
        # 迁移旧格式：将对象格式转换为字符串格式
        video_presets = []
        needs_migration = False
        for p in raw_video_presets:
            if isinstance(p, str):
                video_presets.append(p)
            elif isinstance(p, dict):
                # 旧格式 {keyword, prompt} -> "keyword:prompt"
                keyword = (p.get("keyword") or "").strip()
                prompt = (p.get("prompt") or "").strip()
                if keyword and prompt:
                    video_presets.append(f"{keyword}:{prompt}")
                    needs_migration = True
        if needs_migration:
            grok_conf["video_presets"] = video_presets
            self.config["grok_config"] = grok_conf
        video_settings = {
            "api_key": grok_conf.get("api_key", "") or "",
            "server_url": grok_conf.get("base_url", "https://api.x.ai") or "https://api.x.ai",
            "model": grok_conf.get("video_model", "") or grok_conf.get("model", "grok-imagine-1.0-video") or "grok-imagine-1.0-video",
            "timeout_seconds": grok_conf.get("timeout", 180) or 180,
            "max_retries": grok_conf.get("max_retries", 2) or 2,
            "presets": video_presets,
        }
        self.grok_config = grok_conf  # 保存原始配置
        self.video_service = GrokVideoService(settings=video_settings)
        self.video_manager = VideoManager(video_settings, self.data_dir)
        self._video_lock = asyncio.Lock()
        self._video_in_progress: set[str] = set()

        # === v2.0.0: Gitee AI 文生图服务 ===
        gitee_conf = self.config.get("gitee_config", {}) or {}
        edit_conf = self.config.get("edit_config", {}) or {}
        self.edit_enabled = edit_conf.get("enabled", True)
        self.edit_presets = edit_conf.get("presets", []) or []
        self.edit_provider = edit_conf.get("provider", "gemini") or "gemini"
        # 各提供商改图模型（独立配置）
        self.edit_model_gitee = edit_conf.get("model", "Qwen-Image-Edit-2511") or "Qwen-Image-Edit-2511"
        self.edit_model_gemini = edit_conf.get("gemini_model", "") or ""
        self.edit_model_grok = edit_conf.get("grok_model", "") or ""
        self.gitee_draw = GiteeDrawService(
            data_dir=self.data_dir,
            api_keys=gitee_conf.get("api_keys", []) or [],
            base_url=gitee_conf.get("base_url", "https://ai.gitee.com/v1") or "https://ai.gitee.com/v1",
            model=gitee_conf.get("model", "z-image-turbo") or "z-image-turbo",
            default_size=gitee_conf.get("size", "1024x1024") or "1024x1024",
            num_inference_steps=gitee_conf.get("num_inference_steps", 9) or 9,
            negative_prompt=gitee_conf.get("negative_prompt", "") or "",
            timeout=gitee_conf.get("timeout", 300) or 300,
            max_retries=gitee_conf.get("max_retries", 2) or 2,
            proxy=self.config.get("proxy", "") or None,
            max_storage_mb=cache_conf.get("max_storage_mb", 500) or 500,
            max_count=cache_conf.get("max_count", 100) or 100,
            edit_model=edit_conf.get("model", "Qwen-Image-Edit-2511") or "Qwen-Image-Edit-2511",
            edit_poll_interval=edit_conf.get("poll_interval", 5) or 5,
            edit_poll_timeout=edit_conf.get("poll_timeout", 300) or 300,
        )

        # === v2.4.0: Gemini AI 文生图服务 ===
        gemini_conf = self.config.get("gemini_config", {}) or {}
        self.gemini_draw = GeminiDrawService(
            data_dir=self.data_dir,
            api_key=gemini_conf.get("api_key", "") or "",
            base_url=gemini_conf.get("base_url", "https://generativelanguage.googleapis.com") or "https://generativelanguage.googleapis.com",
            model=gemini_conf.get("model", "gemini-2.0-flash-exp-image-generation") or "gemini-2.0-flash-exp-image-generation",
            image_size=gemini_conf.get("image_size", "1K") or "1K",
            timeout=gemini_conf.get("timeout", 120) or 120,
            proxy=self.config.get("proxy", "") or None,
            max_storage_mb=cache_conf.get("max_storage_mb", 500) or 500,
            max_count=cache_conf.get("max_count", 100) or 100,
        )


        # 主备切换配置
        self.draw_provider = self.config.get("draw_provider", "gitee") or "gitee"
        self.enable_fallback = self.config.get("enable_fallback", True)
        # 备用模型顺序（用户自定义，不包含主模型）
        self.fallback_models = self.config.get("fallback_models", ["gemini", "grok"]) or ["gemini", "grok"]

        # === v3.1.0: 图片管理器（用于元数据存储）===
        self.image_manager = ImageManager(
            self.data_dir,
            proxy=self.config.get("proxy", "") or None,
            max_storage_mb=cache_conf.get("max_storage_mb", 500) or 500,
            max_count=cache_conf.get("max_count", 100) or 100,
        )

        # === v2.6.0: 人像参考配置 ===
        selfie_conf = self.config.get("selfie_config", {}) or {}
        self.selfie_enabled = selfie_conf.get("enabled", False)
        # 参考图缓存（目录 mtime 变化时自动失效）
        self._selfie_refs_cache: list[bytes] = []
        self._selfie_refs_cache_mtime: float = 0.0
        # 清理废弃的 reference_images 字段
        if "reference_images" in selfie_conf:
            del selfie_conf["reference_images"]
            self.config["selfie_config"] = selfie_conf

        # === v3.1.0: 改图功能配置 ===
        self._edit_http_session: aiohttp.ClientSession | None = None
        self._edit_session_lock = asyncio.Lock()

        # 清理废弃的顶级 video_presets 字段（已迁移到 grok_config 内）
        if "video_presets" in self.config:
            del self.config["video_presets"]

        # === v2.1.0: WebUI 服务器 ===
        self.web_server: WebServer | None = None
        self._webui_started = False
        webui_conf = self.config.get("webui_config", {}) or {}
        if webui_conf.get("enabled", False):
            self.web_server = WebServer(
                plugin=self,
                host=webui_conf.get("host", "127.0.0.1") or "127.0.0.1",
                port=webui_conf.get("port", 8088) or 8088,
                token=webui_conf.get("token", "") or "",
            )
            # 立即启动 WebUI（在事件循环中调度）
            try:
                loop = asyncio.get_running_loop()
                self._webui_started = True
                task = loop.create_task(self._start_webui())
                self._bg_tasks.add(task)
            except RuntimeError:
                # 没有运行中的事件循环，延迟到首次 LLM 请求时启动
                pass

    def _load_dynamic_config(self) -> dict:
        """从独立文件加载动态配置（环境和摄影模式）"""
        if self.dynamic_config_path.exists():
            try:
                with open(self.dynamic_config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"[Portrait] 加载动态配置失败: {e}，使用默认值")
        return {
            "environments": DEFAULT_ENVIRONMENTS,
            "cameras": DEFAULT_CAMERAS,
        }

    def _save_dynamic_config(self):
        """保存动态配置到独立文件"""
        try:
            with open(self.dynamic_config_path, "w", encoding="utf-8") as f:
                json.dump(self._dynamic_config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Portrait] 保存动态配置失败: {e}")

    def _load_persisted_config(self):
        """加载 WebUI 持久化的配置（仅填充 AstrBot 未设置的字段）"""
        if self.config_persist_path.exists():
            try:
                with open(self.config_persist_path, "r", encoding="utf-8") as f:
                    persisted = json.load(f)
                # 仅填充 AstrBot 配置中未设置或为空的字段
                # AstrBot 配置优先级高于 WebUI 持久化配置
                merged_keys = []
                for key, value in persisted.items():
                    # 跳过已废弃的字段
                    if key == "vision_model":
                        continue
                    # 只有当 AstrBot 配置中没有该字段或值为空时才使用持久化值
                    if key not in self.config or self.config.get(key) in (None, "", [], {}):
                        self.config[key] = value
                        merged_keys.append(key)
                if merged_keys:
                    logger.debug(f"[Portrait] 从持久化配置填充字段: {merged_keys}")
            except Exception as e:
                logger.warning(f"[Portrait] 加载持久化配置失败: {e}")

    def save_config_to_disk(self):
        """将当前配置持久化到磁盘"""
        # 需要持久化的字段（保存到 webui_config.json）
        persist_fields = {
            "char_identity",
            "injection_rounds",
            "cooldown_seconds",
            "enable_env_injection",
            "enable_camera_injection",
            "proxy",
            "gitee_config",
            "gemini_config",
            "grok_config",
            "cache_config",
            "draw_provider",
            "enable_fallback",
            "fallback_models",
            "selfie_config",
        }

        try:
            persist_data = {k: v for k, v in self.config.items() if k in persist_fields}
            with open(self.config_persist_path, "w", encoding="utf-8") as f:
                json.dump(persist_data, f, ensure_ascii=False, indent=2)

            # 同步到 AstrBot 配置文件
            astrbot_config_path = Path(self.data_dir).parent.parent / "config" / "astrbot_plugin_portrait_config.json"
            if astrbot_config_path.exists():
                try:
                    with open(astrbot_config_path, "r", encoding="utf-8-sig") as f:
                        astrbot_config = json.load(f)
                    # 更新需要同步的字段
                    for key in persist_fields:
                        if key in persist_data:
                            astrbot_config[key] = persist_data[key]
                    # 清理废弃字段（不应出现在 AstrBot 配置中）
                    deprecated_fields = ["video_presets", "environments", "cameras"]
                    for field in deprecated_fields:
                        if field in astrbot_config:
                            del astrbot_config[field]
                    with open(astrbot_config_path, "w", encoding="utf-8") as f:
                        json.dump(astrbot_config, f, ensure_ascii=False, indent=2)
                    logger.debug(f"[Portrait] 已同步配置到 AstrBot")
                except Exception as e:
                    logger.warning(f"[Portrait] 同步 AstrBot 配置失败: {e}")

            logger.info(f"[Portrait] 配置已持久化到磁盘")
        except Exception as e:
            logger.error(f"[Portrait] 持久化配置失败: {e}")

    async def _start_webui(self):
        """启动 WebUI 服务器"""
        if self.web_server:
            try:
                await self.web_server.start()
            except Exception as e:
                logger.error(f"[Portrait] WebUI 启动失败: {e}")
                self._webui_started = False  # 重置标志以允许重试
                raise

    async def _load_selfie_reference_images(self) -> list[bytes]:
        """加载人像参考照片 - 自动扫描 selfie_refs 目录（带 mtime 缓存）"""
        if not self.selfie_enabled:
            return []

        selfie_refs_dir = self.data_dir / "selfie_refs"
        if not selfie_refs_dir.exists():
            return []

        # 检查目录 mtime，如果未变化则返回缓存
        try:
            dir_mtime = selfie_refs_dir.stat().st_mtime
        except OSError:
            return []

        if self._selfie_refs_cache and dir_mtime == self._selfie_refs_cache_mtime:
            logger.debug(f"[Portrait] 使用缓存的 {len(self._selfie_refs_cache)} 张人像参考")
            return self._selfie_refs_cache

        allowed_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

        def _load_sync() -> list[bytes]:
            """同步加载逻辑，在线程池中执行"""
            images: list[bytes] = []
            for file_path in sorted(selfie_refs_dir.iterdir()):
                if file_path.is_file() and file_path.suffix.lower() in allowed_exts:
                    try:
                        images.append(file_path.read_bytes())
                    except Exception as e:
                        logger.warning(f"[Portrait] 读取参考照失败: {file_path.name}, {e}")
            return images

        images = await asyncio.to_thread(_load_sync)
        if images:
            logger.info(f"[Portrait] 已加载 {len(images)} 张人像参考")

        # 更新缓存
        self._selfie_refs_cache = images
        self._selfie_refs_cache_mtime = dir_mtime

        return images

    def get_dynamic_config(self) -> dict:
        """获取动态配置（环境和摄影模式列表）"""
        return {
            "environments": self._dynamic_config.get("environments", DEFAULT_ENVIRONMENTS),
            "cameras": self._dynamic_config.get("cameras", DEFAULT_CAMERAS),
        }

    def update_dynamic_config(self, new_config: dict):
        """更新动态配置并重建 Prompt"""
        if "environments" in new_config:
            self._dynamic_config["environments"] = new_config["environments"]
        if "cameras" in new_config:
            self._dynamic_config["cameras"] = new_config["cameras"]
        self._save_dynamic_config()
        self.rebuild_full_prompt()

    def rebuild_full_prompt(self):
        """重建完整 Prompt（热更新时调用）"""
        p_char_id = self.config.get("char_identity", "") or ""

        # 环境列表（根据开关决定是否生成）
        if self.enable_env_injection:
            environments = self._dynamic_config.get("environments", DEFAULT_ENVIRONMENTS)
            env_section_lines = ["## 3. 动态环境与风格 (Dynamic Environment & Style)"]
            env_section_lines.append("**逻辑判断 (Logic Branching):** Check user input for keywords.")

            for idx, env in enumerate(environments):
                name = env.get("name", f"Scene {idx}")
                keywords = env.get("keywords", [])
                prompt_content = env.get("prompt", "")

                if "default" in keywords:
                    trigger_desc = "**默认场景 (Default)**: 当未匹配到其他特定场景关键词时使用。"
                else:
                    kws_str = ", ".join([f'"{k}"' for k in keywords])
                    trigger_desc = f"**触发关键词**: {kws_str}"

                env_section_lines.append(f"\n* **Scenario: {name}**")
                env_section_lines.append(f"    * {trigger_desc}")
                env_section_lines.append(f"    * *Prompt Block:*")
                env_section_lines.append(f"    > **{prompt_content}**")

            section_env = "\n".join(env_section_lines)
        else:
            section_env = ""

        # 镜头列表（根据开关决定是否生成）
        if self.enable_camera_injection:
            cameras = self._dynamic_config.get("cameras", DEFAULT_CAMERAS)
            cam_section_lines = ["## 4. 摄影模式切换 (Photo Format Switching)"]
            cam_section_lines.append("**指令:** 仅检查**用户发送的消息文本**中的关键词来决定摄影模式。")
            cam_section_lines.append("**注意:** 日程参考中的\"穿搭\"描述是用于生成服装内容的，不是摄影模式触发词。只有用户消息中明确出现\"全身\"、\"OOTD\"等词才切换全身模式。")
            cam_section_lines.append("**默认规则:** 如果用户消息中没有明确的触发词，必须使用**半身/默认模式**。")

            for idx, cam in enumerate(cameras):
                name = cam.get("name", f"Mode {idx}")
                keywords = cam.get("keywords", [])
                prompt_content = cam.get("prompt", "")

                if "default" in keywords:
                    trigger_desc = "触发: **默认模式** (当无其他匹配时)。"
                else:
                    kws_str = ", ".join([f'"{k}"' for k in keywords])
                    trigger_desc = f"触发 (必须出现在当前句中): {kws_str}"

                cam_section_lines.append(f"\n* **模式: {name}**")
                cam_section_lines.append(f"    * {trigger_desc}")
                cam_section_lines.append(f"    * *Camera Params:* `{prompt_content}`")

            section_camera = "\n".join(cam_section_lines)
        else:
            section_camera = ""

        # 组装完整 Prompt
        prompt_parts = [
            TPL_HEADER,
            TPL_CHAR.format(content=p_char_id),
            TPL_MIDDLE,
        ]
        if section_env:
            prompt_parts.append(section_env)
        if section_camera:
            prompt_parts.append(section_camera)
        prompt_parts.append(TPL_FOOTER)
        prompt_parts.append("--- END CONTEXT DATA ---")

        self.full_prompt = "\n\n".join(prompt_parts)
        logger.debug("[Portrait] Prompt 已重建")

    async def terminate(self):
        """插件卸载/重载时的清理逻辑"""
        self._is_terminated = True
        try:
            # 停止 WebUI 服务器
            if self.web_server:
                await self.web_server.stop()
            # 取消所有后台任务
            for task in self._bg_tasks:
                if not task.done():
                    task.cancel()
            # 清理会话缓存
            self.injection_counter.clear()
            self.injection_last_active.clear()
            # 关闭 Gitee 服务
            await self.gitee_draw.close()
            # 关闭 Gemini 服务
            await self.gemini_draw.close()
            # 关闭 Grok 图片服务
            if self.grok_draw:
                await self.grok_draw.close()
            # 关闭 Grok 视频服务
            if self.video_service:
                await self.video_service.close()
            # 关闭改图 HTTP session
            if self._edit_http_session and not self._edit_http_session.closed:
                await self._edit_http_session.close()
                self._edit_http_session = None
            logger.info("[Portrait] 插件已停止，清理资源完成")
        except Exception as e:
            logger.error(f"[Portrait] 停止插件出错: {e}")

    # ==================== 公共 API（供其他插件调用）====================

    async def generate_image_api(
        self,
        prompt: str,
        provider: str | None = None,
        size: str | None = None,
        reference_images: list[bytes] | None = None,
    ) -> tuple[str, str] | None:
        """公共 API：供其他插件调用的文生图接口

        Args:
            prompt: 生图提示词
            provider: 提供商（gitee/gemini/grok），默认使用配置的 draw_provider
            size: 图片尺寸（如 1024x1024 或 1K/2K/4K），默认使用配置值
            reference_images: 可选的参考图字节列表（用于 Gemini/Grok 人像参考）

        Returns:
            (mime_type, base64_data) 或 None（生成失败）

        Example:
            # 在其他插件中调用
            for star in context.get_all_stars():
                if star.name == "astrbot_plugin_portrait":
                    result = await star.star_instance.generate_image_api(
                        prompt="a cute cat",
                        provider="gemini",
                        size="1K",
                    )
                    if result:
                        mime, b64 = result
                        # 使用图片...
        """
        if self._is_terminated:
            return None

        try:
            # 临时切换提供商（如果指定）
            original_provider = self.draw_provider
            if provider and provider in ("gitee", "gemini", "grok"):
                self.draw_provider = provider

            try:
                # 调用内部生图方法
                image_path = await self._generate_image(
                    prompt=prompt,
                    size=size,
                    images=reference_images,
                    is_character_related=False,  # API 调用默认不加载自拍参考
                )

                if image_path and image_path.exists():
                    # 读取图片并返回 base64
                    image_bytes = await asyncio.to_thread(image_path.read_bytes)
                    # 根据后缀判断 MIME 类型
                    suffix = image_path.suffix.lower()
                    mime_map = {
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".png": "image/png",
                        ".gif": "image/gif",
                        ".webp": "image/webp",
                    }
                    mime = mime_map.get(suffix, "image/png")
                    b64 = base64.b64encode(image_bytes).decode("utf-8")
                    return (mime, b64)

                return None
            finally:
                # 恢复原始提供商
                self.draw_provider = original_provider

        except Exception as e:
            logger.error(f"[Portrait] API 生图失败: {e}")
            return None

    async def edit_image_api(
        self,
        prompt: str,
        image_bytes: bytes,
        provider: str | None = None,
    ) -> tuple[str, str] | None:
        """公共 API：供其他插件调用的改图接口

        Args:
            prompt: 改图提示词
            image_bytes: 原始图片字节数据
            provider: 提供商（gitee/gemini/grok），默认使用配置的 edit_provider

        Returns:
            (mime_type, base64_data) 或 None（生成失败）
        """
        if self._is_terminated:
            return None

        if not self.edit_enabled:
            logger.warning("[Portrait] 改图功能未启用")
            return None

        try:
            target_provider = provider or self.edit_provider

            if target_provider == "gemini":
                # Gemini 改图
                model = self.edit_model_gemini or self.gemini_draw.model
                result_path = await self.gemini_draw.edit_image(
                    prompt=prompt,
                    image_bytes=image_bytes,
                    model=model,
                )
            elif target_provider == "grok":
                # Grok 改图
                model = self.edit_model_grok or self.grok_draw.model
                result_path = await self.grok_draw.edit_image(
                    prompt=prompt,
                    image_bytes=image_bytes,
                    model=model,
                )
            else:
                # Gitee 改图
                result_path = await self.gitee_draw.edit_image(
                    prompt=prompt,
                    image_bytes=image_bytes,
                    model=self.edit_model_gitee,
                )

            if result_path and result_path.exists():
                img_bytes = await asyncio.to_thread(result_path.read_bytes)
                suffix = result_path.suffix.lower()
                mime_map = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }
                mime = mime_map.get(suffix, "image/png")
                b64 = base64.b64encode(img_bytes).decode("utf-8")
                return (mime, b64)

            return None

        except Exception as e:
            logger.error(f"[Portrait] API 改图失败: {e}")
            return None

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        # 生命周期检查：防止旧实例继续工作
        if self._is_terminated:
            return

        # 调试：记录钩子调用
        logger.debug(f"[Portrait] on_llm_request 钩子被调用，当前 system_prompt 长度: {len(req.system_prompt) if req.system_prompt else 0}")

        # 延迟启动 WebUI（首次 LLM 请求时，此时事件循环已在运行）
        if self.web_server and not self._webui_started:
            self._webui_started = True
            task = asyncio.create_task(self._start_webui())
            self._bg_tasks.add(task)

        # v1.6.0: One-Shot 单次注入策略
        # 仅在检测到绘图意图时注入 Visual Context

        # 获取用户消息内容 - 优先使用原始消息，避免被其他插件修改
        user_message = ""
        extract_source = ""

        # 方式1 (优先): 从 event.message_str 获取（用户原始消息，未被其他插件修改）
        if hasattr(event, 'message_str') and event.message_str:
            user_message = event.message_str
            extract_source = "message_str"

        # 方式2: 从 event.message 获取
        if not user_message and hasattr(event, 'message') and event.message:
            if hasattr(event.message, 'message'):
                parts = []
                for seg in event.message.message:
                    if hasattr(seg, 'text'):
                        parts.append(seg.text)
                    elif hasattr(seg, 'data') and isinstance(seg.data, dict):
                        parts.append(seg.data.get('text', ''))
                user_message = ''.join(parts)
                if user_message:
                    extract_source = "event.message.message"
            # 尝试直接获取 raw_message
            if not user_message and hasattr(event.message, 'raw_message'):
                user_message = event.message.raw_message or ""
                if user_message:
                    extract_source = "raw_message"

        # 方式3 (备选): 从 req.prompt 获取（可能被记忆插件等修改过）
        if not user_message and hasattr(req, 'prompt') and req.prompt:
            if isinstance(req.prompt, str):
                user_message = req.prompt
                extract_source = "req.prompt (str)"
            elif isinstance(req.prompt, list):
                # 如果是消息列表，提取最后一条用户消息
                for msg in reversed(req.prompt):
                    if isinstance(msg, dict) and msg.get('role') == 'user':
                        content = msg.get('content', '')
                        if isinstance(content, str):
                            user_message = content
                            extract_source = "req.prompt (list)"
                        break

        # 方式4 (最后备选): 从 req.messages 获取最后一条用户消息
        if not user_message and hasattr(req, 'messages') and req.messages:
            for msg in reversed(req.messages):
                if hasattr(msg, 'role') and msg.role == 'user':
                    if hasattr(msg, 'content'):
                        user_message = str(msg.content) if msg.content else ""
                        extract_source = "req.messages"
                    break

        if user_message:
            logger.debug(f"[Portrait] 消息提取成功 (来源: {extract_source}): {user_message[:50]}...")
        else:
            logger.debug("[Portrait] 消息提取失败: 所有方式均未获取到用户消息")

        # === v2.9.0: 防止工具调用响应重复触发注入 ===
        # 检查消息历史中是否已有工具调用记录（表示正在处理工具调用后的响应）
        is_tool_response = False
        if hasattr(req, 'messages') and req.messages:
            # 检查最近几条消息是否有工具调用
            for msg in reversed(list(req.messages)[-5:]):  # 只检查最近5条消息
                if hasattr(msg, 'role') and msg.role == 'tool':
                    is_tool_response = True
                    break
                # 检查 assistant 消息中是否包含工具调用
                if hasattr(msg, 'role') and msg.role == 'assistant':
                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        is_tool_response = True
                        break

        if is_tool_response:
            logger.debug("[Portrait] 检测到工具调用响应，跳过注入防止循环")
            return

        # === v2.9.6: 排除插件指令，避免干扰 ===
        user_msg_stripped = user_message.strip()

        # 排除以 / 或 . 开头的指令
        if user_msg_stripped.startswith('/') or user_msg_stripped.startswith('.'):
            logger.debug(f"[Portrait] 检测到插件指令，跳过注入")
            return

        # 动态获取 banana_sign 预设词（避免硬编码）
        banana_prefixes = self._get_banana_sign_prefixes()
        cmd = user_msg_stripped.split()[0] if user_msg_stripped else ""
        if cmd in banana_prefixes:
            logger.debug(f"[Portrait] 检测到 banana_sign 命令 '{cmd}'，跳过注入")
            return

        # 正则匹配检测绘图意图
        if not user_message or not self.trigger_regex.search(user_message):
            logger.debug(f"[Portrait] 未检测到绘图意图，跳过注入")
            return

        # === v2.9.2: 前置角色相关性判断，非角色内容不注入 ===
        # === v2.9.8: 传入上下文消息用于回应性对话检测 ===
        context_messages = list(req.messages) if hasattr(req, 'messages') and req.messages else None
        if not self._is_character_related_prompt(user_message, context_messages):
            logger.info(f"[Portrait] 用户消息非角色相关，跳过注入: {user_message[:50]}...")
            return

        # === v1.8.1: 多轮次注入逻辑 ===
        # 修复：使用 群ID + 用户ID 作为 session key，避免群内用户互相污染
        group_id = event.unified_msg_origin or "default"
        user_id = str(event.get_sender_id()) if hasattr(event, 'get_sender_id') else "unknown"
        session_id = f"{group_id}:{user_id}"
        current_time = datetime.now().timestamp()

        # 清理过期会话（间隔触发，减少每次请求的开销）
        if current_time - self._last_session_cleanup_ts >= self._session_cleanup_interval:
            expired_sessions = [
                sid for sid, last_active in self.injection_last_active.items()
                if current_time - last_active > self.session_ttl
            ]
            for sid in expired_sessions:
                self.injection_counter.pop(sid, None)
                self.injection_last_active.pop(sid, None)
            if expired_sessions:
                logger.debug(f"[Portrait] 已清理 {len(expired_sessions)} 个过期会话")
            self._last_session_cleanup_ts = current_time

        # 更新当前会话的活跃时间
        self.injection_last_active[session_id] = current_time

        # === v2.9.0: 修复重复注入问题 - 只在计数已耗尽时才重置 ===
        # 注：此处已确认匹配到触发词（660行已检测），仅在新会话或计数耗尽时重置
        current_count = self.injection_counter.get(session_id, 0)
        # 只有当计数为 0 或会话不存在时才重新初始化
        if current_count <= 0:
            self.injection_counter[session_id] = self.injection_rounds
            logger.info(f"[Portrait] 检测到新的绘图请求，初始化注入轮次: {self.injection_rounds}")
        else:
            logger.debug(f"[Portrait] 会话 {session_id} 仍有 {current_count} 轮注入，继续使用")

        # 检查是否还有剩余注入次数
        remaining = self.injection_counter.get(session_id, 0)
        if remaining <= 0:
            # === v2.2.0: 注入轮次用尽后清理历史记忆中的 portrait 注入内容 ===
            self._clean_portrait_injection(req)
            logger.debug(f"[Portrait] 会话 {session_id} 注入次数已用尽，已清理历史注入内容")
            return

        # === v3.2.0: 解析日程信息，融入注入内容 ===
        schedule_hint = ""
        character_state = self._extract_character_state(req)
        if character_state:
            schedule_info = self._parse_schedule_from_state(character_state)
            if schedule_info:
                schedule_hint = f"""
## 5. 当前日程参考 (Current Schedule Reference)
**当前时间点**: {schedule_info['time']}
**角色状态**: {schedule_info['content']}
**穿搭参考**: {schedule_info['outfit'][:200] if schedule_info['outfit'] else '参考上方穿搭设定'}

**重要**: 生成图片时，请参考上述日程状态来决定场景、表情和动作。例如：
- 如果日程是"被窝里好暖和"，场景应该是床上/卧室
- 如果日程是"奶茶店排队"，场景应该是户外/奶茶店
- 根据日程内容推断角色的情绪和姿态
"""
            else:
                logger.debug("[Portrait] 未解析到日程条目，使用默认注入")
        else:
            logger.debug("[Portrait] 未检测到 <character_state>，跳过日程解析")

        # 执行注入并减少计数
        full_injection_content = self.full_prompt + schedule_hint
        injection = f"\n\n<portrait_status>\n{full_injection_content}\n</portrait_status>"
        if not req.system_prompt:
            req.system_prompt = ""

        # 先清理已存在的 portrait_status 块，防止重复注入
        self._clean_portrait_injection(req)

        original_len = len(req.system_prompt)
        req.system_prompt += injection

        # 调试：记录注入的 prompt 长度
        logger.info(f"[Portrait] 注入内容长度: {len(injection)} 字符")
        logger.info(f"[Portrait] system_prompt 长度: 注入前 {original_len} → 注入后 {len(req.system_prompt)}")

        self.injection_counter[session_id] -= 1
        remaining_after = self.injection_counter[session_id]

        # 如果这是最后一轮注入，标记需要在下次请求时清理
        if remaining_after <= 0:
            logger.info(f"[Portrait] Visual Context 已注入 (最后一轮 {self.injection_rounds}/{self.injection_rounds}) - 下次请求将清理历史注入")
        else:
            logger.info(f"[Portrait] Visual Context 已注入 (轮次 {self.injection_rounds - remaining_after}/{self.injection_rounds}) - 触发词: {user_message[:30]}...")

    def _clean_portrait_injection(self, req: ProviderRequest):
        """清理请求中的 portrait 注入内容，防止污染上下文"""
        # 清理 system_prompt
        if req.system_prompt:
            has_portrait = '<portrait_status>' in req.system_prompt
            logger.debug(f"[Portrait] 清理检查: system_prompt 长度={len(req.system_prompt)}, 包含portrait_status={has_portrait}")
            cleaned = self._portrait_status_pattern.sub('', req.system_prompt)
            if cleaned != req.system_prompt:
                removed_len = len(req.system_prompt) - len(cleaned)
                req.system_prompt = cleaned
                logger.info(f"[Portrait] 已从 system_prompt 清理注入内容，移除 {removed_len} 字符")

        # 清理 messages 中的历史消息
        if hasattr(req, 'messages') and req.messages:
            for msg in req.messages:
                if hasattr(msg, 'content') and isinstance(msg.content, str):
                    cleaned = self._portrait_status_pattern.sub('', msg.content)
                    if cleaned != msg.content:
                        msg.content = cleaned
                        logger.debug(f"[Portrait] 已从 {msg.role} 消息清理注入内容")

        # 清理 prompt (如果是字符串)
        if hasattr(req, 'prompt') and isinstance(req.prompt, str):
            cleaned = self._portrait_status_pattern.sub('', req.prompt)
            if cleaned != req.prompt:
                req.prompt = cleaned
                logger.debug("[Portrait] 已从 prompt 清理注入内容")

    def _extract_character_state(self, req: ProviderRequest) -> str | None:
        """从请求中提取 <character_state> 块内容"""
        # 优先从 system_prompt 提取
        if req.system_prompt:
            match = self._character_state_pattern.search(req.system_prompt)
            if match:
                return match.group(1).strip()

        # 其次从 messages 提取
        if hasattr(req, 'messages') and req.messages:
            for msg in req.messages:
                if hasattr(msg, 'content') and isinstance(msg.content, str):
                    match = self._character_state_pattern.search(msg.content)
                    if match:
                        return match.group(1).strip()

        return None

    def _parse_schedule_from_state(self, state_content: str) -> dict | None:
        """从 <character_state> 内容中解析当前时间对应的日程

        Returns:
            包含 time, content, outfit 的字典，或 None
        """
        if not state_content:
            return None

        # 提取穿着信息
        outfit = ""
        outfit_match = self._state_outfit_pattern.search(state_content)
        if outfit_match:
            outfit = outfit_match.group(1).strip()

        # 提取日程部分
        schedule_match = self._state_schedule_pattern.search(state_content)
        if not schedule_match:
            return None

        schedule_text = schedule_match.group(1).strip()

        # 解析各个时间点 (格式: HH:MM 内容)
        entries = self._schedule_time_pattern.findall(schedule_text)

        if not entries:
            return None

        # 获取当前时间
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute

        # 找到最近的时间点（可以是过去或即将到来的，优先匹配最近的）
        best_entry = None
        best_diff = float('inf')

        for time_str, content in entries:
            try:
                parts = time_str.split(':')
                entry_hour = int(parts[0])
                entry_minute = int(parts[1])
                entry_minutes = entry_hour * 60 + entry_minute

                # 计算时间差的绝对值（匹配最近的时间点，无论过去还是未来）
                diff = abs(current_minutes - entry_minutes)
                if diff < best_diff:
                    best_diff = diff
                    best_entry = {
                        'time': time_str,
                        'content': content.strip(),
                        'outfit': outfit,
                    }
            except (ValueError, IndexError):
                continue

        # 兜底：如果没有匹配到任何条目，取第一个条目
        if best_entry is None and entries:
            first_time, first_content = entries[0]
            best_entry = {
                'time': first_time,
                'content': first_content.strip(),
                'outfit': outfit,
            }

        if best_entry:
            logger.info(f"[Portrait] 日程匹配: {best_entry['time']} - {best_entry['content'][:30]}...")

        return best_entry

    def _get_banana_sign_prefixes(self) -> set[str]:
        """动态获取 banana_sign 插件的预设词列表（带 TTL 缓存）"""
        current_time = time.time()

        # 检查缓存是否有效
        if (self._banana_prefixes_cache is not None
            and current_time - self._banana_prefixes_cache_time < self._banana_prefixes_cache_ttl):
            return self._banana_prefixes_cache

        prefixes = set()

        # 固定的命令（不在配置文件中的）
        fixed_commands = {
            'cp生图', 'cp改图', '画图', '生图', 'cp画图', '改图',
        }
        prefixes.update(fixed_commands)

        # 尝试读取 banana_sign 配置文件获取预设词
        try:
            config_path = self.data_dir.parent.parent / "config" / "astrbot_plugin_banana_sign_config.json"
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8-sig') as f:
                    config = json.load(f)
                prompt_list = config.get("prompt", [])
                for prompt in prompt_list:
                    if not prompt:
                        continue
                    # 提取触发词（第一个单词或 [触发词1,触发词2] 格式）
                    prompt = prompt.strip()
                    if prompt.startswith('['):
                        # [触发词1,触发词2] 格式
                        end = prompt.find(']')
                        if end > 0:
                            triggers = prompt[1:end].split(',')
                            for t in triggers:
                                prefixes.add(t.strip())
                    else:
                        # 普通格式：第一个空格前的内容
                        first_word = prompt.split()[0] if prompt.split() else ""
                        if first_word:
                            prefixes.add(first_word)
        except Exception as e:
            logger.debug(f"[Portrait] 读取 banana_sign 配置失败: {e}")

        # 更新缓存
        self._banana_prefixes_cache = prefixes
        self._banana_prefixes_cache_time = current_time

        return prefixes

    def _is_global_admin(self, event: AstrMessageEvent) -> bool:
        """检查发送者是否为全局管理员"""
        admin_ids = self.context.get_config().get("admins_id", [])
        sender_id = str(event.get_sender_id())
        # 统一转为字符串比较，过滤空值
        return sender_id in [str(aid) for aid in admin_ids if aid]

    def _check_cooldown(self, event: AstrMessageEvent) -> tuple[bool, int]:
        """检查用户是否在冷却中

        Returns:
            (is_allowed, remaining_seconds): 是否允许使用，剩余冷却秒数
        """
        # 无冷却时间限制
        if self.cooldown_seconds <= 0:
            return True, 0

        # 管理员不受冷却限制
        if self._is_global_admin(event):
            return True, 0

        user_id = str(event.get_sender_id())
        now = time.time()

        # 检查用户上次使用时间
        if user_id in self.user_last_use:
            elapsed = now - self.user_last_use[user_id]
            if elapsed < self.cooldown_seconds:
                remaining = int(self.cooldown_seconds - elapsed)
                return False, remaining

        return True, 0

    def _update_cooldown(self, event: AstrMessageEvent):
        """更新用户的冷却时间"""
        user_id = str(event.get_sender_id())
        self.user_last_use[user_id] = time.time()

        # 清理过期记录（超过冷却时间2倍的记录）
        if len(self.user_last_use) > 1000:
            now = time.time()
            threshold = self.cooldown_seconds * 2
            self.user_last_use = {
                k: v for k, v in self.user_last_use.items()
                if now - v < threshold
            }

    async def _extract_first_image_bytes_from_event(self, event: AstrMessageEvent) -> bytes | None:
        """从消息或引用消息中提取第一张图片并转换为 bytes。"""
        for seg in event.get_messages():
            if isinstance(seg, Comp.Reply) and getattr(seg, "chain", None):
                for quote_seg in seg.chain:
                    if isinstance(quote_seg, Comp.Image):
                        try:
                            b64 = await quote_seg.convert_to_base64()
                            return base64.b64decode(b64)
                        except Exception as e:
                            logger.warning(f"[Portrait][视频] 引用图片转换失败: {e}")

        for seg in event.get_messages():
            if isinstance(seg, Comp.Image):
                try:
                    b64 = await seg.convert_to_base64()
                    return base64.b64decode(b64)
                except Exception as e:
                    logger.warning(f"[Portrait][视频] 当前消息图片转换失败: {e}")

        return None

    async def _video_begin(self, user_id: str) -> bool:
        """单用户并发保护：成功占用返回 True，否则 False。"""
        uid = str(user_id or "")
        async with self._video_lock:
            if uid in self._video_in_progress:
                return False
            self._video_in_progress.add(uid)
            return True

    async def _video_end(self, user_id: str) -> None:
        uid = str(user_id or "")
        async with self._video_lock:
            self._video_in_progress.discard(uid)

    def _parse_video_args(self, text: str) -> tuple[str | None, str]:
        """解析 /视频 参数，返回 (preset, prompt)。"""
        message = (text or "").strip()
        if not message:
            return None, ""

        first, _, rest = message.partition(" ")
        presets = self.video_service.get_preset_names()
        if first and first in presets:
            return first, rest.strip()
        return None, message

    async def _send_video_result(self, event: AstrMessageEvent, video_url: str, prompt: str = "") -> None:
        """发送视频结果：URL / 本地文件 / 文本链接兜底。同时保存URL到画廊。"""
        mode = str(self.grok_config.get("video_send_mode", "auto")).strip().lower()
        if mode not in {"auto", "url", "file"}:
            mode = "auto"

        # 保存视频URL到元数据（用于画廊在线播放）
        try:
            self.video_manager.save_video_url(video_url, prompt=prompt)
        except Exception as e:
            logger.warning(f"[Portrait][视频] 保存视频URL失败: {e}")

        if mode in {"auto", "url"}:
            try:
                await event.send(event.chain_result([Video.fromURL(video_url)]))
                return
            except Exception as e:
                if mode == "url":
                    raise
                logger.warning(f"[Portrait][视频] URL 发送失败，尝试文件发送: {e}")

        if mode in {"auto", "file"}:
            try:
                timeout_seconds = int(self.grok_config.get("timeout", 180) or 180)
                video_path = await self.video_manager.download_video(
                    video_url,
                    timeout_seconds=timeout_seconds,
                )
                await event.send(event.chain_result([Video.fromFileSystem(str(video_path))]))
                return
            except Exception as e:
                if mode == "file":
                    raise
                logger.warning(f"[Portrait][视频] 文件发送失败，回退文本链接: {e}")

        await event.send(event.plain_result(f"视频生成成功：{video_url}"))

    @filter.command("视频")
    async def generate_video_command(self, event: AstrMessageEvent):
        """参考图生视频：/视频 <提示词> 或 /视频 <预设名> [额外提示词]"""
        event.should_call_llm(True)

        if not bool(self.grok_config.get("video_enabled", False)):
            yield event.plain_result("视频功能未启用，请在 grok_config.video_enabled 中开启")
            return

        # 冷却时间检查
        is_allowed, remaining = self._check_cooldown(event)
        if not is_allowed:
            yield event.plain_result(f"操作太频繁，请 {remaining} 秒后再试")
            return

        raw_msg = (event.message_str or "").strip()
        # 直接匹配 "视频" 后面的提示词
        match = self._video_cmd_pattern.search(raw_msg)
        arg = match.group(1).strip() if match else ""
        if not arg:
            yield event.plain_result("用法: /视频 <提示词> 或 /视频 <预设名> [额外提示词]\n请附带图片或引用一张图片")
            return

        preset, prompt = self._parse_video_args(arg)
        final_prompt = self.video_service.build_prompt(prompt, preset=preset)
        if not final_prompt:
            yield event.plain_result("提示词不能为空")
            return

        user_id = str(event.get_sender_id() or "")
        if not await self._video_begin(user_id):
            yield event.plain_result("你已有一个视频任务正在进行中，请稍后再试")
            return

        try:
            image_bytes = await self._extract_first_image_bytes_from_event(event)
            if not image_bytes:
                yield event.plain_result("请附带一张图片，或引用包含图片的消息后再使用 /视频")
                return

            yield event.plain_result("🎬 正在生成视频，请稍候...")

            video_url = await self.video_service.generate_video_url(
                prompt=prompt,
                image_bytes=image_bytes,
                preset=preset,
            )
            await self._send_video_result(event, video_url, prompt=final_prompt)

            # 更新冷却时间
            self._update_cooldown(event)
        except Exception as e:
            logger.error(f"[Portrait][视频] 生成失败: {e}", exc_info=True)
            yield event.plain_result(f"视频生成失败: {e}")
        finally:
            await self._video_end(user_id)

    # === v3.1.0: 改图命令 ===

    @filter.command("改图")
    async def edit_image_cmd(self, event: AstrMessageEvent, prompt: str = ""):
        """改图命令：发送/引用图片 + /改图 <提示词>

        用法:
        - 发送图片 + /改图 <提示词>
        - 引用图片消息 + /改图 <提示词>
        """
        # 阻止 LLM 调用但允许命令响应
        event.should_call_llm(False)

        # 功能开关检查
        if not self.edit_enabled:
            yield event.plain_result("改图功能未启用，请在配置中开启")
            return

        # 冷却检查
        is_allowed, remaining = self._check_cooldown(event)
        if not is_allowed:
            yield event.plain_result(f"操作太频繁，请 {remaining} 秒后再试")
            return

        # 获取图片
        images = await self._get_images_from_event(event, include_avatar=False)
        if not images:
            yield event.plain_result(
                "请发送或引用一张图片\n"
                "用法: 发送图片 + /改图 <提示词>\n"
                "或: 引用图片消息 + /改图 <提示词>"
            )
            return

        # 提示词处理
        if not prompt.strip():
            prompt = "优化这张图片"

        yield event.plain_result("正在处理改图请求...")

        try:
            t_start = time.perf_counter()
            image_path = await self._edit_image(prompt, images)
            t_end = time.perf_counter()

            logger.info(f"[Portrait] 改图完成: prompt={prompt[:30]}..., 耗时={t_end - t_start:.2f}s")

            # 更新冷却
            self._update_cooldown(event)

            # 发送结果
            try:
                await event.send(
                    event.chain_result([Comp.Image.fromFileSystem(str(image_path))])
                )
            except Exception as e:
                logger.warning(f"[Portrait] 发送改图结果失败: {e}，尝试 base64 方式")
                image_bytes = await asyncio.to_thread(image_path.read_bytes)
                image_b64 = base64.b64encode(image_bytes).decode("utf-8")
                await event.send(
                    event.chain_result([Comp.Image.fromBase64(image_b64)])
                )

        except Exception as e:
            logger.error(f"[Portrait] 改图失败: {e}")
            yield event.plain_result(f"改图失败: {str(e)}")

    def _is_character_related_prompt(self, text: str, context_messages: list | None = None) -> bool:
        """判断文本是否与角色本人相关

        用于两个场景：
        1. 注入判断：检查用户消息是否需要注入 Visual Context
        2. 生成判断：检查 prompt 是否需要使用参考图和角色外貌

        策略：
        1. 当前消息明确匹配角色关键词 -> 注入
        2. 当前消息含对话回应词 + 上下文有角色内容 -> 注入
        3. 默认不注入
        """
        # 使用预编译正则匹配（性能优化）
        match = self._char_keyword_regex.search(text)
        if match:
            logger.debug(f"[Portrait] 检测到角色关键词 '{match.group()}'")
            return True

        # === 上下文检测：当前消息是回应性对话时，检查上下文是否与角色相关 ===
        if self._response_regex.search(text) and context_messages:
            # 检查最近 3 条助手消息
            assistant_messages = [
                msg for msg in context_messages[-6:]
                if hasattr(msg, 'role') and msg.role == 'assistant'
            ][-3:]

            for msg in reversed(assistant_messages):
                content = getattr(msg, 'content', '') or ''
                if isinstance(content, str) and self._context_regex.search(content):
                    logger.info(f"[Portrait] 上下文检测：用户回应 + 角色活动上下文，执行注入")
                    return True

        # 默认不注入
        logger.debug("[Portrait] 未匹配角色关键词，跳过注入")
        return False

    # === v3.1.0: 改图功能辅助方法 ===

    async def _get_edit_session(self) -> aiohttp.ClientSession:
        """获取或创建改图用的 HTTP Session (线程安全)"""
        if self._edit_http_session is None or self._edit_http_session.closed:
            async with self._edit_session_lock:
                if self._edit_http_session is None or self._edit_http_session.closed:
                    timeout = aiohttp.ClientTimeout(total=60, connect=15)
                    connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
                    self._edit_http_session = aiohttp.ClientSession(
                        timeout=timeout,
                        connector=connector,
                    )
        return self._edit_http_session

    async def _download_image_bytes(self, url: str, retries: int = 3) -> bytes | None:
        """下载图片，带重试机制和指数退避"""
        session = await self._get_edit_session()
        proxy = self.config.get("proxy", "") or None
        max_size = 20 * 1024 * 1024
        backoff = 0.5
        last_error: Exception | None = None

        for i in range(retries):
            try:
                async with session.get(url, proxy=proxy) as resp:
                    if resp.status == 200:
                        content_length = resp.headers.get("Content-Length")
                        if content_length and int(content_length) > max_size:
                            logger.warning(f"[Portrait] 下载图片过大: {url[:60]}...")
                            return None
                        return await resp.read()
                    last_error = RuntimeError(f"HTTP {resp.status}")
                    logger.warning(f"[Portrait] 下载图片 HTTP {resp.status}: {url[:60]}...")
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                last_error = e
                logger.warning(f"[Portrait] 下载图片网络异常 (第{i + 1}次): {url[:60]}..., {e}")
            except Exception as e:
                last_error = e
                logger.warning(f"[Portrait] 下载图片失败 (第{i + 1}次): {url[:60]}..., 错误: {e}")
            if i < retries - 1:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 3.0)
        if last_error:
            logger.error(f"[Portrait] 下载图片最终失败: {url[:60]}..., 错误: {last_error}")
        return None

    async def _get_images_from_event(
        self,
        event: AstrMessageEvent,
        include_avatar: bool = False,
    ) -> list[bytes]:
        """从消息事件中提取图片字节列表

        图片来源：
        1. 回复/引用消息中的图片
        2. 当前消息中的图片

        Args:
            event: 消息事件
            include_avatar: 是否包含头像作为兜底

        Returns:
            图片字节列表
        """
        image_bytes_list: list[bytes] = []
        chain = event.get_messages()

        # 1. 回复链中的图片
        for seg in chain:
            if isinstance(seg, Reply) and seg.chain:
                for chain_item in seg.chain:
                    if isinstance(chain_item, Image):
                        img_bytes = await self._image_to_bytes(chain_item)
                        if img_bytes:
                            image_bytes_list.append(img_bytes)
                            logger.debug("[Portrait] 从回复中获取图片")

        # 2. 当前消息中的图片
        for seg in chain:
            if isinstance(seg, Image):
                img_bytes = await self._image_to_bytes(seg)
                if img_bytes:
                    image_bytes_list.append(img_bytes)
                    logger.debug(f"[Portrait] 从当前消息获取图片")

        logger.debug(f"[Portrait] 获取到 {len(image_bytes_list)} 张图片")
        return image_bytes_list

    async def _image_to_bytes(self, image: Image) -> bytes | None:
        """将 Image 组件转换为字节"""
        try:
            # 优先使用 URL
            if hasattr(image, 'url') and image.url:
                return await self._download_image_bytes(image.url)
            # 其次使用 base64
            if hasattr(image, 'file') and image.file:
                file_str = str(image.file)
                if file_str.startswith('base64://'):
                    return base64.b64decode(file_str[9:])
            return None
        except Exception as e:
            logger.warning(f"[Portrait] 图片转换失败: {e}")
            return None

    async def _edit_image(
        self,
        prompt: str,
        images: list[bytes],
    ) -> Path:
        """统一改图方法，支持主备切换

        Args:
            prompt: 改图提示词
            images: 原图字节列表

        Returns:
            生成的图片路径
        """
        if not images:
            raise ValueError("至少需要一张图片")

        # 确定提供商顺序：使用独立的 edit_provider 配置
        providers = {
            "gitee": (self.gitee_draw, "Gitee"),
            "gemini": (self.gemini_draw, "Gemini"),
            "grok": (self.grok_draw, "Grok"),
        }

        # 主提供商使用配置的 edit_provider（独立于 draw_provider）
        primary_key = self.edit_provider if self.edit_provider in providers else "gemini"
        primary, primary_name = providers[primary_key]

        # 获取改图专用模型配置
        def get_edit_model(provider_key: str, service) -> str:
            """获取提供商的改图模型，优先使用独立配置，否则回退到文生图模型"""
            if provider_key == "gitee":
                return self.edit_model_gitee or (service.edit_model if hasattr(service, 'edit_model') else service.model)
            elif provider_key == "gemini":
                return self.edit_model_gemini or service.model
            elif provider_key == "grok":
                return self.edit_model_grok or (service.image_model if hasattr(service, 'image_model') else service.model)
            return service.model

        # 备用提供商顺序（排除主改图提供商）
        fallback_order = [k for k in ["gemini", "gitee", "grok"] if k != primary_key and k in providers]

        # 获取当前改图使用的模型
        edit_model = get_edit_model(primary_key, primary)
        logger.info(
            f"[Portrait] 改图配置: provider={primary_key}, model={edit_model}, "
            f"fallback={self.enable_fallback}, images={len(images)}"
        )

        # 尝试主提供商
        if primary.enabled:
            try:
                # 临时切换模型用于改图
                original_model = primary.model
                if primary_name == "Gemini" and self.edit_model_gemini:
                    primary.model = self.edit_model_gemini
                elif primary_name == "Grok" and self.edit_model_grok:
                    if hasattr(primary, 'image_model'):
                        primary.image_model = self.edit_model_grok
                    primary.model = self.edit_model_grok

                if primary_name == "Gitee":
                    # Gitee 使用异步改图 API，edit_model 已在服务初始化时设置
                    # 临时切换 edit_model
                    original_edit_model = primary.edit_model if hasattr(primary, 'edit_model') else None
                    if self.edit_model_gitee:
                        primary.edit_model = self.edit_model_gitee
                    image_path = await primary.edit(prompt, images)
                    logger.info(f"[Portrait] Gitee 改图成功")
                    # 保存元数据，分类为 edit
                    await self.image_manager.set_metadata_async(
                        image_path.name,
                        prompt,
                        model=self.edit_model_gitee or primary.edit_model,
                        category="edit",
                    )
                    # 恢复原模型
                    if original_edit_model is not None:
                        primary.edit_model = original_edit_model
                    return image_path
                else:
                    # Gemini/Grok 使用 generate + images 参数
                    image_path = await primary.generate(prompt, images=images)
                    logger.info(f"[Portrait] {primary_name} 改图成功")
                    # 保存元数据，分类为 edit
                    await self.image_manager.set_metadata_async(
                        image_path.name,
                        prompt,
                        model=edit_model,  # 使用改图专用模型名
                        category="edit",
                    )
                    # 恢复原模型
                    primary.model = original_model
                    return image_path
            except Exception as e:
                # 恢复原模型
                if 'original_model' in dir():
                    primary.model = original_model
                logger.warning(f"[Portrait] {primary_name} 改图失败: {e}")
                if not self.enable_fallback:
                    raise

        # 尝试备用提供商
        if self.enable_fallback:
            for fallback_key in fallback_order:
                fallback, fallback_name = providers[fallback_key]
                if not fallback or not fallback.enabled:
                    continue
                try:
                    # 获取备用提供商的改图模型
                    fallback_edit_model = get_edit_model(fallback_key, fallback)
                    if fallback_name == "Gitee":
                        image_path = await fallback.edit(prompt, images)
                        model_name = self.edit_model_gitee or (fallback.edit_model if hasattr(fallback, 'edit_model') else fallback.model)
                    else:
                        image_path = await fallback.generate(prompt, images=images)
                        model_name = fallback_edit_model
                    logger.info(f"[Portrait] {fallback_name} 改图成功 (备用)")
                    # 保存元数据，分类为 edit
                    await self.image_manager.set_metadata_async(
                        image_path.name,
                        prompt,
                        model=model_name,
                        category="edit",
                    )
                    return image_path
                except Exception as e:
                    logger.warning(f"[Portrait] {fallback_name} 改图失败: {e}")
                    continue

        raise RuntimeError("所有提供商均不可用或改图失败")

    # === v2.4.0: 统一图片生成方法（支持主备切换） ===
    async def _generate_image(
        self,
        prompt: str,
        size: str | None = None,
        resolution: str | None = None,
        images: list[bytes] | None = None,
        is_character_related: bool | None = None,
    ) -> Path:
        """统一图片生成方法，支持主备切换

        Args:
            prompt: 图片描述提示词
            size: 图片尺寸（仅 Gitee 支持）
            resolution: 分辨率（仅 Gitee 支持）
            images: 额外参考图片列表（会与自拍参考照合并）
            is_character_related: 是否角色相关（可选，避免重复判断）

        Returns:
            生成的图片路径
        """
        # 使用传入的判断结果或重新判断
        if is_character_related is None:
            is_character_related = self._is_character_related_prompt(prompt)

        # === v2.9.0: 智能参考图加载 - 仅角色相关请求使用参考图 ===
        selfie_refs = []
        if is_character_related:
            # 仅当 prompt 与角色相关时才加载参考照
            selfie_refs = await self._load_selfie_reference_images()
        elif self.selfie_enabled:
            logger.info(f"[Portrait] 已跳过参考图加载(非角色相关请求)")

        # 合并参考图：自拍参考照在前，用户提供的图片在后
        all_images: list[bytes] | None = None
        if selfie_refs or images:
            all_images = []
            if selfie_refs:
                all_images.extend(selfie_refs)
            if images:
                all_images.extend(images)

        # === v2.9.3: 检测是否需要自定义尺寸（非正方形）===
        # 非正方形尺寸时，Gemini/Grok 会自动使用默认正方形尺寸
        is_custom_size = False
        if size:
            size_upper = size.upper()
            if "X" in size_upper:
                parts = size_upper.split("X")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    w, h = int(parts[0]), int(parts[1])
                    if w != h:  # 非正方形
                        is_custom_size = True

        # === v3.0.0: 支持 Grok 作为第三个提供商，统一 provider 选择逻辑 ===
        providers = {
            "gitee": (self.gitee_draw, "Gitee"),
            "gemini": (self.gemini_draw, "Gemini"),
            "grok": (self.grok_draw, "Grok"),
        }

        # 确定主提供商
        primary_key = self.draw_provider if self.draw_provider in providers else "gitee"
        primary, primary_name = providers[primary_key]

        # 调试日志
        logger.info(
            f"[Portrait] 生图配置: provider={self.draw_provider}, primary={primary_name}, "
            f"enabled={primary.enabled}, fallback={self.enable_fallback}, fallback_models={self.fallback_models}, "
            f"ref_images={len(all_images) if all_images else 0}, custom_size={is_custom_size}"
        )

        # 确定备用提供商顺序：使用用户配置的顺序，过滤掉主模型
        fallback_order = [k for k in self.fallback_models if k != primary_key and k in providers]

        # 辅助函数：保存元数据
        async def save_image_metadata(image_path: Path, model_name: str) -> None:
            """保存图片元数据到 ImageManager"""
            try:
                category = "character" if is_character_related else "other"
                await self.image_manager.set_metadata_async(
                    image_path.name,
                    prompt,
                    model=model_name,
                    category=category,
                    size=size or resolution or "",
                )
                logger.debug(f"[Portrait] 已保存图片元数据: {image_path.name}, model={model_name}, category={category}")
            except Exception as e:
                logger.warning(f"[Portrait] 保存图片元数据失败: {e}")

        # 尝试主提供商
        if primary.enabled:
            try:
                if primary_name == "Gitee":
                    # Gitee 不支持参考图
                    if all_images:
                        logger.warning(f"[Portrait] Gitee 不支持参考图，将忽略 {len(all_images)} 张参考图")
                    image_path = await primary.generate(prompt, size=size, resolution=resolution)
                    await save_image_metadata(image_path, primary.model)
                    return image_path
                elif primary_name == "Grok":
                    # Grok 不支持自定义宽高比，使用 resolution 或默认尺寸
                    logger.info(f"[Portrait] Grok 调用参数: size={size}, resolution={resolution}, is_custom_size={is_custom_size}")
                    if is_custom_size:
                        logger.info(f"[Portrait] Grok 不支持自定义宽高比，将使用默认正方形尺寸")
                        image_path = await primary.generate(prompt, images=all_images, resolution=resolution)
                    else:
                        image_path = await primary.generate(prompt, images=all_images, size=size, resolution=resolution)
                    await save_image_metadata(image_path, primary.model)
                    return image_path
                else:  # Gemini
                    # Gemini 不支持自定义宽高比，使用 resolution 或默认尺寸
                    if is_custom_size:
                        logger.info(f"[Portrait] Gemini 不支持自定义宽高比，将使用默认正方形尺寸")
                    image_path = await primary.generate(prompt, all_images, resolution=resolution)
                    await save_image_metadata(image_path, primary.model)
                    return image_path
            except Exception as e:
                logger.warning(f"[Portrait] {primary_name} 生成失败: {e}")
                if not self.enable_fallback:
                    raise
        else:
            # 主提供商未启用
            if not self.enable_fallback:
                raise ValueError(f"主提供商 {primary_name} 未启用（未配置 API Key），且备用功能已禁用")

        # 尝试备用提供商
        if self.enable_fallback:
            for fallback_key in fallback_order:
                fallback, fallback_name = providers[fallback_key]
                if fallback.enabled:
                    logger.info(f"[Portrait] 切换到备用提供商 {fallback_name}")
                    try:
                        if fallback_name == "Gitee":
                            if all_images:
                                logger.warning(f"[Portrait] Gitee 不支持参考图，将忽略 {len(all_images)} 张参考图")
                            image_path = await fallback.generate(prompt, size=size, resolution=resolution)
                            await save_image_metadata(image_path, fallback.model)
                            return image_path
                        elif fallback_name == "Grok":
                            image_path = await fallback.generate(prompt, images=all_images, size=size, resolution=resolution)
                            await save_image_metadata(image_path, fallback.model)
                            return image_path
                        else:  # Gemini
                            image_path = await fallback.generate(prompt, all_images, resolution=resolution)
                            await save_image_metadata(image_path, fallback.model)
                            return image_path
                    except Exception as e:
                        logger.warning(f"[Portrait] {fallback_name} 生成失败: {e}")
                        continue

        # 都不可用
        enabled_providers = [name for _, name in providers.values() if _.enabled]
        if not enabled_providers:
            raise ValueError("未配置任何图片生成服务，请在插件配置中填写 Gitee AI、Gemini 或 Grok API Key")
        else:
            raise ValueError("图片生成失败，所有提供商都已尝试")

    def _build_final_prompt(self, prompt: str, is_character_related: bool | None = None) -> str:
        """构建最终 prompt（自动添加角色外貌）

        Args:
            prompt: 原始提示词
            is_character_related: 是否角色相关（可选，避免重复判断）
        """
        if not self.auto_prepend_identity or not self.char_identity:
            return prompt

        # 使用传入的判断结果或重新判断
        if is_character_related is None:
            is_character_related = self._is_character_related_prompt(prompt)

        if not is_character_related:
            logger.debug("[Portrait] 非角色相关请求，跳过自动添加角色外貌")
            return prompt

        # 检查 prompt 是否已包含核心特征关键词
        identity_keywords = ["asian girl", "pink hair", "rose pink", "dusty rose", "air bangs"]
        has_identity = any(kw.lower() in prompt.lower() for kw in identity_keywords)
        if not has_identity:
            logger.debug("[Portrait] 自动添加角色外貌到 prompt")
            return f"{self.char_identity} {prompt}"
        return prompt

    # === v2.9.4: 发送图片并记录消息ID映射 ===
    # === v2.9.7: 使用 file:// 发送图片（需要 Docker 卷映射）===
    async def _send_image_and_record(self, event: AstrMessageEvent, image_path: Path) -> str | None:
        """发送图片并尝试记录消息ID映射

        Args:
            event: 消息事件
            image_path: 图片文件路径

        Returns:
            消息ID（如果能获取到）
        """
        message_id = None

        # 使用 file:// 协议发送图片
        file_uri = f"file://{image_path.resolve()}"

        # 辅助函数：回退到 base64 发送
        async def _fallback_send_base64() -> None:
            image_bytes = await asyncio.to_thread(image_path.read_bytes)
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            await event.send(
                event.chain_result([Comp.Image.fromBase64(image_b64)])
            )

        # 尝试直接使用 bot.call_action 发送以获取消息ID
        if hasattr(event, 'bot') and event.bot:
            try:
                is_group = bool(event.get_group_id())
                message = [{"type": "image", "data": {"file": file_uri}}]

                if is_group:
                    result = await event.bot.send_group_msg(
                        group_id=int(event.get_group_id()),
                        message=message
                    )
                else:
                    result = await event.bot.send_private_msg(
                        user_id=int(event.get_sender_id()),
                        message=message
                    )

                # 获取返回的消息ID
                if isinstance(result, dict) and 'message_id' in result:
                    message_id = str(result['message_id'])
                    # 记录映射
                    self._record_sent_image(message_id, image_path)

            except Exception as e:
                logger.warning(f"[Portrait] 使用 bot API 发送失败，回退到 event.send: {e}")
                # 回退到标准方式（使用 base64）
                await _fallback_send_base64()
        else:
            # 没有 bot 对象，使用标准方式（使用 base64）
            await _fallback_send_base64()

        return message_id

    def _record_sent_image(self, message_id: str, image_path: Path):
        """记录发送的图片映射"""
        # 清理过多的记录
        if len(self.sent_images) >= self.max_sent_images:
            # 删除最早的一半记录
            keys_to_delete = list(self.sent_images.keys())[:len(self.sent_images) // 2]
            for key in keys_to_delete:
                del self.sent_images[key]
            logger.debug(f"[Portrait] 清理了 {len(keys_to_delete)} 条旧的图片映射记录")

        self.sent_images[message_id] = image_path

    # === v2.0.0: LLM 工具调用 - 文生图 ===
    async def _handle_image_generation(
        self,
        event: AstrMessageEvent,
        prompt: str,
        size: str | None = None,
        resolution: str | None = None,
    ) -> str:
        """通用图片生成处理"""
        # === v2.9.5: 冷却时间检查 ===
        is_allowed, _ = self._check_cooldown(event)
        if not is_allowed:
            # 静默忽略冷却期间的请求，返回成功让 LLM 不再回复
            logger.debug(f"[Portrait] 用户 {event.get_sender_id()} 画图冷却中，静默忽略请求")
            return "[SUCCESS] 图片已处理。"

        try:
            # === v2.9.2: 统一判断角色相关性，避免重复调用 ===
            is_character_related = self._is_character_related_prompt(prompt)

            final_prompt = self._build_final_prompt(prompt, is_character_related)
            image_path = await self._generate_image(
                final_prompt,
                size=size,
                resolution=resolution,
                is_character_related=is_character_related,
            )

            # === v2.9.4: 发送图片并记录消息ID映射 ===
            message_id = await self._send_image_and_record(event, image_path)
            if message_id:
                logger.debug(f"[Portrait] 已记录图片映射: msg_id={message_id}, path={image_path}")

            # === v2.9.5: 更新冷却时间 ===
            self._update_cooldown(event)

            return "[SUCCESS] 图片已成功生成并发送给用户。任务完成，无需再次调用此工具。"
        except Exception as e:
            logger.error(f"[Portrait] 文生图失败: {e}")
            return f"[ERROR] 生成图片失败: {str(e)}"

    @filter.llm_tool(name="portrait_draw_image")
    async def portrait_draw_image(self, event: AstrMessageEvent, prompt: str):
        """根据提示词生成图片。调用一次即可，图片会自动发送给用户。收到 [SUCCESS] 后请勿重复调用。

        Args:
            prompt(string): 图片提示词，需要包含主体、场景、风格等描述
        """
        return await self._handle_image_generation(event, prompt)

    # === v2.5.0: 画图帮助指令 ===
    @filter.command("画图帮助")
    async def draw_help(self, event: AstrMessageEvent):
        """显示画图帮助信息"""
        help_text = """🎨 人物形象 - 画图帮助
━━━━━━━━━━━━━━━

【工作原理】
本插件通过 AI 注入人物形象 Prompt，让 LLM 调用工具自动生成图片。
当检测到画图意图时，会自动注入人物特征、环境、镜头等上下文。

【触发方式】
发送包含以下关键词的消息即可触发：
  画、拍、照、自拍、全身、穿搭、看看、康康
  draw、photo、selfie、picture、image
  给我看、让我看、发张、来张、再来一

【预设提示词】
如需使用预设提示词，请安装 banana_sign 插件。
  /lm列表 - 查看所有预设提示词
  /lm添加 - 添加新提示词（管理员）
  /lm详情 <触发词> - 查看提示词详情

━━━━━━━━━━━━━━━
"""

        yield event.plain_result(help_text)

    # === v2.7.0: WebUI 管理指令 ===
    @filter.command("后台管理")
    async def webui_control(self, event: AstrMessageEvent, action: str = ""):
        """手动启动或关闭 WebUI 后台管理界面

        Args:
            action: 操作类型，可选 "开" 或 "关"
        """
        # 管理员鉴权
        if not self._is_global_admin(event):
            yield event.plain_result("仅管理员可使用此命令")
            return

        action = action.strip()

        # 获取 WebUI 配置
        webui_conf = self.config.get("webui_config", {}) or {}
        default_host = webui_conf.get("host", "127.0.0.1") or "127.0.0.1"
        default_port = webui_conf.get("port", 8088) or 8088
        default_token = webui_conf.get("token", "") or ""

        if action == "开":
            # 如果 WebServer 未实例化，动态创建
            if not self.web_server:
                self.web_server = WebServer(
                    plugin=self,
                    host=default_host,
                    port=default_port,
                    token=default_token,
                )

            if self._webui_started:
                host = self.web_server.host
                port = self.web_server.port
                yield event.plain_result(f"WebUI 已在运行中\n地址: http://{host}:{port}")
                return

            try:
                await self._start_webui()
                self._webui_started = True
                host = self.web_server.host
                port = self.web_server.port
                yield event.plain_result(f"WebUI 已启动\n地址: http://{host}:{port}")
            except Exception as e:
                self._webui_started = False
                yield event.plain_result(f"WebUI 启动失败: {e}")

        elif action == "关":
            if not self.web_server or not self._webui_started:
                yield event.plain_result("WebUI 未在运行")
                return

            try:
                await self.web_server.stop()
                self._webui_started = False
                yield event.plain_result("WebUI 已关闭")
            except Exception as e:
                yield event.plain_result(f"WebUI 关闭失败: {e}")

        else:
            # 显示当前状态
            if self.web_server:
                status = "运行中" if self._webui_started else "已停止"
                host = self.web_server.host
                port = self.web_server.port
            else:
                status = "未初始化"
                host = default_host
                port = default_port
            msg = f"""WebUI 后台管理
━━━━━━━━━━━━━━━
状态: {status}
地址: http://{host}:{port}

用法:
  /后台管理 开  - 启动 WebUI
  /后台管理 关  - 关闭 WebUI
━━━━━━━━━━━━━━━"""
            yield event.plain_result(msg)

    # === v2.9.4: 消息撤回和图片删除命令 ===
    async def _recall_message(self, event: AstrMessageEvent, message_id: str) -> bool:
        """撤回指定消息

        Args:
            event: 消息事件
            message_id: 要撤回的消息 ID

        Returns:
            是否成功撤回
        """
        try:
            # 尝试获取 bot 对象并调用撤回 API
            if hasattr(event, 'bot') and event.bot:
                await event.bot.call_action("delete_msg", message_id=int(message_id))
                return True
            else:
                logger.warning("[Portrait] 无法获取 bot 对象，撤回失败")
                return False
        except Exception as e:
            logger.error(f"[Portrait] 撤回消息失败: {e}")
            return False

    def _extract_image_filename_from_url(self, url: str) -> str | None:
        """从图片 URL 中提取文件名"""
        if not url:
            return None
        # 尝试从 URL 中提取文件名
        # 格式可能是: .../generated_images/1770263908130_e5f0ff33.jpg
        match = self._img_url_pattern.search(url)
        if match:
            return match.group(1)
        return None

    @filter.command("删图")
    async def delete_image(self, event: AstrMessageEvent):
        """引用一张由本插件生成的图片，撤回并从 WebUI 删除"""
        # 管理员鉴权
        if not self._is_global_admin(event):
            yield event.plain_result("仅管理员可使用此命令")
            return

        # 获取被引用的消息
        reply_msg_id = None
        image_url = None

        for comp in event.get_messages():
            if isinstance(comp, Comp.Reply):
                reply_msg_id = str(comp.id) if comp.id else None
                logger.debug(f"[Portrait] Reply 组件: id={comp.id}, chain={getattr(comp, 'chain', None)}")
                # 从引用消息中获取图片
                if hasattr(comp, 'chain') and comp.chain:
                    for quote_comp in comp.chain:
                        if isinstance(quote_comp, Comp.Image):
                            image_url = quote_comp.url
                            logger.debug(f"[Portrait] 找到图片 URL: {image_url}")
                            break
                break

        if not reply_msg_id:
            yield event.plain_result("请引用一张图片后使用 /删图 命令")
            return

        # 尝试撤回消息
        recall_success = await self._recall_message(event, reply_msg_id)

        # === v2.9.4: 优先从映射表获取图片路径，否则从 URL 提取 ===
        delete_success = False
        image_path = None

        # 方式1：从映射表查找
        if reply_msg_id in self.sent_images:
            image_path = self.sent_images[reply_msg_id]
            logger.debug(f"[Portrait] 从映射表找到图片: {image_path}")
            # 删除映射记录
            del self.sent_images[reply_msg_id]

        # 方式2：从 URL 提取文件名
        if not image_path and image_url:
            filename = self._extract_image_filename_from_url(image_url)
            if filename:
                image_path = self.data_dir / "generated_images" / filename
                logger.debug(f"[Portrait] 从 URL 提取图片路径: {image_path}")

        # 删除图片文件
        if image_path and image_path.exists():
            try:
                image_path.unlink()
                delete_success = True
                logger.info(f"[Portrait] 已删除图片文件: {image_path.name}")
            except Exception as e:
                logger.error(f"[Portrait] 删除图片文件失败: {e}")

        # 返回结果
        if recall_success and delete_success:
            yield event.plain_result("已撤回消息并删除图片")
        elif recall_success:
            yield event.plain_result("已撤回消息（图片文件未找到或删除失败）")
        elif delete_success:
            yield event.plain_result("已删除图片文件（消息撤回失败，可能已超时）")
        else:
            yield event.plain_result("操作失败：无法撤回消息或删除图片")

    # === v3.1.1: 防止工具调用时重复回复 ===
    @filter.on_llm_response(priority=100)  # 高优先级，尽早处理
    async def on_llm_response(self, event: AstrMessageEvent, response: LLMResponse):
        """当 LLM 响应同时包含 tool_calls 和 content 时，清空 content 防止重复回复"""
        if self._is_terminated:
            return

        try:
            # 检查是否有 tool_calls
            tool_calls = getattr(response, 'tool_calls', None)
            if not tool_calls:
                return

            # 检查是否调用了 portrait 相关的工具
            portrait_tools = {'portrait_draw_image', 'portrait_generate_image'}
            has_portrait_tool = False
            for tc in tool_calls:
                func_name = getattr(tc, 'function', {})
                if hasattr(func_name, 'name'):
                    func_name = func_name.name
                elif isinstance(func_name, dict):
                    func_name = func_name.get('name', '')
                if func_name in portrait_tools:
                    has_portrait_tool = True
                    break

            if not has_portrait_tool:
                return

            # 如果同时有 content，清空它
            content = getattr(response, 'completion_text', None)
            if content and content.strip():
                logger.info(f"[Portrait] 检测到工具调用时附带 content，已清空防止重复回复")
                response.completion_text = ""
                # 同时清空 result_chain 中的文本
                if hasattr(response, 'result_chain') and response.result_chain:
                    response.result_chain = [
                        comp for comp in response.result_chain
                        if not isinstance(comp, Comp.Plain)
                    ]
        except Exception as e:
            logger.debug(f"[Portrait] on_llm_response 处理异常: {e}")
