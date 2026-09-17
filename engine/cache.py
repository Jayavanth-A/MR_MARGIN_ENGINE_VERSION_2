import hashlib
import json
from pathlib import Path
from typing import Any, Optional


class CacheManager:
    """Manages deterministic file-based caching for LLM requests."""

    def __init__(self, base_cache_dir: Path):
        self.base_cache_dir = Path(base_cache_dir)
        self.base_cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_namespace_dir(self, namespace: str) -> Path:
        ns_dir = self.base_cache_dir / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)
        return ns_dir

    def _compute_key_hash(self, key_data: Any) -> str:
        serialized = json.dumps(key_data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, namespace: str, key_data: Any) -> Optional[dict]:
        """Retrieve cached response if available."""
        key_hash = self._compute_key_hash(key_data)
        cache_file = self._get_namespace_dir(namespace) / f"{key_hash}.json"
        if cache_file.exists():
            try:
                content = cache_file.read_text(encoding="utf-8")
                return json.loads(content)
            except Exception:
                return None
        return None

    def set(self, namespace: str, key_data: Any, value: dict) -> None:
        """Store response in cache."""
        key_hash = self._compute_key_hash(key_data)
        cache_file = self._get_namespace_dir(namespace) / f"{key_hash}.json"
        try:
            cache_file.write_text(
                json.dumps(value, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            # Non-fatal error if caching fails
            pass

    def clear(self, namespace: Optional[str] = None) -> None:
        """Clear cache for a specific namespace or all namespaces."""
        target_dir = self._get_namespace_dir(namespace) if namespace else self.base_cache_dir
        if target_dir.exists():
            for p in target_dir.glob("**/*.json"):
                try:
                    p.unlink()
                except Exception:
                    pass
