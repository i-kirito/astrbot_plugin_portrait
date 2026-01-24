from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest

@register("astrbot_plugin_portrait", "ikirito", "摄影师人格注入插件", "1.1.0")
class PortraitPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        # keywords 已废弃，改用 LLM Tool 触发
        self.max_turns = self.config.get("max_turns", 3)

        # === 内置默认 Prompt (分段) ===

        # [Part 1] Visuals
        self.DEF_CHAR_VISUALS = """## 1. Character Visuals - [Fixed Prefix]
**Block 1 (Always Start with):**
> **The subject is a young 18-year-old Asian girl with fair skin and delicate features. She has dusty rose pink hair featuring essential wispy air bangs. Her large, round, doll-like eyes are deep-set and natural dark brown, looking at the viewer with a sweet, engaging smile. She possesses a slender hourglass figure with a tiny waist and a full bust, emphasizing a natural soft tissue silhouette.**"""

        # [Part 2] Identity
        self.DEF_CHAR_IDENTITY = """## 2. Character Identity Card - [Fixed Core]
* **Face & Expression:** 18 years old, doll-like large round eyes (dark brown), default sweet smile.
* **Hair:** Dusty rose pink. **Must keep essential wispy air bangs**. Loose hair only allowed before bed or after waking up; otherwise high ponytail or twin tails.
* **Body:** Slender hourglass figure, tiny waist, full bust, natural soft texture."""

        # [Part 4] Environment Logic (Prefix)
        self.ENV_HEADER = """## 4. Dynamic Environment & Style - [Realistic Lighting]
**Logic Branching:**"""

        # Env A
        self.DEF_ENV_DEFAULT = """* **Scenario A: Default (Selfie Mode A / Half Body Mode C)**
    * *Scene:* **Cozy Bedroom**.
    * *Prompt Block:*
    > **(indoors, cute girl's bedroom aesthetic:1.3), (kawaii style:1.2), (natural window light mixed with warm indoor lamps:1.3), (realistic light and shadow:1.2), (pastel pink and warm tones:1.1), (fairy lights on wall:1.1), bed filled with plushies, (shelves with anime figures:1.2), gaming setup background, cozy atmosphere, clear background details, (raw photo:1.2), (authentic skin texture:1.2), photorealistic**"""

        # Env B
        self.DEF_ENV_FULLBODY = """* **Scenario B: Full Body Mode B**
    * *Scene:* **Pink Dressing Room**.
    * *Prompt Block:*
    > **(indoors, pink aesthetic dressing room:1.4), (bright sunlight streaming through sheer curtains:1.4), (volumetric lighting), (shadows casting on floor:1.2), (white vanity table with large mirror), (pink fluffy stool), (white shelves filled with plush toys and pink accessories), (pink clothing rack with dresses), (pink utility cart), (pink curtains), (pink fluffy rugs), (pastel pink and white tones:1.2), cozy, kawaii aesthetic, (reflection in vanity mirror is blurred and indistinct:1.5), (focus away from reflection), (raw photo:1.2), (realistic texture:1.3), photorealistic**"""

        # Env C
        self.DEF_ENV_OUTDOOR = """* **Scenario C: Outdoor/Specific (User Specified)**
    * *Action:* Remove indoor description, use user specified scene, force add `(blurred background), (bokeh), (natural lighting)`."""

        # [Part 6] Camera Logic (Prefix)
        self.CAM_HEADER = """## 6. Photo Format Switching - [Forced Reset Logic]
**Instruction:** Check **Current Input** for keywords. **DO NOT** refer to photo mode in history."""

        # Cam A
        self.DEF_CAM_SELFIE = """* **Mode A: Selfie Mode**
    * *Trigger (Must appear in current sentence):* “自拍”, “selfie”, “拿着手机”, “对镜自拍”.
    * *Camera Params:* `, (mirror selfie style:1.2), holding phone, looking at phone screen or mirror, (realistic screen light reflection on face), cute pose, close-up POV shot, (phone camera noise:1.1)`"""

        # Cam B
        self.DEF_CAM_FULLBODY = """* **Mode B: Full Body Shot**
    * *Trigger (Must appear in current sentence):* “全身照”, “看看穿搭”, “full body”, ”穿搭”.
    * *Camera Params:* `, full body shot, showing entire figure from head to toe, wide angle lens, far shot, (relaxed fashion pose:1.3), (shifting weight onto one leg), (casual stance), (slightly turned body), (one hand in pocket or touching hair), (natural movement snapshot), candid fashion photography, no phone, legs visible, shoes visible, (sharp focus on real person:1.4), (high dynamic range)`"""

        # Cam C (Fixed default)
        self.DEF_CAM_DEFAULT = """* **Mode C: Default/Half Body**
    * *Trigger:* **When NO keywords from Mode A or B appear in current input, FORCE this mode.**
    * *Camera Params:* `, upper body shot, medium close-up portrait, looking at camera, (dynamic random pose:1.2), (playful gestures:1.1), (expressive face), candid portrait, no phone, (detailed skin pores), (film grain:1.1)`"""

        # 1. 读取配置 (Fallback to default if empty)
        # Part 1 & 2
        p_char_vis = self.config.get("char_visuals") or self.DEF_CHAR_VISUALS
        p_char_id = self.config.get("char_identity") or self.DEF_CHAR_IDENTITY

        # Part 4
        p_env_def = self.config.get("env_default") or self.DEF_ENV_DEFAULT
        p_env_full = self.config.get("env_fullbody") or self.DEF_ENV_FULLBODY
        p_env_out = self.config.get("env_outdoor") or self.DEF_ENV_OUTDOOR

        # Part 6
        p_cam_selfie = self.config.get("cam_selfie") or self.DEF_CAM_SELFIE
        p_cam_full = self.config.get("cam_fullbody") or self.DEF_CAM_FULLBODY
        p_cam_def = self.DEF_CAM_DEFAULT

        # 2. 定义固定逻辑部分
        self.header_logic = """# 图像生成核心系统指令 (Optimized Core System Instructions) v3.7

## 0. 提示词构建逻辑 (Prompt Construction Logic)

**[Important] The final prompt MUST be constructed in this order:**
`[1. Character Visuals] + [2. User's Outfit & Action] + [4. Dynamic Environment & Style] + [6. Camera Parameters]`

---"""

        self.middle_logic = """---

## 3. 动态内容处理 (Handling User Input)

* **穿搭 (Outfit):** 用户未指定时，默认保持简洁风格或根据场景补全。
* **动作 (Action):** 自然融入用户描述的动作。如果动作/表情与核心设定的“sweet smile”冲突，**以用户要求为准**。

---"""

        self.footer_logic = """---

## 7. 交互行为准则 (Interaction Guidelines)

1.  **穿搭一致性:** 优先沿用上下文中已确定的服装设定。
2.  **特征校验:** 确保包含刘海、发色和核心身材描述。
3.  **模式不继承 (Mode Reset):** 摄影模式（自拍/全身/半身）**不具备记忆性**。每一次生成请求都视为一次新的拍摄。如果用户只说“发张照片”而没说“自拍”，必须回滚到 **Mode C (默认半身)**，严禁沿用上一轮的“自拍”设定。
"""

        # 3. 组装最终的 System Prompt
        self.full_prompt = (
            f"{self.header_logic}\n\n"
            f"{p_char_vis}\n\n"
            f"{p_char_id}\n\n"
            f"{self.middle_logic}\n\n"
            f"{self.ENV_HEADER}\n\n"
            f"{p_env_def}\n\n"
            f"{p_env_full}\n\n"
            f"{p_env_out}\n\n"
            f"{self.CAM_HEADER}\n\n"
            f"{p_cam_selfie}\n\n"
            f"{p_cam_full}\n\n"
            f"{p_cam_def}\n\n"
            f"{self.footer_logic}"
        )

        # 记录用户的剩余会话次数: user_id -> remaining_turns
        self.user_sessions = {}

    @filter.llm_tool(name="enter_portrait_mode")
    async def enter_portrait_mode(self, event: AstrMessageEvent):
        """
        Use this tool when the user wants to take a photo, selfie, check outfit, ask for a photography session, or record a visual moment.
        This tool activates the 'Portrait/Photographer Mode' for the bot.
        """
        user_id = event.get_sender_id()

        # 激活会话状态
        self.user_sessions[user_id] = self.max_turns
        logger.info(f"[Portrait] LLM 触发摄影师模式 (Tool Call)，用户 {user_id}，剩余轮数: {self.max_turns}")

        # 返回给 LLM 的 System Message，指导它接下来的行为
        return "Portrait Mode Activated. Please immediately adopt the persona of a professional photographer and describe the shot as per the System Instructions that will be injected."

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        在 LLM 请求发出前拦截，动态注入 System Prompt。
        逻辑：
        1. 检查 Session 计数器是否激活。
        2. 如果激活，注入 Prompt。
        """
        user_id = event.get_sender_id()

        # 检查是否需要注入
        if user_id in self.user_sessions:
            turns = self.user_sessions[user_id]
            if turns > 0:
                # 构造注入文本
                inject_text = f"\n\n<portrait_mode_instruction>\n{self.full_prompt}\n</portrait_mode_instruction>"

                # 修改请求中的 system_prompt
                if not req.system_prompt:
                    req.system_prompt = ""

                req.system_prompt += inject_text

                # 打印详细日志
                logger.debug(f"[LLM] 添加的内在状态注入：\n{inject_text}")

                # 递减计数
                self.user_sessions[user_id] -= 1
            else:
                # 归零清理
                del self.user_sessions[user_id]
                logger.info(f"[Portrait] 用户 {user_id} 摄影师模式结束")
