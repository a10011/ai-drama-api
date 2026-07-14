"""
Hermes 剧本工作流 — 数据模型
独立于短剧管线，自包含 Schema
"""
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class CharacterProfile:
    name: str
    role: str          # 主角/配角/反派
    gender: str        # 男/女
    age: str
    appearance: str
    personality: str
    background: str
    growth_arc: str
    voice_style: str = ""


@dataclass
class EpisodeOutline:
    ep: int
    title: str
    duration_seconds: int = 180
    summary: str = ""
    hook: str = ""
    climax: str = ""
    cliffhanger: str = ""


@dataclass
class SeasonStructure:
    act_1: str = ""
    act_2: str = ""
    act_3: str = ""


@dataclass
class StoryArc:
    format: str = "season"
    season: int = 1
    total_episodes: int = 12
    season_title: str = ""
    season_arc: str = ""
    world_building: str = ""
    theme: str = ""
    tone: str = ""
    characters: list = field(default_factory=list)
    episode_outlines: list = field(default_factory=list)
    season_structure: dict = field(default_factory=dict)
    character_relationships: list = field(default_factory=list)
    narrative_threads: dict = field(default_factory=dict)


@dataclass
class Shot:
    shot_id: int
    camera: str          # 镜头类型
    image_prompt: str    # 画面描述
    dialogue: str = ""   # 对白
    duration: int = 5    # 秒
    action: str = ""     # 动作描述


@dataclass
class Scene:
    scene_id: int
    location: str
    time: str            # 日/夜/黄昏
    characters: list = field(default_factory=list)
    summary: str = ""
    shots: list = field(default_factory=list)
    emotional_tone: str = ""


@dataclass
class EpisodeScript:
    format: str = "episode"
    episode: int = 1
    title: str = ""
    total_duration: int = 180
    scenes: list = field(default_factory=list)


@dataclass
class ReviewReport:
    passed: bool = False
    score: float = 0.0
    issues: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    dimension_scores: dict = field(default_factory=dict)


@dataclass
class WorkflowTask:
    task_id: str = ""
    user_id: int = 0
    title: str = ""
    genre: str = ""
    synopsis: str = ""
    style_hint: str = ""
    mode: str = "precise"   # precise / fast / deep
    status: str = "pending"
    result: dict = field(default_factory=dict)
    error: str = ""


def char_to_dict(c: CharacterProfile) -> dict:
    return asdict(c)


def scene_to_dict(s: Scene) -> dict:
    return asdict(s)


def episode_to_dict(e: EpisodeScript) -> dict:
    return asdict(e)
