# Archived strategy and quoting-engine tests

Tests for legacy strategies and the quoting engine archived on **2026-07-02**. Not collected by default pytest runs
(`norecursedirs` excludes `_archive`).

Run manually if needed:

```bash
pytest tests/_archive/ -v
PYTHONPATH=_archive:. pytest tests/_archive/quoting_engine/ -v
```
