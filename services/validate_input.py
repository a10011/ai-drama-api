"""model_client 输入自查 — 失败后先验证内容，确认合格才进重试队列"""
import logging

logger = logging.getLogger("model_client")

MODEL_SPECS = {
    "hidream": {
        "prompt_max_chars": 2000,
        "prompt_min_chars": 3,
        "valid_sizes": ["1024x1024", "768x1365", "1365x768"],
        "forbidden_patterns": [],
    },
    "seedream": {
        "prompt_max_chars": 2000,
        "prompt_min_chars": 1,
        "valid_sizes": ["2560x1440", "1920x1920", "1440x2560", "1280x720"],
        "forbidden_patterns": ["sexual", "nude", "naked", "porn", "violence"],
    },
    "seedance": {
        "prompt_max_chars": 500,
        "prompt_min_chars": 1,
        "valid_resolutions": ["480P", "720P", "1080P"],
        "forbidden_patterns": [],
        "require_image": True,
    },
    "kling": {
        "prompt_max_chars": 2500,
        "prompt_min_chars": 1,
        "valid_resolutions": ["720P", "1080P"],
        "forbidden_patterns": [],
    },
    "wanxiang": {
        "prompt_max_chars": 2000,
        "prompt_min_chars": 1,
        "valid_sizes": ["1024x1024"],
        "forbidden_patterns": [],
    },
    "happyhorse": {
        "prompt_max_chars": 500,
        "prompt_min_chars": 1,
        "valid_resolutions": ["720P"],
        "forbidden_patterns": [],
    },
}

def validate_input(call_type: str, model_name: str, call_args: dict) -> dict:
    """验证模型输入参数"""
    specs = MODEL_SPECS.get(model_name)
    issues = []
    fixes = []
    
    prompt = call_args.get("prompt", "")
    if not prompt or len(prompt.strip()) < 3:
        issues.append(f"prompt 为空或过短 ({len(prompt)}字符)")
        return {"ok": False, "issues": issues, "fixes": []}
    
    if specs:
        if len(prompt) > specs["prompt_max_chars"]:
            truncated = prompt[:specs["prompt_max_chars"] - 3] + "..."
            issues.append(f"prompt 过长 {len(prompt)} > {specs['prompt_max_chars']}字符")
            fixes.append({"field": "prompt", "before_len": len(prompt), 
                         "after_len": len(truncated), "action": "truncate"})
        
        for pattern in specs.get("forbidden_patterns", []):
            if pattern.lower() in prompt.lower():
                issues.append(f"prompt 含禁止词: '{pattern}'")
        
        if call_type == "image":
            size = call_args.get("size", "")
            if specs.get("valid_sizes") and size and size not in specs["valid_sizes"]:
                issues.append(f"尺寸 {size} 不在有效范围 {specs['valid_sizes']}")
        
        if call_type == "video":
            res = call_args.get("resolution", "")
            if specs.get("valid_resolutions") and res and res not in specs["valid_resolutions"]:
                issues.append(f"分辨率 {res} 不在有效范围 {specs['valid_resolutions']}")
        
        if call_type == "video" and specs.get("require_image"):
            img = call_args.get("image_url", "")
            if not img or len(img) < 5:
                issues.append(f"视频生成缺少参考图 (model={model_name} 要求有图)")
    
    if prompt and len(prompt.strip()) < specs["prompt_min_chars"] if specs else 3:
        issues.append(f"prompt 过短 ({len(prompt.strip())}字符)")
    
    if prompt and len(prompt.strip()) == 0:
        issues.append("prompt 全为空白字符")
    
    if prompt and (prompt.startswith("{") and not prompt.strip().endswith("}")):
        issues.append("prompt 看起来像被截断的 JSON")
    
    dangerous = ["def ", "function(", "=>", "import ", "require(", "```"]
    for d in dangerous:
        if d in prompt.lower():
            issues.append(f"prompt 疑似包含代码残留: '{d}'")
            break
    
    fatal_issues = [i for i in issues if "过长" not in i]
    fixable_issues = [i for i in issues if "过长" in i]
    
    return {
        "ok": len(fatal_issues) == 0,
        "issues": issues,
        "fatal_issues": fatal_issues,
        "fixable_issues": fixable_issues,
        "fixes": fixes,
        "prompt_preview": prompt[:200] + ("..." if len(prompt) > 200 else ""),
        "prompt_len": len(prompt),
    }