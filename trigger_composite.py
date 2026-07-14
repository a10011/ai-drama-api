#!/usr/bin/env python3
"""触发 10011152 的 composite 合成阶段"""
import requests
try:
    r = requests.post("http://127.0.0.1:8000/api/v1/pipeline/step/10011152",
                      json={"stage": "composite", "params": {}}, timeout=15)
    print("status", r.status_code)
    print(r.text[:400])
except Exception as e:
    print("ERR", e)
