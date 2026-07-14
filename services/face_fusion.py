import requests,logging,json,time,hmac,hashlib,datetime
logger=logging.getLogger('FaceFusion')

class FaceFusionAPI:
    def _get_key(self,name):
        import json,os
        try:
            with open(os.path.join(os.path.dirname(__file__),'..','config','api_keys.json')) as f:
                keys=json.load(f)
            return keys.get(name,{}).get('key','')
        except: return ''
    
    def __init__(self):
        from app_config import settings
        self.ak=getattr(settings,'VOLC_ACCESS_KEY','')
        self.sk=getattr(settings,'VOLC_SECRET_KEY','')
        self.base='https://visual.volcengineapi.com'
        self.service='cv'
        self.region='cn-north-1'
    
    def _sign(self,method,uri,query,headers,body):
        now=datetime.datetime.utcnow()
        xdate=now.strftime('%Y%m%dT%H%M%SZ')
        shortdate=now.strftime('%Y%m%d')
        headers['X-Date']=xdate
        headers['Host']='visual.volcengineapi.com'
        # Sort headers
        signed_headers=';'.join(sorted([k.lower() for k in headers]))
        canonical='\n'.join([method,uri,query,
            '\n'.join([f'{k.lower()}:{headers[k]}' for k in sorted(headers)]),
            '',signed_headers,hashlib.sha256(body.encode()).hexdigest()])
        scope=f'{shortdate}/{self.region}/{self.service}/request'
        string_to_sign='\n'.join(['HMAC-SHA256',xdate,scope,hashlib.sha256(canonical.encode()).hexdigest()])
        k=hmac.new(f'{self.sk}'.encode(),shortdate.encode(),hashlib.sha256)
        k=hmac.new(k.digest(),self.region.encode(),hashlib.sha256)
        k=hmac.new(k.digest(),self.service.encode(),hashlib.sha256)
        k=hmac.new(k.digest(),'request'.encode(),hashlib.sha256)
        sig=hmac.new(k.digest(),string_to_sign.encode(),hashlib.sha256).hexdigest()
        headers['Authorization']=f'HMAC-SHA256 Credential={self.ak}/{shortdate}/{self.region}/{self.service}/request,SignedHeaders={signed_headers},Signature={sig}'
    
    def swap_face(self,source_url,template_url,similarity=0.7):
        body=json.dumps({
            'req_key':'face_swap3_6',
            'image_urls':[source_url,template_url],
            'source_similarity':str(similarity),
            'return_url':True,
            'logo_info':{'add_logo':False}
        })
        headers={'Content-Type':'application/json'}
        self._sign('POST','/',f'Action=CVProcess&Version=2022-08-31',headers,body)
        try:
            r=requests.post(f'{self.base}/?Action=CVProcess&Version=2022-08-31',
                headers=headers,data=body,timeout=30)
            if r.status_code==200:
                data=r.json()
                code=data.get('ResponseMetadata',{}).get('Error',{}).get('Code','')
                if code:
                    logger.error(f'CVProcess error {code}: {data["ResponseMetadata"]["Error"].get("Message","")}')
                    return ''
                return data.get('data',{}).get('image_url','')
            return ''
        except Exception as e:
            logger.error(f'FaceSwap failed: {e}')
            return ''

# Built-in age templates (generated once, reused forever)
AGE_TEMPLATES={
    '少年':'https://ai.mzsh.top/storage/templates/teen_male.jpg',
    '青年':'https://ai.mzsh.top/storage/templates/youth_male.jpg',
    '中年':'https://ai.mzsh.top/storage/templates/mid_male.jpg',
    '老年':'https://ai.mzsh.top/storage/templates/old_male.jpg'
}

class SmartAgePortrait:
    def __init__(self):
        self.fusion=FaceFusionAPI()
    
    def generate_all_ages(self,base_portrait_url,char_name):
        results={}
        for stage,template in AGE_TEMPLATES.items():
            url=self.fusion.swap_face(base_portrait_url,template,similarity=0.7)
            if url:
                results[stage]=url
                logger.info(f'{char_name} {stage}: fused OK')
            else:
                results[stage]=base_portrait_url
        return results

smart_age=SmartAgePortrait()
