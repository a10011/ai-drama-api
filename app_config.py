import os
from core.config import Config
config = Config()
BASE_URL = os.environ.get('BASE_URL', 'https://ai.mzsh.top')
