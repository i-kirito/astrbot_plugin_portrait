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
        # 从配置读取注入轮次，默认为 1（单次注入）
        self.injection_rounds = max(1, self.config.get("injection_rounds", 1))

        # === v1.7.0: 主动拍照定时任务 ===
        self.scheduler = None
        self._scheduler_started = False
        # 延迟启动 scheduler，因为 __init__ 时事件循环可能未准备好
        self._init_scheduler_jobs()

    def _init_scheduler_jobs(self):
        """初始化定时任务配置（不启动 scheduler）"""
        if not HAS_APSCHEDULER:
            logger.warning("[Portrait] apscheduler 未安装，定时推送功能不可用")
            return

        # 检查是否启用
        if not self.config.get("proactive_enabled", False):
            logger.debug("[Portrait] 定时推送功能未启用")
            return

        # 检查目标列表
        target_list = self.config.get("proactive_target_list", [])
        if not target_list:
            logger.warning("[Portrait] 定时推送已启用，但未配置目标群组列表")
            return

        # 获取推送时间
        push_times = self.config.get("proactive_time", "08:00,22:00")
        self._scheduled_times = [t.strip() for t in push_times.split(",") if t.strip()]

        if self._scheduled_times:
            logger.info(f"[Portrait] 定时推送已配置，共 {len(self._scheduled_times)} 个时间点: {', '.join(self._scheduled_times)}")

    def _ensure_scheduler_started(self):
        """确保 scheduler 已启动（在事件循环运行后调用）"""
        if self._scheduler_started or not HAS_APSCHEDULER:
            return

        if not self.config.get("proactive_enabled", False):
            return

        if not hasattr(self, '_scheduled_times') or not self._scheduled_times:
            return

        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger

            self.scheduler = AsyncIOScheduler()

            for idx, time_str in enumerate(self._scheduled_times):
                try:
                    hour, minute = time_str.split(":")
                    hour_int = int(hour)

                    # 根据配置的时间判断时段
                    if 5 <= hour_int < 12:
                        time_period = "morning"
                    elif 12 <= hour_int < 18:
                        time_period = "afternoon"
                    else:
                        time_period = "evening"

                    self.scheduler.add_job(
                        self._send_proactive_photo,
                        CronTrigger(hour=hour_int, minute=int(minute)),
                        args=[time_period],
                        id=f"proactive_push_{idx}"
                    )
                    logger.info(f"[Portrait] 已添加定时推送任务 #{idx+1}，时间: {time_str} ({time_period})")
                except ValueError:
                    logger.warning(f"[Portrait] 时间格式错误，跳过: {time_str}")

            self.scheduler.start()
            self._scheduler_started = True
            logger.info(f"[Portrait] 定时推送 scheduler 已启动")

        except Exception as e:
            logger.error(f"[Portrait] 启动定时任务失败: {e}")
            import traceback
            traceback.print_exc()

    async def _send_proactive_photo(self, time_period: str = None):
        """执行主动拍照并发送到所有目标

        Args:
            time_period: 时段标识 (morning/afternoon/evening)，由定时任务传入
        """
        target_list = self.config.get("proactive_target_list", [])
        if not target_list:
            logger.warning("[Portrait] 未配置推送目标列表，跳过主动拍照")
            return

        # 根据传入的时段或当前时间确定时段
        if not time_period:
            hour = datetime.now().hour
            if 5 <= hour < 12:
                time_period = "morning"
            elif 12 <= hour < 18:
                time_period = "afternoon"
            else:
                time_period = "evening"

        # 时段对应的问候语（仅问候，不要求回复）
        period_greetings = {
            "morning": ["早安～", "早上好呀～", "新的一天开始啦～", "起床啦～"],
            "afternoon": ["下午好～", "午安～", "下午茶时间～"],
            "evening": ["晚安～", "晚上好呀～", "睡前问候～", "今天辛苦了～"]
        }

        greeting = random.choice(period_greetings.get(time_period, period_greetings["evening"]))

        logger.info(f"[Portrait] 开始执行定时推送 ({time_period})，目标数: {len(target_list)}")

        # 向所有目标生成图片并发送
        for target_id in target_list:
            try:
                await self._generate_and_send_photo(str(target_id), greeting, time_period)
            except Exception as e:
                logger.error(f"[Portrait] 推送到 {target_id} 失败: {e}")
                import traceback
                traceback.print_exc()

    async def _generate_and_send_photo(self, target_id: str, greeting: str, time_period: str):
        """生成图片并发送到目标群组

        使用 tool_loop_agent 调用 LLM 生成图片，然后直接发送到群组
        """
        try:
            # 随机选择拍照模式（自拍/全身/半身）
            photo_modes = [
                ("自拍", "拍一张对镜自拍"),
                ("全身照", "拍一张全身穿搭照"),
                ("半身照", "拍一张半身照")
            ]
            mode_name, mode_desc = random.choice(photo_modes)

            # 根据时段构建场景描述
            time_scenes = {
                "morning": "早晨刚起床，阳光透过窗帘",
                "afternoon": "下午休闲时光，温馨惬意",
                "evening": "晚上，柔和的灯光"
            }
            scene = time_scenes.get(time_period, time_scenes["evening"])

            # 构建绘图提示词
            draw_prompt = f"请{mode_desc}，场景是{scene}的样子。"

            logger.info(f"[Portrait] 推送模式: {mode_name}，提示词: {draw_prompt}")

            # 尝试使用 tool_loop_agent 调用 LLM
            llm_response = None
            try:
                # 获取当前 Provider ID
                provider_id = None
                if hasattr(self.context, 'get_using_provider'):
                    provider = self.context.get_using_provider()
                    if provider and hasattr(provider, 'provider_id'):
                        provider_id = provider.provider_id

                if hasattr(self.context, 'tool_loop_agent'):
                    # 使用 tool_loop_agent 调用 LLM（带工具支持）
                    llm_response = await self.context.tool_loop_agent(
                        event=None,  # 无事件触发
                        chat_provider_id=provider_id,
                        prompt=draw_prompt,
                        system_prompt=self.full_prompt,  # 注入 Visual Context
                        max_steps=10,
                        tool_call_timeout=120
                    )
                    logger.info(f"[Portrait] LLM 生成完成: {type(llm_response)}")
            except Exception as e:
                logger.warning(f"[Portrait] tool_loop_agent 调用失败: {e}")
                llm_response = None

            # 获取平台并发送消息
            await self._send_message_to_target(target_id, greeting, llm_response)

        except Exception as e:
            logger.error(f"[Portrait] _generate_and_send_photo 异常: {e}")
            import traceback
            traceback.print_exc()

    async def _send_message_to_target(self, target_id: str, greeting: str, llm_response=None):
        """发送消息到目标群组"""
        try:
            # 获取平台管理器
            platforms = []
            if hasattr(self.context, 'platform_manager'):
                pm = self.context.platform_manager
                if hasattr(pm, 'get_insts'):
                    platforms = pm.get_insts()
                elif hasattr(pm, 'platforms'):
                    platforms = pm.platforms
                elif hasattr(pm, 'insts'):
                    platforms = pm.insts

            if not platforms:
                logger.error("[Portrait] 未找到任何平台实例")
                return False

            # 解析 target_id
            platform_filter = None
            user_id = target_id
            if ":" in target_id:
                platform_filter, user_id = target_id.split(":", 1)

            try:
                uid_int = int(user_id)
            except ValueError:
                uid_int = None

            for platform in platforms:
                platform_name = getattr(platform, "platform_name", None) or getattr(platform, "name", str(platform))

                if platform_filter and platform_name != platform_filter:
                    continue

                # 尝试获取底层客户端
                client = None
                for attr in ['client', 'bot', '_client', '_bot']:
                    if hasattr(platform, attr):
                        client = getattr(platform, attr)
                        if client:
                            break

                if client and uid_int:
                    # 尝试 call_action (OneBot 协议)
                    call_action = None
                    if hasattr(client, 'call_action'):
                        call_action = client.call_action
                    elif hasattr(client, 'api') and hasattr(client.api, 'call_action'):
                        call_action = client.api.call_action

                    if call_action:
                        # 构建消息：问候语 + LLM 返回内容（如果有图片）
                        message_parts = []

                        # 添加问候语
                        message_parts.append({"type": "text", "data": {"text": greeting}})

                        # 如果 LLM 返回了内容，尝试解析图片
                        if llm_response:
                            response_text = ""
                            if hasattr(llm_response, 'completion_text'):
                                response_text = llm_response.completion_text or ""
                            elif isinstance(llm_response, str):
                                response_text = llm_response

                            # 尝试提取图片 URL
                            import re
                            img_urls = re.findall(r'https?://[^\s\]\)]+\.(?:jpg|jpeg|png|gif|webp)', response_text, re.IGNORECASE)
                            for url in img_urls[:1]:  # 只取第一张图
                                message_parts.append({"type": "image", "data": {"url": url}})
                                logger.info(f"[Portrait] 提取到图片 URL: {url}")

                        # 尝试群聊发送
                        try:
                            await call_action("send_group_msg", group_id=uid_int, message=message_parts)
                            logger.info(f"[Portrait] 定时推送成功 (群 {uid_int})")
                            return True
                        except Exception as e1:
                            logger.debug(f"[Portrait] 群聊发送失败: {e1}")

                        # 尝试私聊发送
                        try:
                            await call_action("send_private_msg", user_id=uid_int, message=message_parts)
                            logger.info(f"[Portrait] 定时推送成功 (私聊 {uid_int})")
                            return True
                        except Exception as e2:
                            logger.debug(f"[Portrait] 私聊发送失败: {e2}")

            logger.warning(f"[Portrait] 无法推送到目标: {target_id}")
            return False

        except Exception as e:
            logger.error(f"[Portrait] _send_message_to_target 异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def terminate(self):
        """插件卸载时清理"""
        if self.scheduler:
            self.scheduler.shutdown()
            logger.info("[Portrait] 已停止定时任务")

    @filter.command("拍照推送")
    async def cmd_push_photo(self, event: AstrMessageEvent):
        """立即推送一次拍照到当前会话 - 触发 LLM 调用绘图工具"""
        # 根据时间选择问候语
        hour = datetime.now().hour
        if 5 <= hour < 12:
            greetings = [
                "早上好，发张自拍",
                "早安，看看穿搭",
                "早上好，发张照片"
            ]
        elif 12 <= hour < 18:
            greetings = [
                "下午好，发张自拍",
                "午安，发张照片",
                "下午好，看看穿搭"
            ]
        else:
            greetings = [
                "晚上好，发张自拍",
                "晚安，发张照片",
                "晚上好，看看穿搭"
            ]

        photo_prompt = random.choice(greetings)

        # 使用 request_llm 触发完整的 LLM 流程（包括工具调用）
        # Visual Context 会通过 on_llm_request hook 自动注入
        yield event.request_llm(
            prompt=photo_prompt,
            func_tool_manager=self.context.get_llm_tool_manager(),
            session_id=event.unified_msg_origin
        )
        logger.info(f"[Portrait] 手动推送拍照已触发 LLM 流程")

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
