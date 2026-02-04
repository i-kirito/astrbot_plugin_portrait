"""默认配置常量"""

# 默认角色外貌描述
DEF_CHAR_IDENTITY = """> **The subject is a young 18-year-old Asian girl with fair skin and delicate features. She has dusty rose pink hair featuring essential wispy air bangs. Her large, round, doll-like eyes are deep-set and natural dark brown. She possesses a slender hourglass figure with a tiny waist and a full bust, emphasizing a natural soft tissue silhouette.**"""

# 默认环境场景配置
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

# 默认摄影模式配置
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

# Prompt 模板常量
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
