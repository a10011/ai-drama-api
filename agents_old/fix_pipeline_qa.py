c = open('/www/wwwroot/api.mzsh.top/routers/pipeline.py').read()
# 在 data = _call_agent(...) 之后加质检
old = '''                    data = _call_agent(aid, act, director_notes=director_notes, **(params or {}))
                    preview = None'''
new = '''                    data = _call_agent(aid, act, director_notes=director_notes, **(params or {}))
                    # 质检
                    if data and sname not in ("字幕生成",):
                        try:
                            from agents.agent_qa import qa
                            qa_result = qa.check(sname.replace(" ",""), data, {"genre": genre})
                            if not qa_result["passed"]:
                                logger.warning(f"  [QA] {sname} 不合格，重做...")
                                for r in range(3):
                                    data2 = _call_agent(aid, act, director_notes=director_notes, **(params or {}))
                                    if data2:
                                        qa2 = qa.check(sname.replace(" ",""), data2, {"genre": genre})
                                        if qa2["passed"]:
                                            data = data2
                                            logger.info(f"  [QA] {sname} 第{r+1}次重做通过")
                                            break
                                        data = data2
                        except Exception as e:
                            logger.debug(f"  质检异常(跳过): {e}")
                    preview = None'''

c = c.replace(old, new)
open('/www/wwwroot/api.mzsh.top/routers/pipeline.py','w').write(c)
print('fixed')
