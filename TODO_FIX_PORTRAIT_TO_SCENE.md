# 紧急修复：角色肖像 → 场景图锁脸链路

## 当前管线顺序（正确）
```
CHARACTER → STORYBOARD → SCENE
```

## 问题
CHARACTER 阶段的 `_post_gen_portraits` 是 post_process，肖像图生成了但 portrait_url 可能没正确传递到下游：
- STORYBOARD agent 拿到的 characters 里缺少 portrait_url
- SCENE agent 拿到的 characters 里也缺少 portrait_url
- scene agent _gen_one 匹配不到角色肖像 → 只跑纯文生图 → 脸锁不住

## 需要你做
1. 排查 portrait_url 的数据流：_post_gen_portraits → ctx.characters → STORYBOARD/SCENE agent 的入参
2. 修复：确保场景图生成时能拿到角色肖像做 i2i 参考
3. 改完 `pm2 restart ai-drama-api`

## 关键文件
- /www/wwwroot/api.mzsh.top/services/orchestrator.py
- /www/wwwroot/api.mzsh.top/agents/agent_scene.py
