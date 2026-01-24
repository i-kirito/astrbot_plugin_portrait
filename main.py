from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest
import re
import copy

@register("astrbot_plugin_portrait", "ikirito", "人物特征Prompt注入器,增强美化画图", "1.2.2")
class PortraitPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        # [Trigger Regex]
        # 涵盖：画图、拍照、自拍、OOTD、看看/长啥样/在干嘛等日常询问
        self.trigger_regex = re.compile(
            r"(画|绘|生|造|搞|整|来|P|修|写|发|给|爆).{0,10}(图|照|像|片)|"
            r"(拍|自).{0,10}(照|拍)|"
            r"(看|查|秀|显|露|瞧|康).{0,10}(穿搭|造型|样子|OOTD|脸|你|我|私)|"
            r"(美|帅|私)照|摄影|留念|记录.{0,10}(画面|瞬间)|"
            r"(长|长得).{0,5}(啥|什么)样|"
            r"(在|干).{0,5}(干|做|忙).{0,5}(嘛|什么|啥)|"
            r"(photo|pic|image|draw|generate|capture|portrait|selfie|outfit)",
            re.IGNORECASE
        )

        # === 默认内容 (Content Only) ===
        self.DEF_CHAR_IDENTITY = """> **The subject is a young 18-year-old Asian girl with fair skin and delicate features. She has dusty rose pink hair featuring essential wispy air bangs. Her large, round, doll-like eyes are deep-set and natural dark brown. She possesses a slender hourglass figure with a tiny waist and a full bust, emphasizing a natural soft tissue silhouette.**"""

        self.DEF_ENV_A = """(indoors, cute girl's bedroom aesthetic:1.3), (kawaii style:1.2), (natural window light mixed with warm indoor lamps:1.3), (realistic light and shadow:1.2), (pastel pink and warm tones:1.1), (fairy lights on wall:1.1), bed filled with plushies, (shelves with anime figures:1.2), gaming setup background, cozy atmosphere, clear background details, (raw photo:1.2), (authentic skin texture:1.2), photorealistic"""

        self.DEF_ENV_B = """(indoors, pink aesthetic dressing room:1.4), (bright sunlight streaming through sheer curtains:1.4), (volumetric lighting), (shadows casting on floor:1.2), (white vanity table with large mirror), (pink fluffy stool), (white shelves filled with plush toys and pink accessories), (pink clothing rack with dresses), (pink utility cart), (pink curtains), (pink fluffy rugs), (pastel pink and white tones:1.2), cozy, kawaii aesthetic, (reflection in vanity mirror is blurred and indistinct:1.5), (focus away from reflection), (raw photo:1.2), (realistic texture:1.3), photorealistic"""

        self.DEF_ENV_C = """Ignore the bedroom/dressing room prompts above. Analyze the user's request (e.g., "in the park", "at the beach") or the current chat context/itinerary. Generate a scene description that matches the requested location. Force add: `(blurred background), (bokeh), (natural lighting)`."""

        self.DEF_CAM_A = """, (mirror selfie style:1.2), holding phone, looking at phone screen or mirror, (realistic screen light reflection on face), cute pose, close-up POV shot, (phone camera noise:1.1)"""

        self.DEF_CAM_B = """, full body shot, showing entire figure from head to toe, wide angle lens, far shot, (relaxed fashion pose:1.3), (shifting weight onto one leg), (casual stance), (slightly turned body), (one hand in pocket or touching hair), (natural movement snapshot), candid fashion photography, no phone, legs visible, shoes visible, (sharp focus on real person:1.4), (high dynamic range)"""

        self.DEF_CAM_C = """, upper body shot, medium close-up portrait, looking at camera, (dynamic random pose:1.2), (playful gestures:1.1), (expressive face), candid portrait, no phone, (detailed skin pores), (film grain:1.1)"""

        # === 模板结构 (Template Structure) ===
        self.TPL_HEADER = """# 图像生成核心系统指令 (Optimized Core System Instructions)
## 0. 提示词构建逻辑 (Prompt Construction Logic)
**[Important] The final prompt MUST be constructed in this order:**
`[1. Character Visuals] + [2. User's Outfit & Action] + [3. Dynamic Environment & Style] + [4. Camera Parameters]`"""

        self.TPL_CHAR = """## 1. 角色视觉核心 (Character Visuals) - [不可变前缀]
**Block 1 (Always Start with):**
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
    * *场景:* **粉色梦幻更衣室 (Pink Dressing Room)**。
    * *Prompt Block:*
    > **{env_b}**

* **Scenario C: 户外/特定场景 (User Specified)**
    * *操作:* {env_c}"""

        self.TPL_CAM = """## 4. 摄影模式切换 (Photo Format Switching) - [强制重置逻辑]
**指令:** 检查**当前用户输入 (Current Input)** 中的关键词。**不要**参考历史记录中的摄影模式。
* **模式 A：自拍 (Selfie Mode)**
    * *触发 (必须在当前句中出现):* “自拍”、“selfie”、“拿着手机”、“对镜自拍”。
    * *Camera Params:* `{cam_a}`

* **模式 B：全身照 (Full Body Shot)**
    * *触发 (必须在当前句中出现):* “全身照”、“看看穿搭”、“full body”、”穿搭”。
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

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        user_id = event.get_sender_id()
        msg_text = event.message_str
        should_inject = False
        is_debug = False

        # 检查是否为调试模式 (以 # 开头)
        if msg_text.startswith("#"):
            is_debug = True
            # 去除前缀用于正则匹配
            match_text = msg_text.lstrip("#").strip()
        else:
            match_text = msg_text

        if self.trigger_regex.search(match_text):
            should_inject = True
            logger.info(f"[Portrait] 正则命中，单次注入激活 (Debug: {is_debug})")

        if should_inject:
            # 1. 生成完整 Prompt
            # ... (System Prompt 注入逻辑)
            injection = f"\n\n<portrait_status>\n{self.full_prompt}\n</portrait_status>"

            if is_debug:
                # 调试模式：拦截请求，直接返回生成的 Prompt 供检查
                # 我们通过修改 User Message 让 LLM 复述出来，或者直接在日志看到
                logger.info(f"[Portrait DEBUG] Generated Prompt:\n{self.full_prompt}")

                # 构造一个让 LLM 复述的请求
                debug_msg = f"【调试模式】插件已成功触发。\n\n生成的 System Prompt 内容如下（请直接输出）：\n\n```\n{self.full_prompt}\n```"

                # 清空原有的 System Prompt 以免干扰 (可选)
                req.system_prompt = ""

                # 替换消息列表，强制 LLM 输出 Prompt
                req.messages = [{
                    "role": "user",
                    "content": debug_msg
                }]
                return

            # 正常模式：注入 System Prompt
            if not req.system_prompt: req.system_prompt = ""
            req.system_prompt += injection

            # 2. User Message 注入
            if hasattr(req, "messages") and req.messages and len(req.messages) > 0:
                new_messages = list(req.messages)
                last_msg = copy.deepcopy(new_messages[-1])

                if last_msg.role == "user":
                    suffix = "\n\n(System Note: When generating images, you MUST strictly adhere to the <portrait_status> descriptors provided above for character appearance and style.)"
                    if isinstance(last_msg.content, str):
                        last_msg.content += suffix
                    elif isinstance(last_msg.content, list):
                        for item in last_msg.content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                item["text"] += suffix
                                break
                    new_messages[-1] = last_msg
                    req.messages = new_messages
            else:
                logger.debug(f"[Portrait] ProviderRequest 无 messages 属性，跳过 User Message 注入")

            logger.debug(f"[LLM] Visual Prompt 已单次注入")
