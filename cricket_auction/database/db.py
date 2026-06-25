import mysql.connector
from mysql.connector import pooling
from config import Config
import time

# Optimized for Render free tier: 1 worker, limited connections
connection_pool = pooling.MySQLConnectionPool(
    pool_name="cricket_pool",
    pool_size=3,              # Reduced from 5 — Render free has 512MB RAM
    pool_reset_session=False,  # CHANGED: Faster — don't reset session state
    host=Config.DB_HOST,
    port=Config.DB_PORT,
    user=Config.DB_USER,
    password=Config.DB_PASSWORD,
    database=Config.DB_NAME,
    connection_timeout=10,    # Reduced from 30 — fail fast
    connect_timeout=10,
    read_timeout=10,
    write_timeout=10,
    autocommit=False,         # CHANGED: Critical — batch transactions
    charset='utf8mb4',
    collation='utf8mb4_unicode_ci',
    use_pure=False,           # Use C extension if available (faster)
    consume_results=True        # Auto-clean cursors
)

def get_db():
    """Get connection from pool. Must call commit() or rollback() manually."""
    conn = connection_pool.get_connection()
    conn.autocommit = False    # Ensure
    return conn

# === SIMPLE IN-MEMORY CACHE (process-local, fast) ===
_cache = {}
_cache_hits = 0
_cache_misses = 0

def get_cached(key, fetch_fn, ttl_seconds=15):
    """Get from cache or fetch. Shorter TTL for live auction data."""
    global _cache_hits, _cache_misses
    now = time.time()
    
    if key in _cache:
        value, expiry = _cache[key]
        if now < expiry:
            _cache_hits += 1
            return value
    
    _cache_misses += 1
    value = fetch_fn()
    _cache[key] = (value, now + ttl_seconds)
    return value

def clear_cache(key=None):
    """Clear cache by key, prefix, or all."""
    global _cache
    if key is None:
        _cache = {}
        return
    
    keys_to_remove = [k for k in _cache.keys() if k == key or k.startswith(key)]
    for k in keys_to_remove:
        _cache.pop(k, None)

def cache_stats():
    """Debug: cache hit rate."""
    total = _cache_hits + _cache_misses
    if total == 0:
        return "No cache activity yet"
    rate = (_cache_hits / total) * 100
    return f"Cache: {_cache_hits} hits, {_cache_misses} misses, {rate:.1f}% hit rate"

# === CONNECTION CONTEXT MANAGER (safer, cleaner) ===
class db_transaction:
    """Context manager for safe DB transactions. Use this everywhere."""
    
    def __init__(self, cursor_dict=True):
        self.cursor_dict = cursor_dict
        self.conn = None
        self.cursor = None
    
    def __enter__(self):
        self.conn = get_db()
        self.cursor = self.conn.cursor(dictionary=self.cursor_dict)
        return self.cursor
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()