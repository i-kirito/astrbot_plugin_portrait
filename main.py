from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools
from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest
import astrbot.api.message_components as Comp
import re
import asyncio
import json
from datetime import datetime
from pathlib import Path

from .core.gitee_draw import GiteeDrawService
from .core.gemini_draw import GeminiDrawService
from .web_server import WebServer


# === Issue 2 fix: Move template constants to module level ===
TPL_HEADER = """# Visual Context Injection (System Override)
## 0. Chain of Thought & Trigger Logic
1.  **Analyze User Intent**: specific keywords like "draw", "photo", "selfie", "show me", "look at you", or implicitly asking for a visual representation.
2.  **If Drawing Intent Detected**: You MUST call the `portrait_draw_image` tool with the Visual Data below.
3.  **Prompt Structure**: `[Character Visuals] + [User Action/Outfit] + [Environment] + [Camera]`
4.  **IMPORTANT**: Always use `portrait_draw_image` tool for image generation.
5.  **CRITICAL**: When calling any tool, do NOT output any text content in the same response. Call the tool ONLY, then wait for the result before responding to the user.
6.  **MANDATORY**: You MUST copy the EXACT prompt blocks from the Environment and Camera sections below verbatim. Do NOT simplify, summarize, or omit any parameters. Include ALL lighting, style, and quality tags exactly as written.
7.  **NO REPEAT**: After the tool returns [SUCCESS], do NOT call portrait_draw_image again with the same or similar prompt. The image has already been sent to the user."""

TPL_CHAR = """## 1. Character Visuals (Fixed Identity)
**Core Appearance (Always Active):**
{content}"""

TPL_MIDDLE = """## 2. 动态内容处理 (Handling User Input)
* **穿搭 (Outfit):** 用户未指定时，默认保持简洁风格或根据场景补全。
* **动作 (Action):** 自然融入用户描述的动作。如果动作/表情与核心设定的冲突，**以用户要求为准**"""

TPL_FOOTER = """---"""

DEF_CHAR_IDENTITY = """> **The subject is a young 18-year-old Asian girl with fair skin and delicate features. She has dusty rose pink hair featuring essential wispy air bangs. Her large, round, doll-like eyes are deep-set and natural dark brown. She possesses a slender hourglass figure with a tiny waist and a full bust, emphasizing a natural soft tissue silhouette.**"""


class PortraitPlugin(Star):
    """人物特征Prompt注入器,增强美化画图,内置Gitee AI文生图"""

    # 默认环境和摄影配置
    DEFAULT_ENVIRONMENTS = [
        {
            "name": "默认/卧室",
            "keywords": ["default"],
            "prompt": "(indoors, cute girl's bedroom aesthetic:1.3), (kawaii style:1.2), (natural window light mixed with warm indoor lamps:1.3), (realistic light and shadow:1.2), (pastel pink and warm tones:1.1), cozy atmosphere"
        },
        {
            "name": "更衣室",
            "keywords": ["穿搭", "全身", "OOTD", "look"],
            "prompt": "(indoors, pink aesthetic dressing room:1.4), (bright sunlight streaming through sheer curtains:1.4), (white vanity table), (pink fluffy stool), (pink clothing rack), (pastel pink and white tones:1.2), cozy, kawaii aesthetic"
        },
        {
            "name": "户外/自定义",
            "keywords": ["户外", "外面", "公园", "街"],
            "prompt": "根据用户指定地点生成场景。必须添加: (blurred background), (bokeh), (natural lighting)"
        }
    ]

    DEFAULT_CAMERAS = [
        {
            "name": "自拍模式",
            "keywords": ["自拍", "selfie", "对镜"],
            "prompt": "(mirror selfie style:1.2), holding phone, looking at phone screen or mirror, (realistic screen light reflection on face), cute pose, close-up POV shot"
        },
        {
            "name": "全身/远景",
            "keywords": ["全身", "full body", "穿搭", "OOTD"],
            "prompt": "full body shot, head to toe visible, wide angle, far shot, (relaxed fashion pose:1.3), casual stance, legs and shoes visible"
        },
        {
            "name": "半身/默认",
            "keywords": ["default"],
            "prompt": "upper body shot, medium close-up portrait, looking at camera, (dynamic random pose:1.2), (playful gestures:1.1), candid portrait"
        }
    ]

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
            '画', '拍', '照', '自拍', '全身', '穿搭', '看看', '康康', '瞧瞧', '瞅瞅', '爆照', '形象', '样子',
            'draw', 'photo', 'selfie', 'picture', 'image', 'shot', 'snap',
            '给我[看康瞧]', '让我[看康瞧]', '发[张个一]', '来[张个一]',
            '在干[嘛啥什么]', '干什么呢', r'现在.{0,3}样子',
            'ootd', 'outfit', 'look', '再来一', '再拍', '再画'
        ]
        self.trigger_regex = re.compile(f"({'|'.join(trigger_keywords)})", re.IGNORECASE)

        # 读取用户配置
        p_char_id = self.config.get("char_identity") or DEF_CHAR_IDENTITY
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

        # === v2.0.0: Gitee AI 文生图服务 ===
        gitee_conf = self.config.get("gitee_config", {}) or {}
        cache_conf = self.config.get("cache_config", {}) or {}
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

        # === v2.6.0: 人像参考配置 ===
        selfie_conf = self.config.get("selfie_config", {}) or {}
        self.selfie_enabled = selfie_conf.get("enabled", False)
        # 清理废弃的 reference_images 字段
        if "reference_images" in selfie_conf:
            del selfie_conf["reference_images"]
            self.config["selfie_config"] = selfie_conf

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
            "environments": self.DEFAULT_ENVIRONMENTS,
            "cameras": self.DEFAULT_CAMERAS,
        }

    def _save_dynamic_config(self):
        """保存动态配置到独立文件"""
        try:
            with open(self.dynamic_config_path, "w", encoding="utf-8") as f:
                json.dump(self._dynamic_config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Portrait] 保存动态配置失败: {e}")

    def _load_persisted_config(self):
        """加载 WebUI 持久化的配置"""
        if self.config_persist_path.exists():
            try:
                with open(self.config_persist_path, "r", encoding="utf-8") as f:
                    persisted = json.load(f)
                # 合并到当前配置（持久化配置优先）
                for key, value in persisted.items():
                    self.config[key] = value
                logger.debug(f"[Portrait] 已加载持久化配置: {list(persisted.keys())}")
            except Exception as e:
                logger.warning(f"[Portrait] 加载持久化配置失败: {e}")

    def save_config_to_disk(self):
        """将当前配置持久化到磁盘"""
        # 需要持久化的字段
        persist_fields = {
            "char_identity",
            "injection_rounds",
            "proxy",
            "gitee_config",
            "gemini_config",
            "draw_provider",
            "enable_fallback",
            "selfie_config",
        }
        try:
            persist_data = {k: v for k, v in self.config.items() if k in persist_fields}
            with open(self.config_persist_path, "w", encoding="utf-8") as f:
                json.dump(persist_data, f, ensure_ascii=False, indent=2)
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
        """加载人像参考照片 - 自动扫描 selfie_refs 目录（异步）"""
        if not self.selfie_enabled:
            return []

        selfie_refs_dir = self.data_dir / "selfie_refs"
        if not selfie_refs_dir.exists():
            return []

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
        return images

    def get_dynamic_config(self) -> dict:
        """获取动态配置（环境和摄影模式列表）"""
        return {
            "environments": self._dynamic_config.get("environments", self.DEFAULT_ENVIRONMENTS),
            "cameras": self._dynamic_config.get("cameras", self.DEFAULT_CAMERAS),
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
        p_char_id = self.config.get("char_identity") or DEF_CHAR_IDENTITY

        # 环境列表（根据开关决定是否生成）
        if self.enable_env_injection:
            environments = self._dynamic_config.get("environments", self.DEFAULT_ENVIRONMENTS)
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
            cameras = self._dynamic_config.get("cameras", self.DEFAULT_CAMERAS)
            cam_section_lines = ["## 4. 摄影模式切换 (Photo Format Switching)"]
            cam_section_lines.append("**指令:** 检查**当前用户输入**中的关键词。**不要**参考历史记录。")

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
            logger.info("[Portrait] 插件已停止，清理资源完成")
        except Exception as e:
            logger.error(f"[Portrait] 停止插件出错: {e}")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        # 生命周期检查：防止旧实例继续工作
        if self._is_terminated:
            return

        # 延迟启动 WebUI（首次 LLM 请求时，此时事件循环已在运行）
        if self.web_server and not self._webui_started:
            self._webui_started = True
            task = asyncio.create_task(self._start_webui())
            self._bg_tasks.add(task)

        # v1.6.0: One-Shot 单次注入策略
        # 仅在检测到绘图意图时注入 Visual Context

        # 获取用户消息内容 - 优先使用原始消息，避免被其他插件修改
        user_message = ""

        # 方式1 (优先): 从 event.message_str 获取（用户原始消息，未被其他插件修改）
        if hasattr(event, 'message_str') and event.message_str:
            user_message = event.message_str

        # 方式2: 从 event.message 获取
        if not user_message and hasattr(event, 'message') and event.message:
            if hasattr(event.message, 'message'):
                for seg in event.message.message:
                    if hasattr(seg, 'text'):
                        user_message += seg.text
                    elif hasattr(seg, 'data') and isinstance(seg.data, dict):
                        user_message += seg.data.get('text', '')
            # 尝试直接获取 raw_message
            if not user_message and hasattr(event.message, 'raw_message'):
                user_message = event.message.raw_message or ""

        # 方式3 (备选): 从 req.prompt 获取（可能被记忆插件等修改过）
        if not user_message and hasattr(req, 'prompt') and req.prompt:
            if isinstance(req.prompt, str):
                user_message = req.prompt
            elif isinstance(req.prompt, list):
                # 如果是消息列表，提取最后一条用户消息
                for msg in reversed(req.prompt):
                    if isinstance(msg, dict) and msg.get('role') == 'user':
                        content = msg.get('content', '')
                        if isinstance(content, str):
                            user_message = content
                        break

        # 方式4 (最后备选): 从 req.messages 获取最后一条用户消息
        if not user_message and hasattr(req, 'messages') and req.messages:
            for msg in reversed(req.messages):
                if hasattr(msg, 'role') and msg.role == 'user':
                    if hasattr(msg, 'content'):
                        user_message = str(msg.content) if msg.content else ""
                    break

        logger.debug(f"[Portrait] 提取到用户消息: {user_message[:50] if user_message else '(空)'}")

        # 正则匹配检测绘图意图
        if not user_message or not self.trigger_regex.search(user_message):
            logger.debug(f"[Portrait] 未检测到绘图意图，跳过注入")
            return

        # === v1.8.1: 多轮次注入逻辑 ===
        # 修复：使用 群ID + 用户ID 作为 session key，避免群内用户互相污染
        group_id = event.unified_msg_origin or "default"
        user_id = str(event.get_sender_id()) if hasattr(event, 'get_sender_id') else "unknown"
        session_id = f"{group_id}:{user_id}"
        current_time = datetime.now().timestamp()

        # 清理过期会话（防止内存无限增长）
        expired_sessions = [
            sid for sid, last_active in self.injection_last_active.items()
            if current_time - last_active > self.session_ttl
        ]
        for sid in expired_sessions:
            self.injection_counter.pop(sid, None)
            self.injection_last_active.pop(sid, None)
        if expired_sessions:
            logger.debug(f"[Portrait] 已清理 {len(expired_sessions)} 个过期会话")

        # 更新当前会话的活跃时间
        self.injection_last_active[session_id] = current_time

        # 检测到绘图触发词时，重置/初始化该会话的注入计数
        if self.trigger_regex.search(user_message):
            # 如果是新触发或计数已耗尽，重新初始化
            if session_id not in self.injection_counter or self.injection_counter[session_id] <= 0:
                self.injection_counter[session_id] = self.injection_rounds
                logger.info(f"[Portrait] 检测到绘图意图，初始化注入轮次: {self.injection_rounds}")

        # 检查是否还有剩余注入次数
        remaining = self.injection_counter.get(session_id, 0)
        if remaining <= 0:
            # === v2.2.0: 注入轮次用尽后清理历史记忆中的 portrait 注入内容 ===
            self._clean_portrait_injection(req)
            logger.debug(f"[Portrait] 会话 {session_id} 注入次数已用尽，已清理历史注入内容")
            return

        # 执行注入并减少计数
        injection = f"\n\n<portrait_status>\n{self.full_prompt}\n</portrait_status>"
        if not req.system_prompt:
            req.system_prompt = ""
        req.system_prompt += injection

        self.injection_counter[session_id] -= 1
        remaining_after = self.injection_counter[session_id]

        # 如果这是最后一轮注入，标记需要在下次请求时清理
        if remaining_after <= 0:
            logger.info(f"[Portrait] Visual Context 已注入 (最后一轮 {self.injection_rounds}/{self.injection_rounds}) - 下次请求将清理历史注入")
        else:
            logger.info(f"[Portrait] Visual Context 已注入 (轮次 {self.injection_rounds - remaining_after}/{self.injection_rounds}) - 触发词: {user_message[:30]}...")

    def _clean_portrait_injection(self, req: ProviderRequest):
        """清理请求中的 portrait 注入内容，防止污染上下文"""
        import re
        portrait_pattern = re.compile(r'\s*<portrait_status>.*?</portrait_status>\s*', re.DOTALL)

        # 清理 system_prompt
        if req.system_prompt:
            cleaned = portrait_pattern.sub('', req.system_prompt)
            if cleaned != req.system_prompt:
                req.system_prompt = cleaned
                logger.debug("[Portrait] 已从 system_prompt 清理注入内容")

        # 清理 messages 中的历史消息
        if hasattr(req, 'messages') and req.messages:
            for msg in req.messages:
                if hasattr(msg, 'content') and isinstance(msg.content, str):
                    cleaned = portrait_pattern.sub('', msg.content)
                    if cleaned != msg.content:
                        msg.content = cleaned
                        logger.debug(f"[Portrait] 已从 {msg.role} 消息清理注入内容")

        # 清理 prompt (如果是字符串)
        if hasattr(req, 'prompt') and isinstance(req.prompt, str):
            cleaned = portrait_pattern.sub('', req.prompt)
            if cleaned != req.prompt:
                req.prompt = cleaned
                logger.debug("[Portrait] 已从 prompt 清理注入内容")

    # === v2.4.0: 统一图片生成方法（支持主备切换） ===
    async def _generate_image(
        self,
        prompt: str,
        size: str | None = None,
        resolution: str | None = None,
        images: list[bytes] | None = None,
    ) -> Path:
        """统一图片生成方法，支持主备切换

        Args:
            prompt: 图片描述提示词
            size: 图片尺寸（仅 Gitee 支持）
            resolution: 分辨率（仅 Gitee 支持）
            images: 额外参考图片列表（会与自拍参考照合并）

        Returns:
            生成的图片路径
        """
        # 加载自拍参考照（如果启用且使用 Gemini）
        selfie_refs = await self._load_selfie_reference_images()

        # 合并参考图：自拍参考照在前，用户提供的图片在后
        all_images: list[bytes] | None = None
        if selfie_refs or images:
            all_images = []
            if selfie_refs:
                all_images.extend(selfie_refs)
            if images:
                all_images.extend(images)

        # 有参考图时，优先使用 Gemini，失败则降级到 Gitee（不带参考图）
        if all_images:
            if self.gemini_draw.enabled:
                try:
                    return await self.gemini_draw.generate(prompt, all_images)
                except Exception as e:
                    logger.warning(f"[Portrait] Gemini 生成失败: {e}")
                    if self.enable_fallback and self.gitee_draw.enabled:
                        logger.info("[Portrait] 切换到备用提供商 Gitee（不带参考图）")
                        return await self.gitee_draw.generate(prompt, size=size, resolution=resolution)
                    raise
            elif self.gitee_draw.enabled:
                logger.warning("[Portrait] Gemini 未配置，降级到 Gitee（不带参考图）")
                return await self.gitee_draw.generate(prompt, size=size, resolution=resolution)
            else:
                raise ValueError("参考图功能需要配置 Gemini API Key")

        # 确定主备提供商
        if self.draw_provider == "gemini":
            primary, fallback = self.gemini_draw, self.gitee_draw
            primary_name, fallback_name = "Gemini", "Gitee"
        else:
            primary, fallback = self.gitee_draw, self.gemini_draw
            primary_name, fallback_name = "Gitee", "Gemini"

        # 尝试主提供商
        if primary.enabled:
            try:
                if primary_name == "Gitee":
                    return await primary.generate(prompt, size=size, resolution=resolution)
                else:
                    return await primary.generate(prompt)
            except Exception as e:
                logger.warning(f"[Portrait] {primary_name} 生成失败: {e}")
                if not self.enable_fallback:
                    raise

        # 尝试备用提供商
        if self.enable_fallback and fallback.enabled:
            logger.info(f"[Portrait] 切换到备用提供商 {fallback_name}")
            if fallback_name == "Gitee":
                return await fallback.generate(prompt, size=size, resolution=resolution)
            else:
                return await fallback.generate(prompt)

        # 都不可用
        if not primary.enabled and not fallback.enabled:
            raise ValueError("未配置任何图片生成服务，请在插件配置中填写 Gitee AI 或 Gemini API Key")
        elif not primary.enabled:
            raise ValueError(f"{primary_name} 未配置 API Key")
        else:
            raise ValueError("图片生成失败，备用提供商也未配置")

    def _build_final_prompt(self, prompt: str) -> str:
        """构建最终 prompt（自动添加角色外貌）"""
        if not self.auto_prepend_identity or not self.char_identity:
            return prompt
        # 检查 prompt 是否已包含核心特征关键词
        identity_keywords = ["asian girl", "pink hair", "rose pink", "dusty rose", "air bangs"]
        has_identity = any(kw.lower() in prompt.lower() for kw in identity_keywords)
        if not has_identity:
            logger.debug("[Portrait] 自动添加角色外貌到 prompt")
            return f"{self.char_identity} {prompt}"
        return prompt

    # === v2.0.0: LLM 工具调用 - 文生图 ===
    @filter.llm_tool(name="portrait_draw_image")
    async def portrait_draw_image(self, event: AstrMessageEvent, prompt: str):
        """根据提示词生成图片。调用一次即可，图片会自动发送给用户。收到 [SUCCESS] 后请勿重复调用。

        Args:
            prompt(string): 图片提示词，需要包含主体、场景、风格等描述
        """
        try:
            final_prompt = self._build_final_prompt(prompt)
            image_path = await self._generate_image(final_prompt)
            # 发送图片
            await event.send(
                event.chain_result([Comp.Image.fromFileSystem(str(image_path))])
            )
            return "[SUCCESS] 图片已成功生成并发送给用户。任务完成，无需再次调用此工具。"
        except Exception as e:
            logger.error(f"[Portrait] 文生图失败: {e}")
            return f"[ERROR] 生成图片失败: {str(e)}"

    @filter.llm_tool(name="portrait_generate_image")
    async def portrait_generate_image(
        self,
        event: AstrMessageEvent,
        prompt: str,
        size: str = "",
        resolution: str = "",
    ):
        """根据提示词生成图片，可指定尺寸。调用一次即可，图片会自动发送给用户。收到 [SUCCESS] 后请勿重复调用。

        Args:
            prompt(string): 图片提示词，需要包含主体、场景、风格等描述
            size(string): 图片尺寸，如 "1024x1024"、"2048x2048"、"4096x4096"
            resolution(string): 分辨率，可选 "1K"、"2K"、"4K"
        """
        try:
            final_prompt = self._build_final_prompt(prompt)
            image_path = await self._generate_image(
                final_prompt,
                size=size or None,
                resolution=resolution or None,
            )
            # 发送图片
            await event.send(
                event.chain_result([Comp.Image.fromFileSystem(str(image_path))])
            )
            return "[SUCCESS] 图片已成功生成并发送给用户。任务完成，无需再次调用此工具。"
        except Exception as e:
            logger.error(f"[Portrait] 文生图失败: {e}")
            return f"[ERROR] 生成图片失败: {str(e)}"

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

