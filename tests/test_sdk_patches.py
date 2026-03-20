"""Tests for parent/sdk_patches.py — SDK monkey-patching."""
from __future__ import annotations

import importlib
import sys
from unittest import mock

import pytest

import parent.sdk_patches as sdk_patches_mod
from parent.sdk_patches import patch_spot_meta_indexing


class TestPatchSpotMetaIndexing:
    def setup_method(self):
        # Reset the idempotency flag before each test
        sdk_patches_mod._spot_meta_patched = False

    def test_idempotent(self):
        """Calling twice should only apply the patch once (no error)."""
        # When SDK not installed, patch is a no-op; just verify no exception
        with mock.patch.dict(sys.modules, {"hyperliquid.info": None}):
            # First call — may or may not apply depending on import
            sdk_patches_mod._spot_meta_patched = False
            patch_spot_meta_indexing()
            assert sdk_patches_mod._spot_meta_patched is True

            # Second call — should be a no-op
            patch_spot_meta_indexing()
            assert sdk_patches_mod._spot_meta_patched is True

    def test_graceful_when_sdk_not_installed(self):
        """Should not raise when hyperliquid SDK is not installed."""
        sdk_patches_mod._spot_meta_patched = False

        # Force ImportError for hyperliquid.info
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def fake_import(name, *args, **kwargs):
            if name == "hyperliquid.info":
                raise ImportError("No module named 'hyperliquid'")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            patch_spot_meta_indexing()

        assert sdk_patches_mod._spot_meta_patched is True

    def test_patches_info_init_when_sdk_available(self):
        """When SDK is available, it should replace Info.__init__."""
        sdk_patches_mod._spot_meta_patched = False

        # Create a fake hyperliquid.info module with a mock Info class
        fake_info_mod = type(sys)("hyperliquid.info")

        class FakeInfo:
            def __init__(self, *args, **kwargs):
                pass

        fake_info_mod.Info = FakeInfo
        original_init = FakeInfo.__init__

        with mock.patch.dict(sys.modules, {"hyperliquid.info": fake_info_mod}):
            patch_spot_meta_indexing()

        # __init__ should have been replaced
        assert fake_info_mod.Info.__init__ is not original_init

    def test_patched_init_calls_original_on_success(self):
        """Patched init should call original init and succeed when no IndexError."""
        sdk_patches_mod._spot_meta_patched = False

        fake_info_mod = type(sys)("hyperliquid.info")
        call_log = []

        class FakeInfo:
            def __init__(self, *args, **kwargs):
                call_log.append("original_init")

        fake_info_mod.Info = FakeInfo

        with mock.patch.dict(sys.modules, {"hyperliquid.info": fake_info_mod}):
            patch_spot_meta_indexing()

        # Create an instance — should call patched init which calls original
        obj = fake_info_mod.Info()
        assert "original_init" in call_log
