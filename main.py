from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest
import astrbot.api.message_components as Comp
import re
import copy
import random
from datetime import datetime

@register("astrbot_plugin_portrait", "ikirito", "人物特征Prompt注入器,增强美化画图", "1.8.2")
class PortraitPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        # v1.6.0: One-Shot 单次注入策略
        # 仅在检测到绘图意图时注入 Visual Context，节省 Token 并避免上下文污染
        self.trigger_regex = re.compile(
            r'(画|拍|照|自拍|全身|穿搭|看看|康康|瞧瞧|瞅瞅|爆照|形象|样子|'
            r'draw|photo|selfie|picture|image|shot|snap|'
            r'给我[看康瞧]|让我[看康瞧]|发[张个一]|来[张个一]|'
            r'在干[嘛啥什么]|干什么呢|现在.{0,3}样子|'
            r'ootd|outfit|look|再来一|再拍|再画)',
            re.IGNORECASE
        )

        # === 默认内容 (Content Only) ===
        self.DEF_CHAR_IDENTITY = """> **The subject is a young 18-year-old Asian girl with fair skin and delicate features. She has dusty rose pink hair featuring essential wispy air bangs. Her large, round, doll-like eyes are deep-set and natural dark brown. She possesses a slender hourglass figure with a tiny waist and a full bust, emphasizing a natural soft tissue silhouette.**"""

        self.DEF_ENV_A = """(indoors, cute girl's bedroom aesthetic:1.3), (kawaii style:1.2), (natural window light mixed with warm indoor lamps:1.3), (realistic light and shadow:1.2), (pastel pink and warm tones:1.1), (fairy lights on wall:1.1), bed filled with plushies, (shelves with anime figures:1.2), gaming setup background, cozy atmosphere, clear background details, (raw photo:1.2), (authentic skin texture:1.2), photorealistic"""

        self.DEF_ENV_B = """(indoors, pink aesthetic dressing room:1.4), (bright sunlight streaming through sheer curtains:1.4), (volumetric lighting), (shadows casting on floor:1.2), (white vanity table with large mirror), (pink fluffy stool), (white shelves filled with plush toys and pink accessories), (pink clothing rack with dresses), (pink utility cart), (pink curtains), (pink fluffy rugs), (pastel pink and white tones:1.2), cozy, kawaii aesthetic, (reflection in vanity mirror is blurred and indistinct:1.5), (focus away from reflection), (raw photo:1.2), (realistic texture:1.3), photorealistic"""

        self.DEF_ENV_C = """根据用户指定地点生成场景。必须添加: (blurred background), (bokeh), (natural lighting)"""

        self.DEF_CAM_A = """, (mirror selfie style:1.2), holding phone, looking at phone screen or mirror, (realistic screen light reflection on face), cute pose, close-up POV shot, (phone camera noise:1.1)"""

        self.DEF_CAM_B = """, full body shot, head to toe visible, wide angle, far shot, (relaxed fashion pose:1.3), casual stance, (natural candid moment:1.4), (looking away from camera:1.2), (subtle closed-mouth smile:1.3), (dreamy soft gaze:1.2), (lost in thought expression:1.1), no phone, legs and shoes visible, (sharp focus:1.3), (fashion editorial style:1.2)"""

        self.DEF_CAM_C = """, upper body shot, medium close-up portrait, looking at camera, (dynamic random pose:1.2), (playful gestures:1.1), (expressive face), candid portrait, no phone, (detailed skin pores), (film grain:1.1)"""

        self.TPL_HEADER = """# Visual Context Injection (System Override)
## 0. Chain of Thought & Trigger Logic
1.  **Analyze User Intent**: specific keywords like "draw", "photo", "selfie", "show me", "look at you", or implicitly asking for a visual representation.
2.  **If Drawing Intent Detected**: You MUST call the `gitee_draw_image` tool (NOT banana_image_generation) with the Visual Data below.
3.  **Prompt Structure**: `[Character Visuals] + [User Action/Outfit] + [Environment] + [Camera]`
4.  **IMPORTANT**: Always use `gitee_draw_image` tool for image generation."""

        self.TPL_CHAR = """## 1. Character Visuals (Fixed Identity)
**Core Appearance (Always Active):**
{content}"""

        self.TPL_MIDDLE = """## 2. 动态内容处理 (Handling User Input)
* **穿搭 (Outfit):** 用户未指定时，默认保持简洁风格或根据场景补全。
* **动作 (Action):** 自然融入用户描述的动作。如果动作/表情与核心设定的冲突，**以用户要求为准**"""

        self.TPL_ENV = """## 3. 动态环境与风格 (Dynamic Environment & Style) - [真实光效版]
**逻辑判断 (Logic Branching):**
* **Scenario A: 默认情况 (自拍 Mode A / 半身照 Mode C)**
    * *场景:* **温馨卧室 (Cozy Bedroom)**。
    * *Prompt Block:*
    > **{env_a}**

* **Scenario B: 全身照模式 (Full Body Mode B)**
    * *触发意图:* 当用户提及"看看穿搭"、"OOTD"、"全身照"时，强制使用此场景。
    * *场景:* **粉色梦幻更衣室 (Pink Dressing Room)**。
    * *Prompt Block:*
    > **{env_b}**

* **Scenario C: 户外/特定场景 (User Specified)**
    * *操作:* {env_c}"""

        self.TPL_CAM = """## 4. 摄影模式切换 (Photo Format Switching) - [强制重置逻辑]
**指令:** 检查**当前用户输入 (Current Input)** 中的关键词。**不要**参考历史记录中的摄影模式。
* **模式 A：自拍 (Selfie Mode)**
    * *触发 (必须在当前句中出现):* "自拍"、"selfie"、"拿着手机"、"对镜自拍"。
    * *Camera Params:* `{cam_a}`

* **模式 B：全身照 (Full Body Shot)**
    * *触发 (必须在当前句中出现):* "全身照"、"看看穿搭"、"full body"、"穿搭"。
    * *Camera Params:* `{cam_b}`

* **模式 C：默认/半身照 (Default)**
    * *触发:* **当当前输入中没有上述 Mode A 或 Mode B 的关键词时，强制使用此模式。**
    * *Camera Params:* `{cam_c}`"""

        self.TPL_FOOTER = """---"""

        # 读取用户配置
        p_char_id = self.config.get("char_identity") or self.DEF_CHAR_IDENTITY
        p_env_a = self.config.get("env_default") or self.DEF_ENV_A
        p_env_b = self.config.get("env_fullbody") or self.DEF_ENV_B
        p_env_c = self.config.get("env_outdoor") or self.DEF_ENV_C

        # 镜头参数逻辑：根据开关决定是否注入
        enable_custom_cam = self.config.get("enable_custom_camera", False)

        if enable_custom_cam:
            p_cam_a = self.config.get("cam_selfie") or self.DEF_CAM_A
            p_cam_b = self.config.get("cam_fullbody") or self.DEF_CAM_B
            p_cam_c = self.DEF_CAM_C
            # 格式化 Camera 部分
            section_camera = self.TPL_CAM.format(cam_a=p_cam_a, cam_b=p_cam_b, cam_c=p_cam_c)
        else:
            # 关闭开关：完全不注入 Camera Logic
            section_camera = ""

        # === 核心 Prompt 组装 ===
        self.full_prompt = (
            f"{self.TPL_HEADER}\n\n"
            f"{self.TPL_CHAR.format(content=p_char_id)}\n\n"
            f"{self.TPL_MIDDLE}\n\n"
            f"{self.TPL_ENV.format(env_a=p_env_a, env_b=p_env_b, env_c=p_env_c)}\n\n"
            f"{section_camera}\n\n"
            f"{self.TPL_FOOTER}\n\n"
            f"--- END CONTEXT DATA ---"
        )

        # === v1.8.1: 注入轮次控制 ===
        # 每个会话的剩余注入次数 {session_id: remaining_count}
        self.injection_counter = {}
        # 会话最后活跃时间，用于清理过期条目 {session_id: timestamp}
        self.injection_last_active = {}
        # 从配置读取注入轮次，默认为 1（单次注入）
        self.injection_rounds = max(1, self.config.get("injection_rounds", 1))
        # 会话过期时间（秒），默认 1 小时
        self.session_ttl = 3600

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        # 确保 scheduler 已启动（延迟启动，此时事件循环已运行）
        self._ensure_scheduler_started()

        # v1.6.0: One-Shot 单次注入策略
        # 仅在检测到绘图意图时注入 Visual Context

        # 获取用户消息内容 - 多种方式尝试提取
        user_message = ""

        # 方式1: 从 req.prompt 获取（最新用户输入）
        if hasattr(req, 'prompt') and req.prompt:
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

        # 方式3: 从 event 直接获取
        if not user_message and hasattr(event, 'message_str'):
            user_message = event.message_str or ""

        # 方式4: 从 req.messages 获取最后一条用户消息
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
        session_id = event.unified_msg_origin or "default"
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
            logger.debug(f"[Portrait] 会话 {session_id} 注入次数已用尽，跳过")
            return

        # 执行注入并减少计数
        injection = f"\n\n<portrait_status>\n{self.full_prompt}\n</portrait_status>"
        if not req.system_prompt:
            req.system_prompt = ""
        req.system_prompt += injection

        self.injection_counter[session_id] -= 1
        remaining_after = self.injection_counter[session_id]

        logger.info(f"[Portrait] Visual Context 已注入 (轮次 {self.injection_rounds - remaining_after}/{self.injection_rounds}) - 触发词: {user_message[:30]}...")
