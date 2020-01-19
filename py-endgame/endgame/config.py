import os
from pathlib import Path

# TODO: implement other types
def config_value(name: str, default: str):
    def get(_):
        return os.getenv(name, default)
    return property(fget=get)
    

class _Config:
    cache_dir = config_value('ENDGAME_CACHE_DIR', str(Path.home() / '.endgame' / 'cache'))


CONFIG = _Config()
