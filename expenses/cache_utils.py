"""Per-user cache versioning: bump once and every cached summary/chart is stale."""
from django.core.cache import cache

TTL = 60 * 60


def version(user_id: int) -> int:
    return cache.get_or_set(f"exp:ver:{user_id}", 1, None)


def bump(user_id: int) -> None:
    try:
        cache.incr(f"exp:ver:{user_id}")
    except ValueError:
        cache.set(f"exp:ver:{user_id}", 2, None)


def cached(user_id: int, name: str, build):
    key = f"exp:{user_id}:v{version(user_id)}:{name}"
    value = cache.get(key)
    if value is None:
        value = build()
        cache.set(key, value, TTL)
    return value
