from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.provider.entities import ProviderRequest
import astrbot.api.message_components as Comp
import re
import copy
import random
from datetime import datetime

# 尝试导入定时任务库
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False
    logger.warning("[Portrait] apscheduler 未安装，主动拍照功能已禁用。可通过 pip install apscheduler 安装。")

@register("astrbot_plugin_portrait", "ikirito", "人物特征Prompt注入器,增强美化画图", "1.7.0")
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
2.  **If Drawing Intent Detected**: You MUST incorporate the Visual Data below into your image generation prompt.
3.  **Prompt Structure**: `[Character Visuals] + [User Action/Outfit] + [Environment] + [Camera]`"""

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

        # === v1.7.0: 主动拍照定时任务 ===
        self.scheduler = None
        self._setup_proactive_photo()

    def _setup_proactive_photo(self):
        """配置主动拍照定时任务"""
        if not HAS_APSCHEDULER:
            return

        # 检查是否启用
        enable_morning = self.config.get("proactive_morning", False)
        enable_noon = self.config.get("proactive_noon", False)
        enable_evening = self.config.get("proactive_evening", False)

        if not any([enable_morning, enable_noon, enable_evening]):
            logger.debug("[Portrait] 主动拍照功能未启用")
            return

        try:
            self.scheduler = AsyncIOScheduler()

            # 早安问候 (默认 8:00)
            if enable_morning:
                morning_time = self.config.get("proactive_morning_time", "08:00")
                hour, minute = morning_time.split(":")
                self.scheduler.add_job(
                    self._send_proactive_photo,
                    CronTrigger(hour=int(hour), minute=int(minute)),
                    id="proactive_morning",
                    args=["morning"]
                )
                logger.info(f"[Portrait] 已启动早安拍照任务，时间: {morning_time}")

            # 午间问候 (默认 12:00)
            if enable_noon:
                noon_time = self.config.get("proactive_noon_time", "12:00")
                hour, minute = noon_time.split(":")
                self.scheduler.add_job(
                    self._send_proactive_photo,
                    CronTrigger(hour=int(hour), minute=int(minute)),
                    id="proactive_noon",
                    args=["noon"]
                )
                logger.info(f"[Portrait] 已启动午间拍照任务，时间: {noon_time}")

            # 晚安问候 (默认 22:00)
            if enable_evening:
                evening_time = self.config.get("proactive_evening_time", "22:00")
                hour, minute = evening_time.split(":")
                self.scheduler.add_job(
                    self._send_proactive_photo,
                    CronTrigger(hour=int(hour), minute=int(minute)),
                    id="proactive_evening",
                    args=["evening"]
                )
                logger.info(f"[Portrait] 已启动晚安拍照任务，时间: {evening_time}")

            self.scheduler.start()

        except Exception as e:
            logger.error(f"[Portrait] 启动定时任务失败: {e}")

    async def _send_proactive_photo(self, period: str):
        """执行主动拍照并发送"""
        target_id = self.config.get("proactive_target_id")
        if not target_id:
            logger.warning("[Portrait] 未配置推送目标ID (proactive_target_id)，跳过主动拍照")
            return

        # 根据时段生成不同的问候语和拍照指令
        greetings = {
            "morning": [
                "早安～刚起床，还有点睡眼惺忪呢",
                "早上好！今天也要元气满满哦",
                "早～给你看看我刚醒来的样子"
            ],
            "noon": [
                "午安～中午休息一下",
                "吃午饭了吗？我也准备吃饭啦",
                "中午好～阳光正好呢"
            ],
            "evening": [
                "晚安～准备睡觉了",
                "今天辛苦了，晚安好梦",
                "夜深了，早点休息哦"
            ]
        }

        # 根据时段选择镜头模式
        camera_modes = {
            "morning": "selfie",  # 早上自拍
            "noon": "default",    # 中午半身照
            "evening": "selfie"   # 晚上自拍
        }

        greeting = random.choice(greetings.get(period, greetings["morning"]))
        camera_mode = camera_modes.get(period, "default")

        logger.info(f"[Portrait] 开始执行主动拍照 ({period})...")

        # 构建拍照请求消息
        photo_request = f"[系统指令] 请立即生成一张{period}时段的照片。问候语：{greeting}。镜头模式：{camera_mode}。"

        try:
            # 尝试调用 LLM 生成图片
            await self._request_llm_photo(target_id, greeting, camera_mode)
        except Exception as e:
            logger.error(f"[Portrait] 主动拍照失败: {e}")

    async def _request_llm_photo(self, target_id: str, greeting: str, camera_mode: str):
        """请求 LLM 生成照片并发送"""
        # 构建 Visual Context 增强的消息
        if camera_mode == "selfie":
            photo_prompt = f"{greeting}，帮我拍张自拍发给你～"
        else:
            photo_prompt = f"{greeting}，看看我现在的样子吧～"

        # 尝试通过平台发送消息（触发 LLM 响应）
        try:
            await self._send_to_target(target_id, photo_prompt)
            logger.info(f"[Portrait] 主动拍照消息已发送: {greeting[:20]}...")
        except Exception as e:
            logger.error(f"[Portrait] 发送主动拍照消息失败: {e}")

    async def _send_to_target(self, target_id: str, msg: str):
        """发送消息到指定目标"""
        platform_name = None
        user_id = target_id

        # 解析 platform:user_id 格式
        if ":" in target_id:
            platform_name, user_id = target_id.split(":", 1)

        logger.debug(f"[Portrait] 准备推送消息，目标: {target_id}")

        try:
            # 获取平台实例
            platforms = []
            if hasattr(self.context, 'platform_manager'):
                pm = self.context.platform_manager
                if hasattr(pm, 'get_insts'):
                    platforms = pm.get_insts()
                elif hasattr(pm, 'platforms'):
                    platforms = pm.platforms
                elif hasattr(pm, 'adapters'):
                    platforms = pm.adapters

            if not platforms:
                logger.error("[Portrait] 未找到任何平台实例")
                return False

            for platform in platforms:
                curr_platform_name = getattr(platform, "platform_name", str(platform))
                if platform_name and curr_platform_name != platform_name:
                    continue

                # 尝试获取底层 bot 客户端
                bot_client = None
                if hasattr(platform, 'get_client'):
                    bot_client = platform.get_client()
                elif hasattr(platform, 'client'):
                    bot_client = platform.client
                elif hasattr(platform, 'bot'):
                    bot_client = platform.bot

                # 尝试转换 ID 为整数
                try:
                    uid_int = int(user_id)
                except ValueError:
                    uid_int = None

                # 策略 1: 使用底层 call_action
                call_action = None
                if bot_client:
                    if hasattr(bot_client, 'call_action'):
                        call_action = bot_client.call_action
                    elif hasattr(bot_client, 'api') and hasattr(bot_client.api, 'call_action'):
                        call_action = bot_client.api.call_action

                if call_action and uid_int:
                    message_payload = [{"type": "text", "data": {"text": msg}}]

                    # 尝试发送群聊
                    try:
                        await call_action("send_group_msg", group_id=uid_int, message=message_payload)
                        logger.info(f"[Portrait] 群聊推送成功 (group_id={uid_int})")
                        return True
                    except Exception:
                        pass

                    # 尝试发送私聊
                    try:
                        await call_action("send_private_msg", user_id=uid_int, message=message_payload)
                        logger.info(f"[Portrait] 私聊推送成功 (user_id={uid_int})")
                        return True
                    except Exception:
                        pass

                # 策略 2: 使用 AstrBot 标准接口
                if hasattr(platform, "send_msg"):
                    chain = [Comp.Plain(msg)]
                    try:
                        await platform.send_msg(uid_int if uid_int else user_id, chain)
                        logger.info("[Portrait] 标准接口推送成功")
                        return True
                    except Exception as e:
                        logger.warning(f"[Portrait] 标准接口发送失败: {e}")

            logger.error(f"[Portrait] 所有尝试均失败，无法推送到目标: {target_id}")
            return False

        except Exception as e:
            logger.error(f"[Portrait] 推送消息致命错误: {e}")
            return False

    async def terminate(self):
        """插件卸载时清理"""
        if self.scheduler:
            self.scheduler.shutdown()
            logger.info("[Portrait] 已停止定时任务")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
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

        # 检测到绘图意图，执行单次注入
        injection = f"\n\n<portrait_status>\n{self.full_prompt}\n</portrait_status>"
        if not req.system_prompt:
            req.system_prompt = ""
        req.system_prompt += injection

        logger.info(f"[Portrait] Visual Context 已注入 (One-Shot Mode) - 触发词: {user_message[:30]}...")
