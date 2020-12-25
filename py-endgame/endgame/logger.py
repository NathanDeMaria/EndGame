import logging
import sys

root_logger = logging.getLogger("endgame")
root_logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s [%(name)s] [%(levelname)s] %(message)s")
handler.setFormatter(formatter)
root_logger.addHandler(handler)
