"""
签到卡片渲染模块
使用 Pillow 生成签到结果图片
"""

import os
import io
import base64
import logging
from datetime import datetime
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("astrbot")


class SignCardRenderer:
    """签到卡片渲染器"""

    def __init__(self, assets_dir: str):
        self.assets_dir = assets_dir
        self.font_path = self._find_font()
        self.mono_font_path = self._find_mono_font()

        # 卡片尺寸
        self.width = 800
        self.height = 400

        # 颜色配置
        self.bg_color = (255, 255, 255)  # 白色背景
        self.text_color = (80, 80, 80)  # 深灰色文字
        self.highlight_color = (255, 180, 0)  # 金黄色高亮
        self.success_color = (100, 180, 100)  # 绿色成功
        self.bonus_color = (255, 100, 100)  # 红色奖励

    def _find_font(self) -> tuple:
        """查找可用的中文粗体字体，返回 (路径, 字体索引)"""
        # 常见中文粗体字体路径 (路径, 索引)
        # PingFang.ttc: 0=Regular, 1=Medium, 2=Semibold
        font_paths = [
            # macOS 粗体
            ("/System/Library/Fonts/PingFang.ttc", 2),  # Semibold
            ("/System/Library/Fonts/Supplemental/Songti.ttc", 1),  # Bold
            ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
            # Linux 粗体
            ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
            ("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", 0),
            # Windows 粗体
            ("C:/Windows/Fonts/msyhbd.ttc", 0),  # 微软雅黑粗体
            ("C:/Windows/Fonts/simhei.ttf", 0),
            # 插件内置字体
            (os.path.join(self.assets_dir, "font.ttf"), 0),
        ]

        for path, index in font_paths:
            if os.path.exists(path):
                return (path, index)

        return (None, 0)  # 使用默认字体

    def _find_mono_font(self) -> Optional[str]:
        """查找可用的等宽字体"""
        mono_font_paths = [
            "/System/Library/Fonts/Monaco.ttf",
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/SFMono-Regular.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "C:/Windows/Fonts/consola.ttf",
        ]
        for path in mono_font_paths:
            if os.path.exists(path):
                return path
        return None

    def _load_font(self, size: int) -> ImageFont.FreeTypeFont:
        """加载粗体字体"""
        try:
            font_path, font_index = self.font_path
            if font_path:
                return ImageFont.truetype(font_path, size, index=font_index)
        except Exception as e:
            logger.debug(f"[SignCard] 加载字体失败: {e}")
        return ImageFont.load_default()

    def _load_mono_font(self, size: int) -> ImageFont.FreeTypeFont:
        """加载等宽字体"""
        try:
            if self.mono_font_path:
                return ImageFont.truetype(self.mono_font_path, size)
        except Exception as e:
            logger.debug(f"[SignCard] 加载等宽字体失败: {e}")
        return self._load_font(size)

    def _load_character_image(self, side: str) -> Optional[Image.Image]:
        """加载角色装饰图片"""
        # 尝试加载左右角色图
        image_names = [f"char_{side}.png", f"character_{side}.png", f"{side}.png"]

        for name in image_names:
            path = os.path.join(self.assets_dir, name)
            if os.path.exists(path):
                try:
                    img = Image.open(path).convert("RGBA")
                    return img
                except Exception as e:
                    logger.debug(f"[SignCard] 加载角色图片失败 {path}: {e}")
        return None

    def _load_background(self) -> Optional[Image.Image]:
        """加载背景图片"""
        # 优先从插件根目录加载，其次从 assets 目录
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        bg_paths = [
            os.path.join(plugin_dir, "sign.png"),  # 插件根目录
            os.path.join(self.assets_dir, "sign.png"),  # assets 目录
        ]
        for bg_path in bg_paths:
            if os.path.exists(bg_path):
                try:
                    bg = Image.open(bg_path).convert("RGBA")
                    # 缩放到卡片尺寸
                    bg = bg.resize((self.width, self.height), Image.Resampling.LANCZOS)
                    return bg
                except Exception as e:
                    logger.debug(f"[SignCard] 加载背景图片失败 {bg_path}: {e}")
        return None

    def render(
        self,
        reward: int,
        daily_reward: int,
        streak_bonus: int,
        lucky_reward: int,
        total_bananas: int | str,
        total_signs: int,
        streak: int,
        already_signed: bool = False,
    ) -> bytes:
        """
        渲染签到卡片

        Args:
            reward: 本次获得的总香蕉数
            daily_reward: 基础签到奖励
            streak_bonus: 连续签到奖励
            lucky_reward: 幸运星随机奖励
            total_bananas: 当前香蕉余额
            total_signs: 累计签到天数
            streak: 连续签到天数
            already_signed: 是否已签到

        Returns:
            PNG 图片的 bytes 数据
        """
        # 创建画布（优先使用背景图片）
        bg = self._load_background()
        if bg:
            img = bg.copy()
        else:
            img = Image.new("RGBA", (self.width, self.height), self.bg_color)
        draw = ImageDraw.Draw(img)

        # 加载字体
        font_title = self._load_font(36)
        font_large = self._load_font(28)
        font_normal = self._load_font(22)
        font_small = self._load_font(18)

        # 加载角色图片
        char_left = self._load_character_image("left")
        char_right = self._load_character_image("right")

        # 计算文字区域（中间部分）
        text_left = 180 if char_left else 50
        text_right = self.width - 180 if char_right else self.width - 50
        text_center = (text_left + text_right) // 2

        # 绘制角色图片
        if char_left:
            # 缩放到合适大小
            char_left = char_left.resize((160, 350), Image.Resampling.LANCZOS)
            img.paste(char_left, (10, 25), char_left)

        if char_right:
            char_right = char_right.resize((160, 350), Image.Resampling.LANCZOS)
            img.paste(char_right, (self.width - 170, 25), char_right)

        # 绘制签到信息
        y_offset = 40

        # 标题
        if already_signed:
            title = "今天已经签到过啦~"
            title_color = (150, 150, 150)
        else:
            title = "签到成功喵~"
            title_color = self.success_color

        # 居中绘制标题
        bbox = draw.textbbox((0, 0), title, font=font_title)
        title_width = bbox[2] - bbox[0]
        draw.text((text_center - title_width // 2, y_offset), title, font=font_title, fill=title_color)
        y_offset += 60

        if not already_signed:
            # 获得香蕉数
            reward_text = f"获得香蕉: {reward}"
            bbox = draw.textbbox((0, 0), reward_text, font=font_large)
            text_width = bbox[2] - bbox[0]
            draw.text((text_center - text_width // 2, y_offset), reward_text, font=font_large, fill=self.highlight_color)
            y_offset += 40

            # 奖励明细
            details = []
            details.append(f"基础: {daily_reward}")
            if streak_bonus > 0:
                details.append(f"连签加成: {streak_bonus}")
            if lucky_reward > 0:
                details.append(f"幸运星: {lucky_reward}")

            if len(details) > 1:
                detail_text = f"  ({', '.join(details)})"
                bbox = draw.textbbox((0, 0), detail_text, font=font_small)
                text_width = bbox[2] - bbox[0]
                draw.text((text_center - text_width // 2, y_offset), detail_text, font=font_small, fill=(120, 120, 120))
                y_offset += 35
            else:
                y_offset += 10

        # 当前余额
        balance_text = f"当前香蕉: {total_bananas}"
        bbox = draw.textbbox((0, 0), balance_text, font=font_large)
        text_width = bbox[2] - bbox[0]
        draw.text((text_center - text_width // 2, y_offset), balance_text, font=font_large, fill=self.text_color)
        y_offset += 45

        # 签到统计
        stats_text = f"累计签到: {total_signs}天"
        bbox = draw.textbbox((0, 0), stats_text, font=font_normal)
        text_width = bbox[2] - bbox[0]
        draw.text((text_center - text_width // 2, y_offset), stats_text, font=font_normal, fill=self.text_color)
        y_offset += 35

        streak_text = f"连续签到: {streak}天"
        bbox = draw.textbbox((0, 0), streak_text, font=font_normal)
        text_width = bbox[2] - bbox[0]
        draw.text((text_center - text_width // 2, y_offset), streak_text, font=font_normal, fill=self.text_color)

        # 如果有幸运星奖励，显示特效文字
        if lucky_reward > 0 and not already_signed:
            lucky_text = f"⭐ 幸运星降临 +{lucky_reward} ⭐"
            bbox = draw.textbbox((0, 0), lucky_text, font=font_normal)
            text_width = bbox[2] - bbox[0]
            draw.text((text_center - text_width // 2, self.height - 50), lucky_text, font=font_normal, fill=self.bonus_color)

        # 添加时间戳（避免复读检测）
        time_text = datetime.now().strftime("%H:%M:%S")
        mono_font = self._load_mono_font(13)
        bbox = draw.textbbox((0, 0), time_text, font=mono_font)
        text_width = bbox[2] - bbox[0]
        draw.text((self.width - text_width - 25, self.height - 32), time_text, font=mono_font, fill=(150, 150, 150))

        # 转换为 bytes
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    def render_to_base64(self, **kwargs) -> str:
        """渲染并返回 base64 编码的图片"""
        img_bytes = self.render(**kwargs)
        return base64.b64encode(img_bytes).decode("utf-8")
