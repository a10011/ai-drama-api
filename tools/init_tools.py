"""注册所有工具并分配给Agent"""
from tools.registry import ToolRegistry
from tools.script_tools import (
    BrainstormHooks, PlotTwistGenerator, DialoguePolish,
    TropesChecker, AudienceSimulator
)
from tools.director_tools import (
    AnalyzePacing, SuggestCamera, ShotlistFromScript, QualityScorecard
)
from tools.character_tools import CharacterConsistencyCheck, CharacterVisualPrompt, CharacterTraitValidator
from tools.scene_tools import SceneAtmospherePrompt, SceneConsistencyCheck
from tools.storyboard_tools import ShotContinuityCheck, ShotCompositionGuide, HighlightSceneDesign
from tools.media_tools import VoiceEmotionGuide, BGMMoodMatcher, VideoPromptOptimizer, VideoInputValidator, SubtitleStyleGuide, CompositeQualityCheck
from tools.cinematographer_tools import ShotCameraGuide, LightingPlanner, LensSelector
from tools.sfx_tools import SFXMatcher, TransitionDesigner, ColorGrader
from tools.wardrobe_tools import OutfitMatcher, PropsAssigner, ContinuityChecker


def create_registry() -> ToolRegistry:
    reg = ToolRegistry()

    # 注册
    reg.register(BrainstormHooks())
    reg.register(PlotTwistGenerator())
    reg.register(DialoguePolish())
    reg.register(TropesChecker())
    reg.register(AudienceSimulator())
    reg.register(AnalyzePacing())
    reg.register(SuggestCamera())
    reg.register(ShotlistFromScript())
    reg.register(QualityScorecard())
    reg.register(CharacterConsistencyCheck())
    reg.register(CharacterVisualPrompt())
    reg.register(CharacterTraitValidator())
    reg.register(SceneAtmospherePrompt())
    reg.register(SceneConsistencyCheck())
    reg.register(ShotContinuityCheck())
    reg.register(ShotCompositionGuide())
    reg.register(HighlightSceneDesign())
    reg.register(VoiceEmotionGuide())
    reg.register(BGMMoodMatcher())
    reg.register(VideoPromptOptimizer())
    reg.register(VideoInputValidator())
    reg.register(SubtitleStyleGuide())
    reg.register(CompositeQualityCheck())
    # 摄影指导工具
    reg.register(ShotCameraGuide())
    reg.register(LightingPlanner())
    reg.register(LensSelector())
    # 特效师工具
    reg.register(SFXMatcher())
    reg.register(TransitionDesigner())
    reg.register(ColorGrader())
    # 服化道工具
    reg.register(OutfitMatcher())
    reg.register(PropsAssigner())
    reg.register(ContinuityChecker())

    # 分配
    reg.assign("ScriptAgent", [
        "brainstorm_hooks", "plot_twist_generator", "dialogue_polish",
        "tropes_checker", "audience_simulator"
    ])
    reg.assign("DirectorAgent", [
        "analyze_pacing", "suggest_camera", "shotlist_from_script", "quality_scorecard"
    ])
    reg.assign("CharacterAgent", [
        "character_consistency_check", "character_visual_prompt", "character_trait_validator"
    ])
    reg.assign("SceneAgent", [
        "scene_atmosphere_prompt", "scene_consistency_check"
    ])
    reg.assign("StoryboardAgent", [
        "shot_continuity_check", "shot_composition_guide"
    ])
    reg.assign("TTSAgent", ["voice_emotion_guide"])
    reg.assign("BGMAgent", ["bgm_mood_matcher"])
    reg.assign("VideoAgent", ["video_prompt_optimizer", "video_input_validator"])
    reg.assign("SubtitleAgent", ["subtitle_style_guide"])
    reg.assign("CompositeAgent", ["composite_quality_check"])
    # 新智能体工具分配
    reg.assign("CinematographerAgent", ["shot_camera_guide", "lighting_planner", "lens_selector"])
    reg.assign("SFXAgent", ["sfx_matcher", "transition_designer", "color_grader"])
    reg.assign("WardrobeAgent", ["outfit_matcher", "props_assigner", "continuity_checker"])

    return reg

# 全局单例
_tool_registry = None


def get_registry() -> ToolRegistry:
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = create_registry()
    return _tool_registry
