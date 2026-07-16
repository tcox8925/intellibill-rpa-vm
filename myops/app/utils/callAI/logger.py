import logging


LOG_LEVEL = "DEBUG"

if isinstance(LOG_LEVEL, str):
    LOG_LEVEL = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

logging.basicConfig(
    level=LOG_LEVEL,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] [%(filename)s:%(funcName)s:%(lineno)d] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("speech-app")
