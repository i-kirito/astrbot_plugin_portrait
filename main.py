from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest
import re
import copy

@register("astrbot_plugin_portrait", "ikirito", "Prompt注入器 (无摄影师人格)", "2.2.1")
class PortraitPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        # [单次注入逻辑]
        # 触发正则：检测画图/拍照意图
        # 只要用户的话里沾边，就立刻注入，确保覆盖到画图工具的调用
        self.trigger_regex = re.compile(
            r"(画|绘|生|造|搞|整|来|P|修|写).{0,10}(图|照|像|片)|"
            r"(拍|自).{0,10}(照|拍)|"
            r"(看|查|秀|显|露).{0,10}(穿搭|造型|样子|OOTD|脸)|"
            r"(美|帅|私)照|摄影|留念|记录.{0,10}(画面|瞬间)|"
            r"(photo|pic|image|draw|generate|capture|portrait|selfie|outfit)",
            re.IGNORECASE
        )

        # === 配置读取 (Fallback to default) ===
        # 简化版: 纯文本拼接
        self.DEF_CHAR_IDENTITY = """The subject is a young 18-year-old Asian girl with fair skin and delicate features.
She has dusty rose pink hair featuring essential wispy air bangs.
Her large, round, doll-like eyes are deep-set and natural dark brown.
She possesses a slender hourglass figure with a tiny waist and a full bust."""

        self.DEF_ENV_DEFAULT = """(indoors, cute girl's bedroom aesthetic:1.3), (kawaii style:1.2), (natural window light mixed with warm indoor lamps:1.3), (realistic light and shadow:1.2), (pastel pink and warm tones:1.1), (fairy lights on wall:1.1), bed filled with plushies"""
        self.DEF_ENV_FULLBODY = """(indoors, pink aesthetic dressing room:1.4), (bright sunlight streaming through sheer curtains:1.4), (volumetric lighting), (pastel pink and white tones:1.2), cozy, kawaii aesthetic"""
        self.DEF_ENV_OUTDOOR = """Remove indoor description, use user specified scene, force add `(blurred background), (bokeh), (natural lighting)`."""

        self.DEF_CAM_SELFIE = """(mirror selfie style:1.2), holding phone, looking at phone screen or mirror, (realistic screen light reflection on face), cute pose, close-up POV shot"""
        self.DEF_CAM_FULLBODY = """full body shot, showing entire figure from head to toe, wide angle lens, far shot, (relaxed fashion pose:1.3), (shifting weight onto one leg)"""
        self.DEF_CAM_DEFAULT = """upper body shot, medium close-up portrait, looking at camera, (dynamic random pose:1.2), candid portrait, (detailed skin pores), (film grain:1.1)"""

        # 读取用户配置
        p_char_id = self.config.get("char_identity") or self.DEF_CHAR_IDENTITY
        p_env_def = self.config.get("env_default") or self.DEF_ENV_DEFAULT
        p_env_full = self.config.get("env_fullbody") or self.DEF_ENV_FULLBODY
        p_env_out = self.config.get("env_outdoor") or self.DEF_ENV_OUTDOOR
        p_cam_selfie = self.config.get("cam_selfie") or self.DEF_CAM_SELFIE
        p_cam_full = self.config.get("cam_fullbody") or self.DEF_CAM_FULLBODY
        p_cam_def = self.DEF_CAM_DEFAULT

        # === 核心 Prompt 构建 ===
        self.header_logic = """# 人物特征生成图像增强指令
IMPORTANT: The following data provides visual descriptors for the current character.
**IF and ONLY IF** you are generating an image or calling an image generation tool, you MUST include these visual descriptors in your prompt parameter."""

        self.full_prompt = (
            f"{self.header_logic}\n\n"
            f"{p_char_id}\n\n"
            f"{p_env_def}\n{p_env_full}\n{p_env_out}\n\n"
            f"{p_cam_selfie}\n{p_cam_full}\n{p_cam_def}\n\n"
            f"--- [上述状态仅供需要时参考，无需主动提及] ---"
        )

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        user_id = event.get_sender_id()
        msg_text = event.message_str
        should_inject = False

        # 唯一触发逻辑：正则判定
        if self.trigger_regex.search(msg_text):
            should_inject = True
            logger.info(f"[Portrait] 正则命中，单次注入激活")

        if should_inject:
            # 1. System Prompt 注入
            injection = f"\n\n<portrait_status>\n{self.full_prompt}\n</portrait_status>"
            if not req.system_prompt: req.system_prompt = ""
            req.system_prompt += injection

            # 2. User Message 注入 (尝试性)
            if hasattr(req, "messages") and req.messages and len(req.messages) > 0:
                # 构建新的消息列表，避免修改原始列表引用
                new_messages = list(req.messages)

                # deepcopy 最后一条消息，防止修改原始对象
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

                    # 更新新列表中的最后一条消息
                    new_messages[-1] = last_msg

                    # 将 request 的 messages 指向新列表
                    req.messages = new_messages
            else:
                logger.debug(f"[Portrait] ProviderRequest 无 messages 属性，跳过 User Message 注入")

            logger.debug(f"[LLM] Visual Prompt 已单次注入")
