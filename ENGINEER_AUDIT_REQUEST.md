# Codex 工程师，请审计并确认流程

## 用户要的完整流程

1. 用户上传剧本
2. 系统提取角色 → 生成角色肖像图
3. **确认角色肖像图完成后** → 才开始生成分镜
4. 分镜完成后 → 场景图生成（用角色肖像图做 i2i 参考，锁脸）
5. 后续：配音、字幕、BGM、视频生成、合成 → 最终输出可下载视频

## 用户的核心要求

> "我必须要角色建模好了才出分镜"
> "生场景图不调我角色图，脸怎么锁"
> "角色建模好了才出分镜"

## 你需要审计确认的问题

### 1. 角色肖像完成 → 分镜开始 的时序是否正确？
- `_post_gen_portraits` 是 CHARACTER 阶段的 post_process
- 调用是在 CHARACTER agent 返回之后、set_result 和 _sync_top_level 之后
- 但 STORYBOARD 阶段的依赖声明是 `[DIRECTOR, SCRIPT, CHARACTER]`
- **确认**：CHARACTER 阶段全部完成（含 post_process 肖像生成）之后，STORYBOARD 才开始

### 2. portrait_url 数据流是否100%正确？
- `_post_gen_portraits` 生成肖像后写回 `self.ctx.characters[i]["portrait_url"]`
- SCENE 阶段 param_map `"characters": "characters"` 取的是 `self.ctx.characters`
- scene agent `_batch_generate` 里 `char_photo_map` 构建时查 `portrait_url` → `photo` → `avatar` → `image_url`
- **确认**：这条链路有没有断的地方？

### 3. image_to_image 调用参数是否完整？
- 今天修了一次：去掉了 `project_id` 参数（函数签名不认识它）
- `generate_scene_image` 里 i2i 调用正确传了：prompt, reference_image, size, timeout, strength
- **确认**：i2i 是否真的在用角色肖像图做参考？seedream 支不支持 i2i？

### 4. 还有什么遗漏？
- 如果 portrait_url 没生成（模型失败），fallback 逻辑是什么？
- 用户编辑过角色后重新提交，肖像会不会覆盖？
- resume 时肖像图是否能正确复用？

## 文件清单
- `/www/wwwroot/api.mzsh.top/services/orchestrator.py` — 管线编排
- `/www/wwwroot/api.mzsh.top/agents/agent_scene.py` — 场景生成 agent
- `/www/wwwroot/api.mzsh.top/services/model_client.py` — 模型调用
- `/www/wwwroot/api.mzsh.top/routers/pipeline.py` — 管线 API

## 改完了告诉我
1. 确认了哪些点是对的
2. 发现了什么问题、怎么修的
3. 现在能不能跑通角色→场景锁脸

用户直接对话中，急。
