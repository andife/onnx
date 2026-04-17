# Copyright (c) ONNX Project Contributors

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import pytest

# Add the project root to sys.path so we can import backend.py (project root module)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

pytest.importorskip("setuptools", reason="setuptools not available")
import backend


class TestBackendBuild(unittest.TestCase):
    def test_create_version_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend._create_version_file(tmp)
            version_file = os.path.join(tmp, "onnx", "version.py")
            with open(version_file, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("version =", content)
            self.assertIn("git_version =", content)

    def test_preview_build_adds_dev_suffix(self):
        with patch.dict(os.environ, {"ONNX_PREVIEW_BUILD": "1"}):
            version = backend._get_version_info()["version"]
            date_part = version.split(".dev")[-1]
            self.assertEqual(len(date_part), 8)
            self.assertTrue(date_part.isdigit())


if __name__ == "__main__":
    unittest.main()
