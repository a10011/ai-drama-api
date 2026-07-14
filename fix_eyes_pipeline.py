#!/usr/bin/env python3
"""Insert face beautification post-processing into pipeline.py"""

insert_block = '''
        # 角色肖像后处理：OpenCV 眼周美颜
        if image_url:
            try:
                from services.face_beautify import beautify_portrait
                import tempfile, os, hashlib
                from routers.media_router import save_and_register
                # Download and beautify
                beautified_local = beautify_portrait(image_url, strength=0.8)
                with open(beautified_local, "rb") as f:
                    img_data = f.read()
                h = hashlib.md5(img_data).hexdigest()[:12]
                fname = "portrait_face_beautified_%s.jpg" % h
                # Save with proper metadata
                result_info = save_and_register(
                    img_data, fname, "figures",
                    name="肖像美颜后处理",
                )
                if result_info and result_info.get("url"):
                    image_url = result_info["url"]
                # Cleanup temp file
                try:
                    os.remove(beautified_local)
                except:
                    pass
            except Exception as e:
                logger.warning("Face beautification post-process failed (non-critical): %s" % str(e))

'''

with open("routers/pipeline.py", "r") as f:
    content = f.read()

# Find the return statement that returns image_url
target = '        return {"success": bool(image_url), "data": {"image_url": image_url}, "error": "" if image_url else "生成失败"}'
if target not in content:
    print("ERROR: Could not find target return statement")
    idx = content.find('return {"success": bool(image_url)')
    if idx >= 0:
        print("Found at position %d" % idx)
        print("Context: >>>%s<<<" % content[idx:idx+200])
    exit(1)

idx = content.find(target)
content = content[:idx] + insert_block + content[idx:]
with open("routers/pipeline.py", "w") as f:
    f.write(content)
print("Inserted face beautification post-processing before return")
