"""默认配置常量 - 仅供参考的示例配置"""

# 默认环境场景配置（示例）
DEFAULT_ENVIRONMENTS = [
    {
        "name": "默认室内",
        "keywords": ["default"],
        "prompt": "indoors, cozy atmosphere with natural lighting, realistic setting"
    },
    {
        "name": "户外",
        "keywords": ["户外", "outdoor", "outside"],
        "prompt": "outdoors, natural daylight, softly blurred background with bokeh effect"
    }
]

# 默认摄影模式配置（示例）
DEFAULT_CAMERAS = [
    {
        "name": "半身/默认",
        "keywords": ["default"],
        "prompt": "upper body shot, medium close-up portrait, looking at camera"
    },
    {
        "name": "全身",
        "keywords": ["全身", "full body"],
        "prompt": "full body shot, head to toe visible, wide angle"
    }
]

# Prompt 模板常量
TPL_HEADER = """# Visual Context Injection (System Override)
## 0. Draw Tool Rules
1. If drawing intent is detected, call `portrait_draw_image` tool.
2. Do not output text in the same response when calling tools.
3. Call the tool only once per user request.

## 1. Prompt Format (Strict)
- Build ONE English prompt with exactly five parts:
  [Character] + [Outfit] + [Action/Expression] + [Scene/Lighting] + [Camera]
- MUST preserve the full character identity details provided in "Character Visuals (Fixed Identity)".
  Do not summarize or drop clauses unless they are contradictory to the user request.
- MUST include the runtime-selected environment and camera hints verbatim when provided.
- Do NOT use weighted tags like `(tag:1.3)`.

## 2. Example
"The subject is a young Asian girl with dusty rose pink hair, wearing a fitted black square-neck short dress, taking a mirror selfie with a playful smile in a cozy pastel bedroom with soft morning sunlight, close-up mirror selfie shot."""

TPL_CHAR = """## 1. Character Visuals (Fixed Identity)
**Core Appearance (Always Active):**
{content}"""

TPL_MIDDLE = """## 2. 动态内容处理 (Handling User Input)
* **穿搭 (Outfit):** 用户未指定时，默认保持简洁风格或根据场景补全。
* **表情 (Expression):** 根据场景补全，参考图片时表情尽可能随机性。
* **动作 (Action):** 根据场景补全或自然融入用户描述的动作。如果动作/表情与核心设定的冲突，**以用户要求为准**
"""

TPL_FOOTER = """---"""
