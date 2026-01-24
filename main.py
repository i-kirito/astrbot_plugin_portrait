from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest
import re

@register("astrbot_plugin_portrait", "ikirito", "Prompt注入器 (无摄影师人格)", "2.0.0")
class PortraitPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.max_turns = self.config.get("max_turns", 3)

        # 触发正则：检测画图/拍照意图
        self.trigger_regex = re.compile(
            r"(画|绘|生|造|搞|整|来|P|修|写).{0,10}(图|照|像|片)|"
            r"(拍|自).{0,10}(照|拍)|"
            r"(看|查|秀|显|露).{0,10}(穿搭|造型|样子|OOTD|脸)|"
            r"(美|帅|私)照|摄影|留念|记录.{0,10}(画面|瞬间)|"
            r"(photo|pic|image|draw|generate|capture|portrait|selfie|outfit)",
            re.IGNORECASE
        )

        # === 配置读取 (Fallback to default) ===
        # 注意：这里的默认值依然保留了原来的粉发少女设定，作为“参数库”

        self.DEF_CHAR_VISUALS = """[Visuals]\nThe subject is a young 18-year-old Asian girl with fair skin and delicate features. She has dusty rose pink hair featuring essential wispy air bangs. Her large, round, doll-like eyes are deep-set and natural dark brown. She possesses a slender hourglass figure with a tiny waist and a full bust, emphasizing a natural soft tissue silhouette."""

        self.DEF_CHAR_IDENTITY = """[Identity]\n* Face: 18 years old, doll-like large round eyes (dark brown).\n* Hair: Dusty rose pink, wispy air bangs.\n* Body: Slender hourglass figure."""

        self.ENV_HEADER = """[Environment Logic]"""
        self.DEF_ENV_DEFAULT = """* Scenario A (Bedroom): (indoors, cute girl's bedroom aesthetic:1.3), (kawaii style:1.2), (natural window light mixed with warm indoor lamps:1.3), (realistic light and shadow:1.2), (pastel pink and warm tones:1.1), (fairy lights on wall:1.1), bed filled with plushies"""
        self.DEF_ENV_FULLBODY = """* Scenario B (Dressing Room): (indoors, pink aesthetic dressing room:1.4), (bright sunlight streaming through sheer curtains:1.4), (volumetric lighting), (pastel pink and white tones:1.2), cozy, kawaii aesthetic"""
        self.DEF_ENV_OUTDOOR = """* Scenario C (Outdoor): Remove indoor description, use user specified scene, force add `(blurred background), (bokeh), (natural lighting)`."""

        self.CAM_HEADER = """[Camera Logic]"""
        self.DEF_CAM_SELFIE = """* Mode A (Selfie): (mirror selfie style:1.2), holding phone, looking at phone screen or mirror, (realistic screen light reflection on face), cute pose, close-up POV shot"""
        self.DEF_CAM_FULLBODY = """* Mode B (Full Body): full body shot, showing entire figure from head to toe, wide angle lens, far shot, (relaxed fashion pose:1.3), (shifting weight onto one leg)"""
        self.DEF_CAM_DEFAULT = """* Mode C (Default): upper body shot, medium close-up portrait, looking at camera, (dynamic random pose:1.2), candid portrait, (detailed skin pores), (film grain:1.1)"""

        # 读取用户配置
        p_char_vis = self.config.get("char_visuals") or self.DEF_CHAR_VISUALS
        p_char_id = self.config.get("char_identity") or self.DEF_CHAR_IDENTITY
        p_env_def = self.config.get("env_default") or self.DEF_ENV_DEFAULT
        p_env_full = self.config.get("env_fullbody") or self.DEF_ENV_FULLBODY
        p_env_out = self.config.get("env_outdoor") or self.DEF_ENV_OUTDOOR
        p_cam_selfie = self.config.get("cam_selfie") or self.DEF_CAM_SELFIE
        p_cam_full = self.config.get("cam_fullbody") or self.DEF_CAM_FULLBODY
        p_cam_def = self.DEF_CAM_DEFAULT

        # === 核心 Prompt 构建 (去人格化) ===
        self.header_logic = """# SYSTEM INJECTION: VISUAL CONTEXT DATA
IMPORTANT: The following data provides visual descriptors for the current character.
**IF and ONLY IF** you are generating an image or calling an image generation tool, you MUST include these visual descriptors in your prompt parameter.
DO NOT adopt a persona. DO NOT roleplay as a photographer. Continue acting as the default assistant, but ensure generated images match these specs."""

        self.full_prompt = (
            f"{self.header_logic}\n\n"
            f"--- CHARACTER VISUALS ---\n{p_char_vis}\n{p_char_id}\n\n"
            f"--- ENVIRONMENT SETTINGS ---\n{self.ENV_HEADER}\n{p_env_def}\n{p_env_full}\n{p_env_out}\n\n"
            f"--- CAMERA SETTINGS ---\n{self.CAM_HEADER}\n{p_cam_selfie}\n{p_cam_full}\n{p_cam_def}\n\n"
            f"--- END CONTEXT DATA ---"
        )

        self.user_sessions = {}

    @filter.llm_tool(name="enter_portrait_mode")
    async def enter_portrait_mode(self, event: AstrMessageEvent, user_intent: str):
        """
        Inject visual context data for image generation.
        Use this when the user wants to generate an image/photo.
        """
        user_id = event.get_sender_id()
        self.user_sessions[user_id] = self.max_turns
        logger.info(f"[Portrait] 激活 Prompt 注入 (Tool Call)，用户 {user_id}")
        return f"Visual context data injected. Please proceed with image generation using the provided character descriptors."

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        user_id = event.get_sender_id()
        msg_text = event.message_str
        should_inject = False

        if self.trigger_regex.search(msg_text):
            should_inject = True
            self.user_sessions[user_id] = self.max_turns
            logger.info(f"[Portrait] 正则命中，激活 Prompt 注入")
        elif user_id in self.user_sessions:
            should_inject = True

        if should_inject and self.user_sessions.get(user_id, 0) > 0:
            turns = self.user_sessions[user_id]

            # 1. System Prompt 注入
            injection = f"\n\n<visual_context_injection>\n{self.full_prompt}\n</visual_context_injection>"
            if not req.system_prompt: req.system_prompt = ""
            req.system_prompt += injection

            # 2. User Message 注入 (纯指令，无人格)
            if req.messages and len(req.messages) > 0:
                last_msg = req.messages[-1]
                if last_msg.role == "user":
                    # 提示 LLM：这是一个纯粹的数据引用要求
                    suffix = "\n\n(System Note: When generating images, you MUST strictly adhere to the <visual_context_injection> descriptors provided above for character appearance and style.)"

                    if isinstance(last_msg.content, str):
                        last_msg.content += suffix
                    elif isinstance(last_msg.content, list):
                        for item in last_msg.content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                item["text"] += suffix
                                break

            logger.debug(f"[LLM] Visual Prompt 已注入 (剩余 {turns} 轮)")
            self.user_sessions[user_id] -= 1
            if self.user_sessions[user_id] <= 0:
                del self.user_sessions[user_id]
