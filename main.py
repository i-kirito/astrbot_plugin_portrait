from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest
import re
import copy

@register("astrbot_plugin_portrait", "ikirito", "人物特征Prompt注入器,增强美化画图", "2.5.0")
class PortraitPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        # [Trigger Regex]
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

        # === 默认内容 v4.0 (Content Only) ===
        self.DEF_CHAR_BASE = """> (18 year old Asian girl:1.5), (dusty rose pink hair:1.3), (essential wispy air bangs:1.4), (large round dark brown eyes:1.2), (sweet smile:1.2), slender hourglass figure, tiny waist, full bust, natural soft tissue silhouette, (korean idol makeup:1.1), (high ponytail OR twin tails:1.2)
*   注意: 仅当场景为“睡觉/刚醒”时，发型才改为 `loose hair, messy hair`，否则强制使用 `high ponytail` 或 `twin tails`。"""

        self.DEF_SCENE_BEDROOM = """> , (indoors, cute girl's bedroom aesthetic:1.3), (kawaii style:1.2), (natural window light mixed with warm indoor lamps:1.3), (realistic light and shadow:1.2), (pastel pink and warm tones), (fairy lights on wall), background filled with plushies and anime figures, (raw photo:1.2), photorealistic"""

        self.DEF_SCENE_DRESSING = """> , (indoors, luxury pink dressing room:1.4), (bright sunlight from sheer curtains:1.4), (volumetric lighting), (white vanity table with mirror), (clothing rack with dresses), (pastel pink and white tones:1.2), (blurred reflection in mirror), (raw photo:1.2), photorealistic"""

        self.DEF_SCENE_CUSTOM = """> , (background set in [INSERT USER LOCATION]:1.5), (blurred background), (bokeh), (natural outdoor lighting), (scenic view), photorealistic"""

        self.DEF_MODE_SELFIE = """> , (mirror selfie style:1.3), holding phone, looking at phone screen, (phone visible in hand), cute pose, close-up POV shot, (impertections:1.1)"""

        self.DEF_MODE_FULLBODY = """> , full body shot, showing from head to shoes, wide angle lens, (standing pose:1.2), (one leg forward), (fashion photography style), legs visible, shoes visible, (sharp focus on character)"""

        self.DEF_MODE_PORTRAIT = """> , upper body shot, medium close-up, looking at camera, (dynamic random pose), (candid shot), high quality portrait, (detailed skin texture:1.2)"""

        # === 模板结构 v4.0 (Template Structure) ===
        self.TPL_HEADER = """# 图像生成核心系统指令 (Optimized Core System Instructions) v4.0 (Strict Mode)
## 0. 核心执行协议 (Execution Protocol) - CRITICAL
当需要生成图像时，**严禁**直接编写描述。必须严格按照以下 [4步组装法] 拼接字符串，不增加任何额外修饰词。最终 Prompt 结构公式：
Final_Prompt = [1. Character_Base] + [2. User_Variable] + [3. Scenario_Logic] + [4. Camera_Logic]"""

        self.TPL_STEP_1 = """## 1. 固定变量 [Character_Base] (不可更改)
{content}"""

        self.TPL_STEP_2 = """## 2. 用户变量 [User_Variable] (动态填充)
*   Outfit: 提取用户描述的服装。若无描述，填入: (white oversized t-shirt:1.1), (casual shorts), comfortable home wear.
*   Action: 提取用户描述的动作。若无描述，填入: (looking at viewer), (sweet engaging smile).
    *   *冲突处理:* 若用户指定动作（如“哭泣”、“生气”），则覆盖核心设定的“sweet smile”。"""

        self.TPL_STEP_3 = """## 3. 场景逻辑 [Scenario_Logic] (三选一)
根据用户意图，必须且只能选择下列 ONE 个场景块拼接到 Prompt 中：
*   IF (默认/无特定地点): -> [Scene_Bedroom]
    {scene_bedroom}
*   IF (用户提及"穿搭/全身/照镜子"): -> [Scene_DressingRoom]
    {scene_dressing}
*   IF (用户明确指定地点, e.g., "在海边", "去公园"): -> [Scene_Custom]
    {scene_custom}"""

        self.TPL_STEP_4 = """## 4. 镜头逻辑 [Camera_Logic] (三选一)
根据关键词判断，必须且只能选择下列 ONE 个参数块：
*   IF (关键词: "自拍", "selfie", "手机"): -> [Mode_Selfie]
    {mode_selfie}
*   IF (关键词: "全身", "full body", "穿搭", "鞋"): -> [Mode_FullBody]
    {mode_fullbody}
*   IF (其他所有情况/默认): -> [Mode_Portrait]
    {mode_portrait}"""

        self.TPL_FOOTER = """## 5. 输出指令 (Output Instruction for Tool Use)
调用画图插件时，仅发送拼接好的英文 Prompt 字符串。不要添加 "Here is a prompt based on..." 等任何对话前缀。"""

        # 读取用户配置
        p_char_base = self.config.get("char_identity") or self.DEF_CHAR_BASE
        p_scene_bed = self.config.get("env_default") or self.DEF_SCENE_BEDROOM
        p_scene_dress = self.config.get("env_fullbody") or self.DEF_SCENE_DRESSING
        p_scene_cust = self.config.get("env_outdoor") or self.DEF_SCENE_CUSTOM
        p_mode_selfie = self.config.get("cam_selfie") or self.DEF_MODE_SELFIE
        p_mode_full = self.config.get("cam_fullbody") or self.DEF_MODE_FULLBODY
        # cam_portrait 暂不暴露配置，使用默认
        p_mode_port = self.DEF_MODE_PORTRAIT

        # === 核心 Prompt 组装 ===
        self.full_prompt = (
            f"{self.TPL_HEADER}\n\n"
            f"{self.TPL_STEP_1.format(content=p_char_base)}\n\n"
            f"{self.TPL_STEP_2}\n\n"
            f"{self.TPL_STEP_3.format(scene_bedroom=p_scene_bed, scene_dressing=p_scene_dress, scene_custom=p_scene_cust)}\n\n"
            f"{self.TPL_STEP_4.format(mode_selfie=p_mode_selfie, mode_fullbody=p_mode_full, mode_portrait=p_mode_port)}\n\n"
            f"{self.TPL_FOOTER}\n\n"
            f"--- END CONTEXT DATA ---"
        )

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        user_id = event.get_sender_id()
        msg_text = event.message_str
        should_inject = False

        if self.trigger_regex.search(msg_text):
            should_inject = True
            logger.info(f"[Portrait] 正则命中，单次注入激活")

        if should_inject:
            # 1. System Prompt 注入
            injection = f"\n\n<portrait_status>\n{self.full_prompt}\n</portrait_status>"
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
