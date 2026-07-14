"""
实时余额监控 — /api/v1/admin/balances
"""
import requests, json, os
from datetime import datetime

KEYS_FILE = '/www/wwwroot/api.mzsh.top/config/api_keys.json'

def check_deepseek() -> dict:
    """DeepSeek — 有实时API"""
    with open(KEYS_FILE) as f:
        key = json.load(f).get('deepseek',{}).get('key','')
    try:
        r = requests.get('https://api.deepseek.com/user/balance',
            headers={'Authorization': f'Bearer {key}'}, timeout=10)
        if r.status_code == 200:
            d = r.json()
            infos = []
            for bi in d.get('balance_infos',[]):
                infos.append({
                    'currency': bi.get('currency','CNY'),
                    'total': float(bi.get('total_balance',0)),
                    'granted': float(bi.get('granted_balance',0)),
                    'topped_up': float(bi.get('topped_up_balance',0)),
                })
            return {
                'provider': 'deepseek',
                'live': True,
                'updated': datetime.now().isoformat(),
                'balances': infos,
                'total_cny': sum(i['total'] for i in infos),
            }
    except Exception as e:
        return {'provider': 'deepseek', 'live': False, 'error': str(e)[:100]}

def get_all_balances() -> dict:
    """聚合所有平台余额"""
    result = {
        'updated': datetime.now().isoformat(),
        'providers': {}
    }
    
    # DeepSeek — 实时
    result['providers']['deepseek'] = check_deepseek()
    
    # 智谱 — 网页后台
    result['providers']['zhipu'] = {
        'provider': 'zhipu',
        'live': False,
        'note': '无API接口，请访问后台查看',
        'dashboard': 'https://open.bigmodel.cn/usercenter/providers/billing/overview',
    }
    
    # 阿里百炼
    result['providers']['bailian'] = {
        'provider': 'aliyun_bailian',
        'live': False,
        'note': '无API接口，请访问后台查看',
        'dashboard': 'https://bailian.console.aliyun.com/#/billing',
    }
    
    # 火山方舟
    result['providers']['ark'] = {
        'provider': 'volcano_ark',
        'live': False,
        'note': '无API接口，请访问后台查看',
        'dashboard': 'https://console.volcengine.com/ark/region:ark+cn-beijing/billing',
    }
    
    # 可灵
    result['providers']['kling'] = {
        'provider': 'kling',
        'live': False,
        'note': '无API接口，请访问后台查看',
        'dashboard': 'https://platform.klingai.com/billing',
    }
    
    return result

def get_balance_endpoint():
    """返回FastAPI路由用的handler"""
    from fastapi import APIRouter
    router = APIRouter()
    
    @router.get("/admin/balances")
    async def balances():
        return {"success": True, "data": get_all_balances()}
    
    return router

# 独立运行测试
if __name__ == '__main__':
    result = get_all_balances()
    print(json.dumps(result, indent=2, ensure_ascii=False))
