#!/usr/bin/env python3
"""
分镜生成专用工具 — 上下文连贯性 + 亮点设计 + 构图引导
"""
import logging, json
from tools.base import AgentTool, ToolResult

logger = logging.getLogger("tools.storyboard")


class ShotContinuityCheck(AgentTool):
    """检测相邻镜头是否连贯——场景跳变/情绪断层/人物状态不一致"""
    name = "shot_continuity_check"
    description = "检测分镜序列的上下文连贯性：场景跳变、情绪断层、时间一致性、景别跳跃"
    category = "storyboard"

    async def execute(self, shots: list = None, shots_json: str = "") -> ToolResult:
        shot_list = shots or []
        if shots_json and not shot_list:
            try:
                shot_list = json.loads(shots_json)
            except:
                pass
        
        if len(shot_list) < 2:
            return self._ok({"issues": [], "score": 100, "summary": "镜头数不足2个"})

        issues = []
        for i in range(len(shot_list) - 1):
            a, b = shot_list[i], shot_list[i + 1]
            if not isinstance(a, dict) or not isinstance(b, dict):
                continue

            # 1. 场景跳变
            loc_a = a.get("location", a.get("scene", ""))
            loc_b = b.get("location", b.get("scene", ""))
            if loc_a and loc_b and loc_a != loc_b:
                trans = str(b.get("transition", "")).lower()
                valid_trans = ("fade", "dissolve", "切", "渐隐", "叠化", "黑场", "淡入", "淡出")
                if not any(t in trans for t in valid_trans):
                    issues.append({
                        "shot_pair": f"#{i+1}→#{i+2}",
                        "type": "场景跳变",
                        "detail": f"从 [{loc_a}] 切到 [{loc_b}]，建议标注转场方式",
                        "severity": "medium"
                    })

            # 2. 情绪断层
            emo_a = str(a.get("emotion", ""))
            emo_b = str(b.get("emotion", ""))
            extreme = ("暴怒", "崩溃", "狂喜", "惊恐", "极度悲伤")
            neutral = ("中性", "平静", "日常")
            if any(e in emo_a for e in extreme) and any(n in emo_b for n in neutral):
                issues.append({
                    "shot_pair": f"#{i+1}→#{i+2}",
                    "type": "情绪断层",
                    "detail": f"从 [{emo_a}] 直接跳到 [{emo_b}]，缺少情绪过渡镜头",
                    "severity": "high"
                })

            # 3. 时间跳跃
            tod_a = str(a.get("time_of_day", ""))
            tod_b = str(b.get("time_of_day", ""))
            if "白天" in tod_a and "深夜" in tod_b:
                issues.append({
                    "shot_pair": f"#{i+1}→#{i+2}",
                    "type": "时间跳跃",
                    "detail": f"[{tod_a}]→[{tod_b}]，需标注时间过渡",
                    "severity": "low"
                })

            # 4. 180°跳轴
            if self._has_axis_jump(str(a), str(b)):
                issues.append({
                    "shot_pair": f"#{i+1}→#{i+2}",
                    "type": "跳轴",
                    "detail": "方向突变，建议插入中性过渡镜",
                    "severity": "medium"
                })

            # 5. 景别跳跃过大
            if self._extreme_cut(str(a), str(b)):
                issues.append({
                    "shot_pair": f"#{i+1}→#{i+2}",
                    "type": "景别跳跃",
                    "detail": "特写→远景直接切换，建议插入中景过渡",
                    "severity": "medium"
                })

        score = max(0, 100 - len(issues) * 10)
        suggestions = []
        for issue in issues:
            itype = issue["type"]
            detail = issue["detail"]
            if itype == "场景跳变":
                suggestions.append(f"[{issue['shot_pair']}] {detail} → 加转场标记：fade/dissolve/黑场，或在两镜间插1镜过渡(如空镜/道具特写)")
            elif itype == "情绪断层":
                suggestions.append(f"[{issue['shot_pair']}] {detail} → 在两镜间加1个情绪过渡镜(人物表情渐变/环境氛围镜头)")
            elif itype == "时间跳跃":
                suggestions.append(f"[{issue['shot_pair']}] {detail} → 加时间字幕或叠化转场(日落→黑夜)")
            elif itype == "跳轴":
                suggestions.append(f"[{issue['shot_pair']}] 跳轴 → 插中立镜头(正面/背影/空镜)再切回，避免方向混淆")
            elif itype == "景别跳跃":
                suggestions.append(f"[{issue['shot_pair']}] 景别跳跃过大 → 插中景过渡，遵循 远景→中景→特写 阶梯递进")
        
        summary = f"{len(shot_list)}镜，{len(issues)}处问题，得分{score}/100"
        if not issues:
            suggestions.append("分镜连贯性良好，无需修改")
        return self._ok({
            "issues": issues,
            "score": score,
            "total_shots": len(shot_list),
            "summary": summary,
            "suggestions": suggestions
        }, score)

    def _has_axis_jump(self, s1: str, s2: str) -> bool:
        dirs = ["左", "右", "前", "后"]
        d1 = [d for d in dirs if d in s1]
        d2 = [d for d in dirs if d in s2]
        for a, b in [("左", "右"), ("前", "后")]:
            if a in d1 and b in d2:
                return True
        return False

    def _extreme_cut(self, s1: str, s2: str) -> bool:
        close = ("特写", "大特写", "近景")
        wide = ("远景", "全景", "大全景")
        c1 = any(c in s1 for c in close)
        w1 = any(w in s1 for w in wide)
        c2 = any(c in s2 for c in close)
        w2 = any(w in s2 for w in wide)
        return (c1 and w2) or (w1 and c2)


class ShotCompositionGuide(AgentTool):
    """根据场景内容和情感基调，建议最佳镜头构图和运镜方式"""
    name = "shot_composition_guide"
    description = "根据场景内容和情感基调，建议最佳镜头构图和运镜方式"
    category = "storyboard"

    async def execute(self, content: str = "", focus: str = "人物", emotion: str = "") -> ToolResult:
        if not content:
            return self._fail("缺少内容描述")

        shot_size = {"人物": "中近景/特写", "环境": "全景/远景", "动作": "中景/跟拍",
                      "对话": "双人中景/过肩镜头", "细节": "大特写/微距"}.get(focus, "中景")

        movement = {"悲伤": "静态固定镜头，缓慢推镜", "欢乐": "手持跟拍",
                     "紧张": "快速摇镜，急促切换", "浪漫": "慢速推轨，柔焦虚化",
                     "动作": "手持跟拍，快速横移"}.get(emotion, "平稳固定镜头")

        angle = {"悲伤": "平视或微俯视", "紧张": "低角度仰拍增压迫感",
                  "浪漫": "平视，柔光侧光", "动作": "多角度切换"}.get(emotion, "平视自然角度")

        return self._ok({
            "shot_size": shot_size, "camera_movement": movement,
            "camera_angle": angle,
            "suggestion": f"建议使用{shot_size}，{movement}，{angle}"
        }, 80)


class HighlightSceneDesign(AgentTool):
    """识别关键戏剧时刻并设计视觉亮点"""
    name = "highlight_scene_design"
    description = "识别剧本中的关键戏剧时刻，建议高光镜头设计"
    category = "storyboard"

    async def execute(self, script_text: str = "", shots_json: str = "", genre: str = "") -> ToolResult:
        if not script_text and not shots_json:
            return self._fail("需要剧本或分镜数据")

        highlight_keywords = {
            "反转": "剧情反转点", "真相": "真相揭晓", "吻": "接吻/亲密",
            "拥抱": "拥抱", "决斗": "决斗/对抗", "死亡": "死亡/告别",
            "眼泪": "落泪", "跪": "下跪", "告白": "告白",
            "分手": "分手", "相遇": "初次相遇", "爆炸": "爆炸场景",
            "车祸": "车祸", "婚礼": "婚礼", "救": "救援/拯救",
            "杀": "刺杀/搏斗", "告白失败": "告白失败",
        }

        highlights = []
        text = script_text[:5000]
        for keyword, desc in highlight_keywords.items():
            if keyword in text:
                highlights.append({"keyword": keyword, "moment": desc})

        if not highlights:
            highlights = [{"keyword": "开场", "moment": "开场高光"}, {"keyword": "尾声", "moment": "结局高光"}]

        design_tips = []
        for h in highlights[:4]:
            m = h["moment"]
            if m in ("接吻/亲密", "告白", "拥抱", "婚礼"):
                tip = {"camera": "慢镜头特写混合", "lighting": "逆光柔焦", "duration": "延长30%", "impact": "极高"}
            elif m in ("决斗/对抗", "爆炸场景", "刺杀/搏斗"):
                tip = {"camera": "快速切换+特写", "lighting": "高对比度侧光", "duration": "正常节奏", "impact": "极高"}
            elif m in ("落泪", "死亡/告别", "分手"):
                tip = {"camera": "长镜头慢推", "lighting": "暗调柔光", "duration": "延长50%", "impact": "极高"}
            else:
                tip = {"camera": "黄金构图+慢推", "lighting": "戏剧化侧光", "duration": "延长20%", "impact": "高"}
            tip["moment"] = m
            design_tips.append(tip)

        return self._ok({
            "highlights": highlights,
            "design_tips": design_tips,
            "count": len(highlights),
            "summary": f"识别{len(highlights)}个戏剧高光时刻"
        })


# 注册
def register():
    return [ShotContinuityCheck(), ShotCompositionGuide(), HighlightSceneDesign()]
