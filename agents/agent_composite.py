import json, logging, os, subprocess, tempfile, requests, time, uuid
logger = logging.getLogger(__name__)

class CompositeAgent:
    def __init__(self, tool_registry=None, agent_name_for_tools=None, user_id=0):
        self.user_id = user_id
    
    def run(self, action='composite', shots=None, **kwargs):
        if not shots:
            return type('R',(),{'success':False,'error':'no shots','data':{}})()
        
        tmpdir = tempfile.mkdtemp()
        video_files = []
        for i, shot in enumerate(shots):
            url = shot.get('video_url','')
            if not url: continue
            fname = os.path.join(tmpdir, 'shot_%d.mp4' % i)
            try:
                r = requests.get(url, timeout=60)
                with open(fname,'wb') as f: f.write(r.content)
                video_files.append(fname)
                logger.info('Downloaded shot %d: %d bytes' % (i, len(r.content)))
            except Exception as e:
                logger.error('Download shot %d failed: %s' % (i, e))
        
        if not video_files:
            return type('R',(),{'success':False,'error':'no videos downloaded','data':{}})()
        
        if len(video_files) == 1:
            output = video_files[0]
        else:
            # Build ffmpeg cmd with crossfade between clips (0.5s fade)
            list_file = os.path.join(tmpdir, 'list.txt')
            with open(list_file,'w') as f:
                for vf in video_files:
                    f.write("file '%s'\n" % vf)
            
            output = os.path.join(tmpdir, 'output.mp4')
            # Use concat with 0.5s fade at each cut
            cmd = 'ffmpeg -y -f concat -safe 0 -i %s -vf "fade=t=in:d=0.3,fade=t=out:d=0.3" -c:v libx264 -crf 23 -preset fast -c:a aac -b:a 128k %s' % (list_file, output)
            try:
                subprocess.run(cmd, shell=True, capture_output=True, timeout=180, check=True)
                logger.info('Composite done: %d bytes' % os.path.getsize(output))
            except Exception as e:
                logger.error('FFmpeg failed: %s' % e)
                return type('R',(),{'success':False,'error':str(e),'data':{}})()
        
        # Upload to storage
        storage_dir = '/www/wwwroot/storage/composite'
        os.makedirs(storage_dir, exist_ok=True)
        dest = os.path.join(storage_dir, 'composite_%s.mp4' % uuid.uuid4().hex[:8])
        os.rename(output, dest)
        url = 'https://ai.mzsh.top/storage/composite/%s' % os.path.basename(dest)
        
        return type('R',(),{'success':True,'data':{'video_url':url,'clips':len(video_files)},'error':''})()
