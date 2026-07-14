"""智能体7：字幕智能体 — 自动时间轴、字幕样式自定义"""
import json
import time
import logging
import re
from typing import Optional, Dict, List
from .agent_base_legacy import BaseAgent, AgentResult
from utils.storage_path import subtitle_path

logger = logging.getLogger(__name__)

SUBTITLE_TIMING_PROMPT = """你是一位资深字幕时间轴师，精通竖屏短剧字幕节奏。你深谙字幕的可读性工程与观众阅读心理。

【专业规范】
1. CPS（每秒字符数）控制：中文字幕理想 CPS=4-8 字/秒，最高不超过 12 字/秒。
   超过 12 字/秒观众读不完，低于 3 字/秒会显得拖沓。
2. 单条时长：最短 1.2 秒（否则一闪而过），最长 6 秒（超过应拆分）。
3. 字数上限：竖屏单行不超 14 字，单条最多 2 行（共 28 字），超长必须断句拆分。
4. 断句原则：在自然停顿处断句（标点/语气词/语义单元），不要把一个词拆开。
   优先在逗号、句号、问号、感叹号处切分；其次在"的/了/着/和"等虚词后切。
5. 时间轴对齐：字幕开始=台词开口瞬间，结束=台词说完+0.1-0.3 秒缓冲（避免贴脸切下一条）。
6. 间隔：相邻字幕间至少留 0.1 秒空白帧，避免视觉粘连。
7. 情绪标注：根据台词内容标注情绪（开心/生气/难过/委屈/温柔/紧张/激动/无奈/羞涩/冷漠）。
8. 说话人：多角色对话必须标注 speaker，独白/旁白标"旁白"。

返回JSON格式（不要markdown代码块）：
{
  "subtitles": [
    {
      "index": 1,
      "start_sec": 0.0,
      "end_sec": 2.5,
      "text": "第一句台词",
      "speaker": "角色名",
      "emotion": "情绪标注"
    }
  ],
  "total_duration_sec": 60,
  "format": "SRT",
  "notes": "时间轴备注"
}"""

SUBTITLE_GENERATE_PROMPT = """你是一位专业字幕制作者，精通 SRT 格式与竖屏短剧字幕排版。

【专业规范】
1. SRT 时间码格式：HH:MM:SS,mmm（注意是逗号不是点），如 00:00:01,000 --> 00:00:04,000
2. 单条字幕不超 2 行，每行不超 14 字（竖屏宽度限制）
3. 标点规范：保留问号/感叹号/省略号（传递语气），去掉句末句号（字幕惯例）
4. 换行：在自然停顿处换行，第二行比第一行短为佳（金字塔型更易读）
5. 时间码必须严格递增，不能重叠或倒退
6. 多说话人：换行标注，或用"角色名：台词"格式

返回JSON格式（不要markdown代码块）：
{
  "subtitle_text": "1\\n00:00:01,000 --> 00:00:04,000\\n台词内容",
  "language": "zh"
}"""

STYLE_PROMPT = """你是一位字幕视觉设计师，精通竖屏短剧字幕的排版美学与可读性平衡。

【专业知识】
1. 字体选择：中文用黑体类（思源黑体/微软雅黑/苹方），避免宋体/楷体（竖屏小字难辨）。
   古装/仙侠可用书法体（仅限标题），正文仍用黑体保证可读。
2. 字号：竖屏 1080×1920 推荐 36-48px，太小看不清，太大遮挡画面。
3. 描边：必加黑色描边（width 1.5-2.5px），保证任何背景下可读；浅色画面用深描边。
4. 阴影：柔和投影增强立体感，offset 2-3px，但不要硬阴影（显得脏）。
5. 位置：默认底部居中（距底边 8-12% 屏高），避开安全区边缘；重要台词可上移。
6. 安全区：字幕必须在画面安全区内（左右各留 5-8% 边距，避免被圆角/状态栏裁切）。
7. 说话人区分：多角色对话可用颜色区分（主角暖色/反派冷色），或加"【角色名】"前缀。
8. 动效：现代题材用淡入淡出，古装用从下浮入，强调句可用逐字出现（但慎用，影响阅读）。
9. 强调字：关键词可用主色高亮（如表白时的"喜欢"用粉色），但单条不超 2 个强调字。

返回JSON格式（不要markdown代码块）：
{
  "style_name": "样式名称",
  "settings": {
    "font_family": "字体名称",
    "font_size": 24,
    "font_color": "#FFFFFF",
    "stroke_color": "#000000",
    "stroke_width": 1.5,
    "shadow_color": "rgba(0,0,0,0.5)",
    "shadow_offset_x": 2,
    "shadow_offset_y": 2,
    "position": "bottom/center/top",
    "alignment": "left/center/right",
    "max_width_percent": 80,
    "background": "none/半透明黑/渐变"
  },
  "speaker_style": {
    "enabled": true,
    "prefix": "【角色名】",
    "color": "#FFD700",
    "separate_line": true
  },
  "animation": "无/淡入淡出/从下弹入/逐字出现",
  "keywords": "适用场景关键词"
}"""


class SubtitleAgent(BaseAgent):
    """字幕智能体：自动时间轴、字幕样式自定义"""

    name = "字幕智能体"
    description = "自动时间轴、字幕样式自定义"
    version = "1.0.0"

    def generate_timeline(
        self,
        script: str,
        dialogue_lines: List[Dict],
        total_duration: float,
    ) -> AgentResult:
        """生成字幕时间轴"""
        start = time.time()
        try:
            dialogue_info = json.dumps(
                dialogue_lines, ensure_ascii=False, indent=2
            )
            user_prompt = f"""剧本：
{script[:2000]}

台词片段：
{dialogue_info}

总时长：{total_duration}秒

每秒约2.5-3个字的语速，请生成精确的时间轴。"""
            result = self._call_llm_json(
                SUBTITLE_TIMING_PROMPT, user_prompt
            )
            return AgentResult(
                data=result, duration_ms=int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.error(f"生成时间轴失败: {e}")
            return AgentResult(success=False, error=str(e))

    def generate_subtitle(self, shot: dict) -> AgentResult:
        """根据单镜头信息生成SRT格式字幕"""
        start = time.time()
        try:
            dialogue = shot.get("dialogue", shot.get("text", ""))
            description = shot.get(
                "description", shot.get("scene_prompt", "")
            )
            shot_num = shot.get("shot_num", 1)

            if not dialogue:
                return AgentResult(
                    data={
                        "subtitle_text": "",
                        "language": "zh",
                        "shot_num": shot_num,
                    },
                    duration_ms=int((time.time() - start) * 1000),
                )

            # 尝试用LLM生成
            try:
                user_prompt = f"""镜头号：{shot_num}
台词：{dialogue[:300]}
场景描述：{description[:200]}
请生成SRT格式的字幕文本，每句台词2-4秒显示时长。"""
                result = self._call_llm_json(
                    SUBTITLE_GENERATE_PROMPT, user_prompt
                )
                if result and result.get("subtitle_text"):
                    return AgentResult(
                        data={
                            "subtitle_text": result["subtitle_text"],
                            "language": result.get("language", "zh"),
                            "shot_num": shot_num,
                        },
                        duration_ms=int(
                            (time.time() - start) * 1000
                        ),
                    )
            except Exception as ex_: logger.warning(f"[agent_subtitle]  {ex_}")

            # LLM失败则本地构造SRT
            lines = [l.strip() for l in re.split(r'[。！？……；？！]', dialogue.strip()) if l.strip()] or [dialogue]
            srt_lines = []
            sec = 1.0
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                duration = max(len(line) * 0.3, 2.0)
                end = sec + duration
                srt_lines.append(str(len(srt_lines) // 3 + 1))
                srt_lines.append(
                    f"{self._fmt_sec(sec)} --> {self._fmt_sec(end)}"
                )
                srt_lines.append(line)
                srt_lines.append("")
                sec = end

            return AgentResult(
                data={
                    "subtitle_text": "\n".join(srt_lines),
                    "language": "zh",
                    "shot_num": shot_num,
                },
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as e:
            logger.error(f"字幕生成失败: {e}")
            return AgentResult(
                data={
                    "subtitle_text": "1\n00:00:01,000 --> 00:00:04,000\n"
                    + shot.get("dialogue", shot.get("text", "")),
                    "language": "zh",
                    "shot_num": shot.get("shot_num", 1),
                }
            )

    def _fmt_sec(self, sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int((sec - int(sec)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def design_style(self, drama_genre: str, mood: str) -> AgentResult:
        """设计字幕样式"""
        start = time.time()
        try:
            user_prompt = f"""短剧类型：{drama_genre}
整体氛围：{mood}"""
            result = self._call_llm_json(STYLE_PROMPT, user_prompt)
            return AgentResult(
                data=result, duration_ms=int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.error(f"设计字幕样式失败: {e}")
            return AgentResult(success=False, error=str(e))

    def export_srt(self, subtitles: List[Dict], pipeline_id: str = "") -> AgentResult:
        """导出SRT格式字幕文件"""
        start = time.time()
        try:

            def fmt_time(sec):
                h = int(sec // 3600)
                m = int((sec % 3600) // 60)
                s = int(sec % 60)
                ms = int((sec - int(sec)) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

            lines = []
            for i, sub in enumerate(subtitles, 1):
                text = sub.get("text", "")
                if sub.get("speaker"):
                    text = f"{sub['speaker']}: {text}"
                lines.append(str(i))
                lines.append(
                    f"{fmt_time(sub.get('start_sec', 0))} --> {fmt_time(sub.get('end_sec', 5))}"
                )
                lines.append(text)
                lines.append("")

            srt_content = "\n".join(lines)
            import os

            local_path, subtitle_url = subtitle_path(project_id)
            fname = local_path
            with open(fname, "w", encoding="utf-8") as f:
                f.write(srt_content)

            return AgentResult(
                data={
                    "srt_content": srt_content,
                    "file_path": fname,
                    "total_lines": len(subtitles),
                },
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as e:
            logger.error(f"导出SRT失败: {e}")
            return AgentResult(success=False, error=str(e))

    def run(
        self, action: str = "timeline", **kwargs
    ) -> AgentResult:
        if action == "timeline" or action == "generate":
            # orchestrator 的 SUBTITLE param_map 传的是 script_text，兼容 script
            return self.generate_timeline(
                kwargs.get("script_text", kwargs.get("script", "")),
                kwargs.get("dialogue_lines", []),
                kwargs.get("total_duration", 60.0),
            )
        elif action == "generate_subtitle":
            return self.generate_subtitle(kwargs.get("shot", {}))
        elif action == "style":
            return self.design_style(
                kwargs.get("genre", "都市"),
                kwargs.get("mood", "轻松"),
            )
        elif action == "export":
            return self.export_srt(kwargs.get("subtitles", []), kwargs.get("pipeline_id", ""))
        return AgentResult(success=False, error=f"未知动作: {action}")

    def execute(self, shot: dict, **kwargs):
        """唯一入口：生成字幕"""
        return self.generate_subtitle(shot)

