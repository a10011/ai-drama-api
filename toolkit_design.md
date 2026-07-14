# 🧰 智能体工具箱架构

## 架构图

```
┌─────────────────────────────────────────────────────────┐
│                    Tool Registry                         │
│  tools/registry.py  统一注册/发现/权限                     │
│  每个Agent启动时声明自己需要哪些Tool                         │
├─────────────────────────────────────────────────────────┤
│  tools/io.py        tools/media.py    tools/creative.py │
│  · read_file        · generate_image  · brainstorm      │
│  · write_file       · analyze_image   · struct_outline  │
│  · search_web       · generate_audio  · plot_twist      │
│  · call_api         · generate_video  · tone_shift      │
│  · run_ffmpeg       · transcribe      · eval_quality    │
│  · db_query         · extract_frames  · compare_versions │
└─────────────────────────────────────────────────────────┘
```

---

## 导演智能体 (DirectorAgent) — 6个工具

### 🔧 analyze_pacing
- **做啥**: 分析剧本节奏曲线，标出高潮/低谷/转折点
- **输入**: script_text, total_duration_sec
- **输出**: {pacing_curve: [{t_sec, intensity, label}], climax_at, slowest_at}
- **实现**: LLM逐段打分(1-10)→插值成曲线→定位极值点
- **比人强在哪**: 人只能感觉"这里慢"，智能体能量化到秒级

### 🔧 suggest_camera
- **做啥**: 根据情绪和场景推荐运镜方案
- **输入**: scene_desc, emotion, character_count
- **输出**: {shot_type, angle, movement, focal_length, dutch_angle}
- **实现**: 规则引擎(emotion→shot映射表) + LLM微调
- **映射表**: 紧张→手持晃动 | 悲伤→慢推近景 | 愤怒→低角度仰拍 | 浪漫→滑轨平移

### 🔧 research_moodboard
- **做啥**: 搜索同类题材的参考画面/色调
- **输入**: genre, keywords, era
- **输出**: [{ref_description, color_palette, lighting_style}]
- **实现**: 网络搜索→提取描述→LLM总结风格要素
- **比人强在哪**: 人找参考要翻几百张图，智能体秒级总结

### 🔧 cast_match
- **做啥**: 根据角色描述匹配最佳演员类型
- **输入**: character_traits, age_range, role_type
- **输出**: {appearance_prompt, archetype, suggested_style}
- **实现**: LLM+角色类型数据库映射

### 🔧 shotlist_from_script
- **做啥**: 从剧本自动生成完整分镜表
- **输入**: script_text, style_preference
- **输出**: [{shot_id, duration, camera, dialogue, emotion, transition}]
- **实现**: 正则切场景→LLM逐场镜头分解→结构化输出

### 🔧 quality_scorecard
- **做啥**: 多维度评估成片质量
- **输入**: final_video_path, script_text, shot_list
- **输出**: {overall: 85, pacing: 80, visual: 90, audio: 82, story: 88, suggestions: [...]}
- **实现**: ffprobe提取技术参数 + LLM评估内容维度

---

## 剧本智能体 (ScriptAgent) — 5个工具

### 🔧 brainstorm_hooks
- **做啥**: 批量生成开场钩子(前3秒抓人)
- **输入**: genre, target_audience, count=10
- **输出**: [{hook_text, hook_type, surprise_score}]
- **实现**: LLM并行生成→去重→按surprise_score排序
- **比人强在哪**: 人想5个脑壳疼，智能体秒出10个选最优

### 🔧 plot_twist_generator
- **做啥**: 生成多级反转点
- **输入**: current_plot, desired_twist_count, twist_types
- **输出**: [{at_scene, twist_description, foreshadowing_needed}]
- **实现**: LLM分析当前情节→寻找可反转的假设→生成+验证

### 🔧 dialogue_polish
- **做啥**: 润色对白（自然度/角色辨识度/节奏）
- **输入**: raw_dialogue, character_profiles, scene_context
- **输出**: {polished_dialogue, changes_made: [...]}
- **实现**: LLM带角色设定重写→标记改动点
- **比人强在哪**: 人对白容易千人一面，智能体能严格按设定匹配

### 🔧 tropes_checker
- **做啥**: 检测陈词滥调/狗血桥段
- **输入**: script_text
- **输出**: [{found_trope, location, suggestion}]
- **实现**: trope关键词库匹配 + LLM语义检测
- **库**: 200+ 常见狗血桥段（车祸/失忆/替身/误会...）

### 🔧 audience_simulator
- **做啥**: 模拟目标观众反应
- **输入**: script_text, persona_count=5
- **输出**: [{persona_type, engagement_curve, drop_points}]
- **实现**: LLM扮演5种观众人设→逐段标注反应
- **比人强在哪**: 人没法同时扮演5种观众不串

---

## 角色智能体 (CharacterAgent) — 4个工具

### 🔧 extract_characters
- **做啥**: 从剧本提取角色+关系网
- **输入**: script_text
- **输出**: {characters: [...], relationship_graph: {edges}}
- **实现**: LLM三层兜底（提取→强化→正则扫描）
- **已有，升级**: 加对话占比统计→判断谁是真正主角

### 🔧 design_wardrobe
- **做啥**: 按场景设计服装/造型
- **输入**: character_desc, scene_context, era
- **输出**: {outfit_desc, color_scheme, accessories, hair_style}
- **实现**: LLM → 参考图搜索 → 描述增强
- **比人强在哪**: 人是A演员穿B衣服，智能体能按角色逻辑连续设计

### 🔧 voice_profile
- **做啥**: 为角色设计声音特征
- **输入**: character_traits, age, personality
- **输出**: {pitch, speed, timbre, emotion_range, idiolect}
- **实现**: 特征→TTS参数映射表
- **映射**: 霸气→低音慢速 | 活泼→高音快语速 | 神秘→气声+留白

### 🔧 ensure_consistency
- **做啥**: 检查同一角色跨场景一致性
- **输入**: all_scene_descriptions, character_name
- **输出**: {inconsistencies: [{scene_a, scene_b, diff_field, diff_value}]}
- **实现**: LLM逐场景抽取角色属性→比较差异
- **比人强在哪**: 人手动画长剧集，角色细节必然前后矛盾

---

## 分镜智能体 (StoryboardAgent) — 3个工具

### 🔧 scene_to_shots
- **做啥**: 场景描述→镜头序列
- **输入**: scene_desc, duration, style
- **输出**: [{shot_id, type, angle, composition, lighting, frame_desc}]
- **实现**: LLM→结构化输出→摄像机规则校验

### 🔧 continuity_check
- **做啥**: 检查连贯性（180度规则/视线匹配/动作连续）
- **输入**: shot_list, current_shot_index
- **输出**: {violations: [{rule, description, fix}]}
- **实现**: 规则引擎(电影语法) + LLM语义验证

### 🔧 render_frame_preview
- **做啥**: 生成关键帧预览图
- **输入**: frame_description, shot_type
- **输出**: image_url
- **实现**: 调UnifiedModel.image() 用最佳画图模型

---

## 场景智能体 (SceneAgent) — 3个工具

### 🔧 design_environment
- **做啥**: 设计场景环境（时间/天气/光照/色调）
- **输入**: scene_context, mood, time_of_day
- **输出**: {environment_desc, lighting_setup, color_grading, props}
- **实现**: LLM → 参考搜索 → 风格增强

### 🔧 match_location
- **做啥**: 匹配最佳场景模板（内景/外景/日/夜）
- **输入**: scene_type, era, budget_level
- **输出**: {template_id, recommended_props, set_dressing}
- **实现**: 模板库匹配 + LLM微调

### 🔧 atmosphere_generator  
- **做啥**: 生成场景氛围描述+参考配色
- **输入**: mood_words, genre
- **输出**: {atmosphere_text, color_palette_hex, particle_suggestions}
- **实现**: mood→颜色映射 + LLM润色
- **映射**: 悬疑→暗蓝+青绿 / 爱情→暖粉+金 / 恐怖→血红+暗紫

---

## 配音智能体 (TTSAgent) — 4个工具

### 🔧 match_voice
- **做啥**: 根据角色特征匹配最佳声音库
- **输入**: character_profile, language
- **输出**: {voice_id, provider, pitch_adjust, speed_adjust}
- **实现**: 角色特征向量→余弦相似度→声音库匹配

### 🔧 mark_emotion
- **做啥**: 标注对白中每句的情绪/强调/停顿
- **输入**: dialogue_text, scene_emotion
- **输出**: [{text, emotion, emphasis_words, pause_before_ms}]
- **实现**: LLM情绪标注 + SSML生成

### 🔧 audio_postprocess
- **做啥**: 后处理（去噪/均衡/混响/空间化）
- **输入**: audio_path, scene_type(indoor/outdoor)
- **输出**: processed_audio_path
- **实现**: ffmpeg滤镜链(equalizer+reverb+compressor)

### 🔧 voice_continuity
- **做啥**: 确保同一角色多句对白声音一致
- **输入**: audio_segments[], character_name
- **输出**: {segments_to_fix, adjustment_params}
- **实现**: 频谱分析→对比基线→自动调整

---

## BGM智能体 (BGMAgent) — 3个工具

### 🔧 mood_to_track
- **做啥**: 情绪→BGM匹配
- **输入**: scene_emotion, duration_sec, intensity
- **输出**: [{track_id, start_offset, fade_in, fade_out}]
- **实现**: 情绪向量→素材库余弦匹配

### 🔧 tempo_mapping
- **做啥**: 分析剧情节奏→BGM节奏映射
- **输入**: pacing_curve, track_candidates
- **输出**: {beat_alignment_map, transition_points}
- **实现**: BPM提取→镜头切换点同步
- **比人强在哪**: 手工对BPM+镜头切换点极其痛苦

### 🔧 dynamic_score
- **做啥**: 生成动态配乐指令（渐强/减弱/变奏）
- **输入**: scene_intensity_curve, base_track
- **输出**: {volume_automation, filter_automation, layer_triggers}
- **实现**: 强度曲线→音量包络+滤波器扫频

---

## 视频智能体 (VideoAgent) — 3个工具

### 🔧 multi_model_strategy
- **做啥**: 按场景类型自动选最佳视频生成模型
- **输入**: scene_type, complexity, quality_requirement
- **输出**: {model_chain: [provider], fallbacks, estimated_time}
- **当前**: Kling→HappyHorse→Seedance 三层
- **升级**: 按场景分派（打斗→可灵, 对话→HappyHorse, 风景→Seedance）

### 🔧 artifact_detect
- **做啥**: 检测画面瑕疵（闪烁/抖动/形变/人脸崩）
- **输入**: video_path
- **输出**: [{artifact_type, timestamp, severity, fix_suggestion}]
- **实现**: ffprobe逐帧差分 + 可选AI检测
- **比人强在哪**: 人肉眼看10分钟视频找瑕疵容易漏

### 🔧 style_transfer
- **做啥**: 统一镜头风格（保证同场景色调一致）
- **输入**: video_path, reference_frame
- **输出**: color_graded_video_path
- **实现**: ffmpeg lut3d + 色彩空间变换

---

## 字幕智能体 (SubtitleAgent) — 3个工具

### 🔧 transcribe_with_timing
- **做啥**: 语音→带时间戳的字幕
- **输入**: audio_path, language
- **输出**: [{text, start_ms, end_ms, confidence}]
- **实现**: whisper/阿里ASR → 时间对齐

### 🔧 style_subtitles
- **做啥**: 按角色/情绪设计字幕样式
- **输入**: character_profiles, scene_mood
- **输出**: {font, color, animation, position, background}
- **实现**: 角色→颜色映射 + ASS/SSA 格式输出

### 🔧 readability_score
- **做啥**: 评估字幕可读性（字数/停留时间/断句）
- **输入**: subtitle_entries
- **输出**: {score, violations: [{entry, issue, fix}]}
- **检查**: 单条>15字、停留<1秒、断句怪异
- **比人强在哪**: 人最多感觉"看着累"，智能体精确到每条

---

## 合成智能体 (CompositeAgent) — 3个工具

### 🔧 timeline_assembler
- **做啥**: 多轨时间线组装（视频/音频/字幕/BGM）
- **输入**: video_clips[], audio_segments[], subtitles[], bgm_tracks[]
- **输出**: ffmpeg_complex_filter_script
- **实现**: 自动生成ffmpeg命令（已做，升级参数生成）

### 🔧 sync_check
- **做啥**: 检查音画同步/唇形匹配/字幕对齐
- **输入**: composite_video_path
- **输出**: {sync_issues: [{track, offset_ms, severity}]}
- **实现**: 音频包络 vs 画面变化采样对比

### 🔧 export_preset
- **做啥**: 多平台导出预设（抖音/快手/B站/YouTube）
- **输入**: raw_video_path, platforms[]
- **输出**: [{platform, resolution, bitrate, format, crop}]
- **实现**: 各平台规格表→ffmpeg编码参数
- **比人强在哪**: 人记不住所有平台规格，智能体一键多格式

---

## 实现优先级

| 优先级 | 智能体 | 工具 | 理由 |
|--------|--------|------|------|
| P0 | ScriptAgent | plot_twist + dialogue_polish + audience_sim | 剧本是一切基础 |
| P0 | DirectorAgent | shotlist_from_script + quality_scorecard | 导演决定成片质量 |
| P1 | CharacterAgent | ensure_consistency + voice_profile | 角色一致性最易出戏 |
| P1 | TTSAgent | mark_emotion + voice_continuity | 配音感情化 |
| P1 | VideoAgent | multi_model_strategy | 已做三层，需要更智能 |
| P2 | SceneAgent | atmosphere_generator | 增强视觉效果 |
| P2 | BGMAgent | mood_to_track + tempo_mapping | BGM增强情绪 |
| P2 | CompositeAgent | sync_check | 最终质量把关 |
| P3 | SubtitleAgent | readability_score | 锦上添花 |
| P3 | StoryboardAgent | continuity_check | 专业级要求 |

## 工具基类

```python
# tools/base.py
class AgentTool:
    name: str
    description: str  
    agent_class: str   # 哪个Agent才能用
    
    async def execute(self, **kwargs) -> dict:
        """所有工具统一接口"""
        pass
    
    def validate(self, **kwargs) -> bool:
        """输入验证"""
        pass
    
    def explain(self) -> str:
        """给LLM看的工具描述"""
        pass
```

## 实现方式

每个工具是一个独立Python文件 `tools/xxx.py`，继承 `AgentTool`。

Agent 实例化时注册工具：
```python
class DirectorAgent:
    def __init__(self):
        self.tools = [
            PacingTool(), ShotlistTool(), CameraTool(),
            MoodboardTool(), CastingTool(), ScorecardTool()
        ]
    
    async def execute(self, context):
        # LLM决定用哪个工具
        tool_plan = await self.llm.plan_tools(context, self.tools)
        results = []
        for call in tool_plan:
            tool = self.get_tool(call.name)
            result = await tool.execute(**call.params)
            results.append(result)
        return results
```
