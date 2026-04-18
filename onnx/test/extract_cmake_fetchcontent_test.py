# Copyright (c) ONNX Project Contributors
#
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Import tools/extract_cmake_fetchcontent.py directly (it is not a package).
# ---------------------------------------------------------------------------
_TOOLS_DIR = Path(__file__).parents[2] / "tools"
_SCRIPT = _TOOLS_DIR / "extract_cmake_fetchcontent.py"

spec = importlib.util.spec_from_file_location("extract_cmake_fetchcontent", _SCRIPT)
assert spec is not None and spec.loader is not None
_mod = importlib.util.module_from_spec(spec)
sys.modules["extract_cmake_fetchcontent"] = _mod
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

_parse_cmake_variables = _mod._parse_cmake_variables
_resolve = _mod._resolve
_find_version_variable = _mod._find_version_variable
_parse_fetchcontent_declares = _mod._parse_fetchcontent_declares
_build_component = _mod._build_component
_make_bom = _mod._make_bom
_merge_into = _mod._merge_into

# ---------------------------------------------------------------------------
# Minimal CMake snippets that mirror the real CMakeLists.txt patterns.
# ---------------------------------------------------------------------------

_URL_CMAKE = """\
set(AbseilURL https://github.com/abseil/abseil-cpp/archive/refs/tags/20240722.0.tar.gz)
set(AbseilSHA256 f50e5ac311a81382da7fa75b97310e4b9006474f9560ac46f54a9967f07d4ae3)
FetchContent_Declare(
  absl
  URL ${AbseilURL}
  URL_HASH SHA256=${AbseilSHA256}
)
"""

_GIT_CMAKE = """\
FetchContent_Declare(
  nanobind
  GIT_REPOSITORY https://github.com/wjakob/nanobind.git
  GIT_TAG v2.10.2
)
"""

_MULTI_CMAKE = _URL_CMAKE + "\n" + _GIT_CMAKE


class TestParseCmakeVariables(unittest.TestCase):
    def test_unquoted_value(self) -> None:
        text = "set(FOO bar)"
        assert _parse_cmake_variables(text) == {"FOO": "bar"}

    def test_quoted_value(self) -> None:
        text = 'set(FOO "bar baz")'
        assert _parse_cmake_variables(text) == {"FOO": "bar baz"}

    def test_url_variable(self) -> None:
        variables = _parse_cmake_variables(_URL_CMAKE)
        assert "AbseilURL" in variables
        assert variables["AbseilURL"].startswith("https://github.com/abseil/")

    def test_multiple_variables(self) -> None:
        variables = _parse_cmake_variables(_URL_CMAKE)
        assert "AbseilSHA256" in variables
        assert len(variables["AbseilSHA256"]) == 64  # SHA-256 hex length

    def test_case_insensitive_keyword(self) -> None:
        text = "SET(FOO value)"
        assert _parse_cmake_variables(text) == {"FOO": "value"}


class TestResolve(unittest.TestCase):
    def test_no_variables(self) -> None:
        assert _resolve("plain-string", {}) == "plain-string"

    def test_known_variable(self) -> None:
        assert _resolve("${FOO}", {"FOO": "bar"}) == "bar"

    def test_unknown_variable_preserved(self) -> None:
        assert _resolve("${MISSING}", {}) == "${MISSING}"

    def test_mixed(self) -> None:
        result = _resolve("https://example.com/${VERSION}/file.tar.gz", {"VERSION": "1.0"})
        assert result == "https://example.com/1.0/file.tar.gz"


class TestFindVersionVariable(unittest.TestCase):
    def test_found(self) -> None:
        text = "set(absl_version 20240722.0)"
        assert _find_version_variable(text, "absl") == "20240722.0"

    def test_case_insensitive(self) -> None:
        text = "set(ABSL_VERSION 20240722.0)"
        assert _find_version_variable(text, "absl") == "20240722.0"

    def test_not_found(self) -> None:
        assert _find_version_variable("set(OTHER_VERSION 1.0)", "absl") is None


class TestParseFetchContentDeclares(unittest.TestCase):
    def _parse(self, text: str) -> list[dict[str, str]]:
        variables = _parse_cmake_variables(text)
        return _parse_fetchcontent_declares(text, variables)

    def test_url_based_name(self) -> None:
        entries = self._parse(_URL_CMAKE)
        assert len(entries) == 1
        assert entries[0]["name"] == "absl"

    def test_url_resolved(self) -> None:
        entries = self._parse(_URL_CMAKE)
        assert "abseil-cpp" in entries[0]["url"]

    def test_url_hash_parsed(self) -> None:
        entries = self._parse(_URL_CMAKE)
        assert entries[0]["hash_alg"] == "SHA256"
        assert len(entries[0]["hash_val"]) == 64

    def test_git_based(self) -> None:
        entries = self._parse(_GIT_CMAKE)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["name"] == "nanobind"
        assert "nanobind.git" in entry["git_url"]
        assert entry["git_tag"] == "v2.10.2"

    def test_multiple_entries(self) -> None:
        entries = self._parse(_MULTI_CMAKE)
        names = [e["name"] for e in entries]
        assert "absl" in names
        assert "nanobind" in names

    def test_no_fetchcontent(self) -> None:
        assert self._parse("set(FOO bar)") == []


class TestBuildComponent(unittest.TestCase):
    def _component_from(self, cmake: str, name: str) -> dict:
        variables = _parse_cmake_variables(cmake)
        entries = _parse_fetchcontent_declares(cmake, variables)
        entry = next(e for e in entries if e["name"].lower() == name)
        return _build_component(entry, cmake)

    def test_url_component_name_canonical(self) -> None:
        comp = self._component_from(_URL_CMAKE, "absl")
        assert comp["name"] == "abseil-cpp"

    def test_url_component_type(self) -> None:
        comp = self._component_from(_URL_CMAKE, "absl")
        assert comp["type"] == "library"

    def test_url_component_version_from_url(self) -> None:
        comp = self._component_from(_URL_CMAKE, "absl")
        assert "version" in comp
        assert "20240722" in comp["version"]

    def test_url_component_purl_github(self) -> None:
        comp = self._component_from(_URL_CMAKE, "absl")
        assert comp["purl"].startswith("pkg:github/abseil/")

    def test_url_component_purl_tag_consistent_with_version(self) -> None:
        # When an explicit version variable exists, purl tag must match it.
        cmake = """\
set(MyDep_VERSION 6.33.6)
set(MyURL https://github.com/example/mydep/releases/download/v33.6/mydep-33.6.tar.gz)
FetchContent_Declare(
  MyDep
  URL ${MyURL}
  URL_HASH SHA256=abc123
)
"""
        variables = _parse_cmake_variables(cmake)
        entries = _parse_fetchcontent_declares(cmake, variables)
        comp = _build_component(entries[0], cmake)
        assert comp["version"] == "6.33.6"
        assert comp["purl"].endswith("@v6.33.6"), f"purl tag must match version, got: {comp['purl']}"

    def test_url_component_hash(self) -> None:
        comp = self._component_from(_URL_CMAKE, "absl")
        assert comp["hashes"][0]["alg"] == "SHA-256"

    def test_url_component_license_absl(self) -> None:
        comp = self._component_from(_URL_CMAKE, "absl")
        assert comp["licenses"][0]["license"]["id"] == "Apache-2.0"

    def test_git_component_version_strips_v(self) -> None:
        comp = self._component_from(_GIT_CMAKE, "nanobind")
        assert comp["version"] == "2.10.2"

    def test_git_component_purl_uses_tag(self) -> None:
        comp = self._component_from(_GIT_CMAKE, "nanobind")
        assert "v2.10.2" in comp["purl"]

    def test_git_component_external_ref_vcs(self) -> None:
        comp = self._component_from(_GIT_CMAKE, "nanobind")
        vcs_refs = [r for r in comp["externalReferences"] if r["type"] == "vcs"]
        assert len(vcs_refs) == 1
        assert "nanobind" in vcs_refs[0]["url"]

    def test_git_component_external_ref_distribution(self) -> None:
        comp = self._component_from(_GIT_CMAKE, "nanobind")
        dist_refs = [r for r in comp["externalReferences"] if r["type"] == "distribution"]
        assert len(dist_refs) == 1
        assert "v2.10.2" in dist_refs[0]["url"]
        assert dist_refs[0]["url"].endswith(".tar.gz")

    def test_git_component_license_nanobind(self) -> None:
        comp = self._component_from(_GIT_CMAKE, "nanobind")
        assert comp["licenses"][0]["license"]["id"] == "BSD-3-Clause"

    def test_bom_ref_uses_canonical_name(self) -> None:
        comp = self._component_from(_URL_CMAKE, "absl")
        assert comp["bom-ref"] == "abseil-cpp@20240722.0"

    def test_bom_ref_with_version(self) -> None:
        comp = self._component_from(_GIT_CMAKE, "nanobind")
        assert comp["bom-ref"] == "nanobind@2.10.2"

    def test_unknown_dep_no_license(self) -> None:
        cmake = """\
FetchContent_Declare(
  unknown_dep
  GIT_REPOSITORY https://github.com/example/unknown.git
  GIT_TAG v1.0.0
)
"""
        comp = self._component_from(cmake, "unknown_dep")
        assert "licenses" not in comp

    def test_sha256_alg_normalized(self) -> None:
        comp = self._component_from(_URL_CMAKE, "absl")
        assert comp["hashes"][0]["alg"] == "SHA-256"

    def test_sha1_alg_normalized(self) -> None:
        cmake = """\
set(MyURL https://github.com/example/dep/archive/v1.0.tar.gz)
set(MySHA abc123)
FetchContent_Declare(
  mydep
  URL ${MyURL}
  URL_HASH SHA1=${MySHA}
)
"""
        comp = self._component_from(cmake, "mydep")
        assert comp["hashes"][0]["alg"] == "SHA-1"


class TestMakeBom(unittest.TestCase):
    def setUp(self) -> None:
        variables = _parse_cmake_variables(_GIT_CMAKE)
        entries = _parse_fetchcontent_declares(_GIT_CMAKE, variables)
        self.components = [_build_component(e, _GIT_CMAKE) for e in entries]
        self.bom = _make_bom(self.components, "build")

    def test_format(self) -> None:
        assert self.bom["bomFormat"] == "CycloneDX"

    def test_spec_version(self) -> None:
        assert self.bom["specVersion"] == "1.6"

    def test_serial_number_is_urn_uuid(self) -> None:
        assert self.bom["serialNumber"].startswith("urn:uuid:")

    def test_lifecycle(self) -> None:
        assert self.bom["metadata"]["lifecycles"] == [{"phase": "build"}]

    def test_components_present(self) -> None:
        assert len(self.bom["components"]) == 1
        assert self.bom["components"][0]["name"] == "nanobind"

    def test_manufacturer(self) -> None:
        assert self.bom["metadata"]["manufacturer"]["name"] == "ONNX Project Contributors"


class TestMergeInto(unittest.TestCase):
    def _make_base_bom(self) -> dict:
        return {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "serialNumber": "urn:uuid:00000000-0000-0000-0000-000000000001",
            "version": 1,
            "metadata": {},
            "components": [{"type": "library", "name": "numpy", "bom-ref": "numpy@2.0.0"}],
            "dependencies": [{"ref": "numpy@2.0.0"}],
        }

    def test_components_appended(self) -> None:
        variables = _parse_cmake_variables(_GIT_CMAKE)
        entries = _parse_fetchcontent_declares(_GIT_CMAKE, variables)
        new_comps = [_build_component(e, _GIT_CMAKE) for e in entries]

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(self._make_base_bom(), f)
            tmp = Path(f.name)

        result = _merge_into(tmp, new_comps, "build")
        tmp.unlink()

        names = [c["name"] for c in result["components"]]
        assert "numpy" in names
        assert "nanobind" in names

    def test_lifecycle_overwritten(self) -> None:
        variables = _parse_cmake_variables(_GIT_CMAKE)
        entries = _parse_fetchcontent_declares(_GIT_CMAKE, variables)
        new_comps = [_build_component(e, _GIT_CMAKE) for e in entries]

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(self._make_base_bom(), f)
            tmp = Path(f.name)

        result = _merge_into(tmp, new_comps, "build")
        tmp.unlink()

        assert result["metadata"]["lifecycles"] == [{"phase": "build"}]

    def test_dependencies_extended(self) -> None:
        variables = _parse_cmake_variables(_GIT_CMAKE)
        entries = _parse_fetchcontent_declares(_GIT_CMAKE, variables)
        new_comps = [_build_component(e, _GIT_CMAKE) for e in entries]

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(self._make_base_bom(), f)
            tmp = Path(f.name)

        result = _merge_into(tmp, new_comps, "build")
        tmp.unlink()

        refs = {d["ref"] for d in result["dependencies"]}
        assert "nanobind@2.10.2" in refs

    def test_no_duplicate_dependencies(self) -> None:
        variables = _parse_cmake_variables(_GIT_CMAKE)
        entries = _parse_fetchcontent_declares(_GIT_CMAKE, variables)
        new_comps = [_build_component(e, _GIT_CMAKE) for e in entries]

        base = self._make_base_bom()
        base["dependencies"].append({"ref": "nanobind@2.10.2"})

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(base, f)
            tmp = Path(f.name)

        result = _merge_into(tmp, new_comps, "build")
        tmp.unlink()

        refs = [d["ref"] for d in result["dependencies"]]
        assert refs.count("nanobind@2.10.2") == 1


class TestAgainstRealCMakeLists(unittest.TestCase):
    """Smoke test: parse the actual CMakeLists.txt and verify expected deps."""

    _CMAKE_PATH = Path(__file__).parents[2] / "CMakeLists.txt"

    @classmethod
    def setUpClass(cls) -> None:
        if not cls._CMAKE_PATH.exists():
            raise unittest.SkipTest("CMakeLists.txt not found")
        text = cls._CMAKE_PATH.read_text(encoding="utf-8")
        variables = _parse_cmake_variables(text)
        entries = _parse_fetchcontent_declares(text, variables)
        cls.components = [_build_component(e, text) for e in entries]
        cls.by_name = {c["name"]: c for c in cls.components}

    def test_abseil_cpp_present(self) -> None:
        assert "abseil-cpp" in self.by_name

    def test_protobuf_present(self) -> None:
        assert "protobuf" in self.by_name

    def test_nanobind_present(self) -> None:
        assert "nanobind" in self.by_name

    def test_abseil_cpp_license(self) -> None:
        assert self.by_name["abseil-cpp"]["licenses"][0]["license"]["id"] == "Apache-2.0"

    def test_protobuf_license(self) -> None:
        assert self.by_name["protobuf"]["licenses"][0]["license"]["id"] == "BSD-3-Clause"

    def test_nanobind_license(self) -> None:
        assert self.by_name["nanobind"]["licenses"][0]["license"]["id"] == "BSD-3-Clause"

    def test_all_have_versions(self) -> None:
        for name, comp in self.by_name.items():
            assert "version" in comp, f"{name} has no version"

    def test_all_have_purl(self) -> None:
        for name, comp in self.by_name.items():
            assert "purl" in comp, f"{name} has no purl"

    def test_purl_tag_matches_version(self) -> None:
        for name, comp in self.by_name.items():
            version = comp.get("version", "")
            purl = comp.get("purl", "")
            # purl tag is the portion after '@'
            purl_tag = purl.split("@")[-1] if "@" in purl else ""
            assert purl_tag.lstrip("v") == version, (
                f"{name}: purl tag '{purl_tag}' does not match version '{version}'"
            )

    def test_abseil_cpp_purl_references_abseil(self) -> None:
        assert "abseil" in self.by_name["abseil-cpp"]["purl"]

    def test_protobuf_purl_references_protocolbuffers(self) -> None:
        assert "protocolbuffers" in self.by_name["protobuf"]["purl"]

    def test_nanobind_has_both_vcs_and_distribution_refs(self) -> None:
        refs = self.by_name["nanobind"]["externalReferences"]
        types = {r["type"] for r in refs}
        assert "vcs" in types
        assert "distribution" in types

    def test_protobuf_version_matches_version_variable(self) -> None:
        # CMakeLists.txt sets Protobuf_VERSION "6.33.6" after the FetchContent_Declare.
        # The script must prefer that over the URL-embedded tag (v33.6).
        assert self.by_name["protobuf"]["version"] == "6.33.6"
        assert "@v6.33.6" in self.by_name["protobuf"]["purl"]


if __name__ == "__main__":
    unittest.main()
