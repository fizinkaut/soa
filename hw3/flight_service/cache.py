"""Cache-Aside helpers. PostgreSQL revision prevents serving stale cache entries."""
import logging
import os

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from google.protobuf.message import DecodeError

logger = logging.getLogger(__name__)
TTL = int(os.getenv("CACHE_TTL_SECONDS", "300"))
if not 300 <= TTL <= 600:
    raise ValueError("CACHE_TTL_SECONDS must be between 300 and 600")

client = redis.Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    socket_connect_timeout=0.3,
    socket_timeout=0.3,
    retry=Retry(NoBackoff(), 0),
)


def revision(cursor):
    cursor.execute("SELECT revision FROM cache_state WHERE id = 1")
    return cursor.fetchone()["revision"]


def invalidate(cursor):
    # Committed atomically with the seat changes. Old keys expire by TTL.
    cursor.execute("UPDATE cache_state SET revision = revision + 1 WHERE id = 1")


def get(key, message_type):
    try:
        value = client.get(key)
        if value is not None:
            message = message_type.FromString(value)
            logger.info("cache hit %s", key)
            return message
    except (redis.RedisError, DecodeError) as exc:
        logger.warning("Cache read failed: %s", exc)
    logger.info("cache miss %s", key)
    return None


def put(key, message):
    try:
        client.set(key, message.SerializeToString(), ex=TTL)
    except redis.RedisError as exc:
        logger.warning("Cache write failed: %s", exc)
