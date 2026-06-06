import mysql.connector
from mysql.connector import pooling
from config import Config
import time

connection_pool = pooling.MySQLConnectionPool(
    pool_name="cricket_pool",
    pool_size=5,
    pool_reset_session=True,
    host=Config.DB_HOST,
    port=Config.DB_PORT,
    user=Config.DB_USER,
    password=Config.DB_PASSWORD,
    database=Config.DB_NAME,
    connection_timeout=30,
    autocommit=True
)

def get_db():
    return connection_pool.get_connection()

_cache = {}

def get_cached(key, fetch_fn, ttl_seconds=30):
    now = time.time()
    if key in _cache:
        value, expiry = _cache[key]
        if now < expiry:
            return value
    value = fetch_fn()
    _cache[key] = (value, now + ttl_seconds)
    return value

def clear_cache(key):
    keys_to_remove = [k for k in _cache.keys() if k == key or k.startswith(key)]
    for k in keys_to_remove:
        del _cache[k]