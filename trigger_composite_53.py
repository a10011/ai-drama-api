#!/usr/bin/env python3
"""触发 10011153 的 composite 合成"""
import requests
try:
    r = requests.post("http://127.0.0.1:8000/api/v1/pipeline/step/10011153",
                      json={"stage": "composite", "params": {}}, timeout=15)
    print("status", r.status_code)
    print(r.text[:400])
except Exception as e:
    print("ERR", e)
