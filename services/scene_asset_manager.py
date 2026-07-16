"""
场景资产库管理器 — 跨集复用场景图
"""
import json, logging, os, sqlite3
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class SceneAssetManager:
    """场景资产库管理器"""
    
    def __init__(self, db_path: str = "/www/wwwroot/api.mzsh.top/data/short_drama.db"):
        self.db_path = db_path
    
    def get_scene_library(self, project_id: str) -> Optional[Dict]:
        """获取项目的场景资产库"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT scene_asset_lib FROM projects WHERE id=?",
                (project_id,)
            ).fetchone()
            conn.close()
            if row and row["scene_asset_lib"]:
                return json.loads(row["scene_asset_lib"])
            return None
        except Exception as e:
            logger.error(f"获取场景资产库失败: {e}")
            return None
    
    def save_scene_library(self, project_id: str, asset_lib: Dict):
        """保存场景资产库"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE projects SET scene_asset_lib=? WHERE id=?",
                (json.dumps(asset_lib, ensure_ascii=False), project_id)
            )
            conn.commit()
            conn.close()
            logger.info(f"保存场景资产库: {project_id}")
        except Exception as e:
            logger.error(f"保存场景资产库失败: {e}")
    
    def get_scene_by_location(self, asset_lib: Dict, location: str, emotion: str = "") -> Optional[str]:
        """根据场景名和情绪获取场景图提示词"""
        if not asset_lib or not location:
            return None
        
        for scene in asset_lib.get("主场景设定图", []):
            if scene.get("场景名") == location:
                # 如果有情绪要求，查找对应氛围图
                if emotion:
                    for atmosphere in scene.get("情绪氛围图", []):
                        if atmosphere.get("情绪") == emotion:
                            return atmosphere.get("提示词", "")
                else:
                    # 返回主场景图
                    return scene.get("主场景提示词", "")
        return None
    
    def get_all_scenes(self, asset_lib: Dict) -> List[Dict]:
        """获取所有场景资产"""
        if not asset_lib:
            return []
        return asset_lib.get("主场景设定图", [])
