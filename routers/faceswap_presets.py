# -*- coding: utf-8 -*-
# 换脸预设配置 API - 2026-06-27
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/faceswap", tags=["换脸预设"])

PRESETS = {
    "outfit": [
        {"id": "hanfu", "icon": "\U0001F458", "name": "古装长袍", "desc": "飘逸的汉服长袍，仙气十足"},
        {"id": "xizhuang", "icon": "\U0001F935", "name": "西装革履", "desc": "修身西装三件套，精英范"},
        {"id": "zhangjia", "icon": "\U0001F6E1", "name": "战甲铠甲", "desc": "金属战甲，威风凛凛"},
        {"id": "weiyi", "icon": "\U0001F9E5", "name": "休闲卫衣", "desc": "宽松卫衣+牛仔裤，日常感"},
        {"id": "xiaofu", "icon": "\U0001F393", "name": "校园校服", "desc": "青春校园风，白衬衫配格子裙"},
        {"id": "hunsha", "icon": "\U0001F470", "name": "梦幻婚纱", "desc": "白色婚纱礼服，浪漫唯美"},
        {"id": "chushi", "icon": "\U0001F9D1\u200D\U0001F373", "name": "厨师制服", "desc": "白色厨师装，专业范"},
        {"id": "ol", "icon": "\U0001F454", "name": "职场OL", "desc": "精致职场套装，干练知性"},
        {"id": "yingxiong", "icon": "\U0001F9B8", "name": "超级英雄", "desc": "紧身战衣，英雄降临"},
        {"id": "yundong", "icon": "\U0001F3C3", "name": "运动装备", "desc": "运动背心+短裤，活力满满"},
    ],
    "hair": [
        {"id": "long", "icon": "\U0001F481\u200D\u2640\uFE0F", "name": "长发飘逸", "desc": "柔顺长发，随风飘逸"},
        {"id": "short", "icon": "\U0001F487", "name": "短发干练", "desc": "利落短发，清爽干练"},
        {"id": "ponytail", "icon": "\U0001F434", "name": "高马尾辫", "desc": "高高束起的马尾，活力四射"},
        {"id": "bun", "icon": "\U0001F361", "name": "丸子头", "desc": "可爱丸子头，俏皮减龄"},
        {"id": "gufa", "icon": "\U0001F451", "name": "古装发髻", "desc": "精致古风发髻配发簪"},
        {"id": "cundou", "icon": "\U0001F488", "name": "寸头短发", "desc": "极短寸头，硬朗阳刚"},
        {"id": "twintail", "icon": "\U0001F380", "name": "双马尾", "desc": "左右双马尾，甜美可爱"},
        {"id": "wave", "icon": "\U0001F30A", "name": "波浪卷发", "desc": "大波浪卷发，成熟妩媚"},
        {"id": "curly", "icon": "\U0001F468\u200D\U0001F9B1", "name": "自然卷", "desc": "自然卷曲短发，慵懒随性"},
        {"id": "oilhead", "icon": "\U0001FAEE", "name": "复古油头", "desc": "经典油头造型，绅士风度"},
    ],
    "scene": [
        {"id": "palace", "icon": "\U0001F3DB", "name": "宫殿大殿", "desc": "金碧辉煌的古代宫殿"},
        {"id": "city", "icon": "\U0001F306", "name": "现代都市", "desc": "霓虹灯下的繁华都市"},
        {"id": "bamboo", "icon": "\U0001F38B", "name": "竹林山涧", "desc": "幽静竹林，流水潺潺"},
        {"id": "space", "icon": "\U0001F680", "name": "太空舱", "desc": "科幻太空舱内部"},
        {"id": "beach", "icon": "\U0001F3D6", "name": "海滩日落", "desc": "金色沙滩，落日余晖"},
        {"id": "castle", "icon": "\U0001F3F0", "name": "欧式城堡", "desc": "中世纪欧式古堡"},
        {"id": "rain", "icon": "\U0001F327", "name": "雨夜街头", "desc": "霓虹灯下的雨夜街道"},
        {"id": "sakura", "icon": "\U0001F338", "name": "樱花庭院", "desc": "樱花盛开的日式庭院"},
        {"id": "snow", "icon": "\u2744", "name": "冰雪世界", "desc": "白雪皑皑的冰雪王国"},
        {"id": "cyber", "icon": "\U0001F3D9", "name": "赛博朋克", "desc": "赛博朋克风格的未来城市"},
    ],
}

@router.get("/presets")
async def get_presets(category: str = ""):
    all_presets = {
        "tabs": [
            {"key": "outfit", "label": "\U0001F457 换装"},
            {"key": "hair", "label": "\U0001F487 换发型"},
            {"key": "scene", "label": "\U0001F3DE 换场景"},
        ],
        "presets": PRESETS,
    }
    return {"success": True, "data": all_presets}
