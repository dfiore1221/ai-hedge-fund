import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / "data_cache"
_NAMESPACE_PAYLOAD_CACHE = {}


def get_cached_json(namespace, key, ttl_seconds):
    path = cache_path(namespace, key)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    cached_at = parse_timestamp(payload.get("cached_at"))
    if not cached_at:
        return None

    age_seconds = (datetime.now() - cached_at).total_seconds()
    if age_seconds > ttl_seconds:
        return None

    return payload.get("data")


def get_stale_cached_json(namespace, key, max_age_seconds=None):
    path = cache_path(namespace, key)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    cached_at = parse_timestamp(payload.get("cached_at"))
    if max_age_seconds and cached_at:
        age_seconds = (datetime.now() - cached_at).total_seconds()
        if age_seconds > max_age_seconds:
            return None

    data = payload.get("data")
    if isinstance(data, dict):
        data = {
            **data,
            "cache": {
                "status": "stale_fallback",
                "cached_at": payload.get("cached_at"),
            },
        }
    return data


def get_latest_stale_cached_json_by_prefix(namespace, key_prefix, max_age_seconds=None):
    candidates = []
    for payload in cached_namespace_payloads(namespace):
        if not str(payload.get("key", "")).startswith(key_prefix):
            continue
        cached_at = parse_timestamp(payload.get("cached_at"))
        if max_age_seconds and cached_at:
            age_seconds = (datetime.now() - cached_at).total_seconds()
            if age_seconds > max_age_seconds:
                continue
        candidates.append((cached_at or datetime.min, payload))

    if not candidates:
        return None

    _, payload = max(candidates, key=lambda item: item[0])
    data = payload.get("data")
    if isinstance(data, dict):
        data = {
            **data,
            "cache": {
                "status": "stale_fallback",
                "cached_at": payload.get("cached_at"),
                "matched_key": payload.get("key"),
            },
        }
    return data


def cached_namespace_payloads(namespace):
    safe_namespace = sanitize_segment(namespace)
    namespace_dir = CACHE_DIR / safe_namespace
    if safe_namespace in _NAMESPACE_PAYLOAD_CACHE:
        return _NAMESPACE_PAYLOAD_CACHE[safe_namespace]
    if not namespace_dir.exists():
        _NAMESPACE_PAYLOAD_CACHE[safe_namespace] = []
        return []

    payloads = []
    for path in namespace_dir.glob("*.json"):
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    _NAMESPACE_PAYLOAD_CACHE[safe_namespace] = payloads
    return payloads


def set_cached_json(namespace, key, data):
    path = cache_path(namespace, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cached_at": datetime.now().isoformat(timespec="seconds"),
        "namespace": namespace,
        "key": key,
        "data": data,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _NAMESPACE_PAYLOAD_CACHE.pop(sanitize_segment(namespace), None)
    return path


def cache_path(namespace, key):
    safe_namespace = sanitize_segment(namespace)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / safe_namespace / f"{digest}.json"


def sanitize_segment(value):
    return "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in str(value))


def parse_timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def cache_summary():
    if not CACHE_DIR.exists():
        return {
            "path": str(CACHE_DIR),
            "file_count": 0,
            "size_bytes": 0,
        }

    files = [path for path in CACHE_DIR.rglob("*.json") if path.is_file()]
    return {
        "path": str(CACHE_DIR),
        "file_count": len(files),
        "size_bytes": sum(path.stat().st_size for path in files),
        "latest_updated_at": latest_mtime(files),
    }


def latest_mtime(files):
    if not files:
        return None
    latest = max(path.stat().st_mtime for path in files)
    return datetime.fromtimestamp(latest).isoformat(timespec="seconds")


def ttl_seconds(**kwargs):
    return int(timedelta(**kwargs).total_seconds())
