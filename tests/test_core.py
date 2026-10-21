"""Tests for the pure logic - the parts a wrong answer silently breaks."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from openmcl import rules


# ------------------------------------------------------------------ maven paths

@pytest.mark.parametrize("coord,expected", [
    ("net.fabricmc:fabric-loader:0.16.5",
     "net/fabricmc/fabric-loader/0.16.5/fabric-loader-0.16.5.jar"),
    ("org.lwjgl:lwjgl:3.3.3:natives-linux",
     "org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3-natives-linux.jar"),
    ("de.oceanlabs.mcp:mcp_config:1.20.1@zip",
     "de/oceanlabs/mcp/mcp_config/1.20.1/mcp_config-1.20.1.zip"),
    ("net.minecraftforge:forge:1.20.1-47.2.0:universal",
     "net/minecraftforge/forge/1.20.1-47.2.0/forge-1.20.1-47.2.0-universal.jar"),
])
def test_maven_to_path(coord, expected):
    assert rules.maven_to_path(coord) == expected


def test_maven_to_url_joins_once():
    url = rules.maven_to_url("a.b:c:1.0", "https://maven.example.com/releases/")
    assert url == "https://maven.example.com/releases/a/b/c/1.0/c-1.0.jar"


def test_maven_rejects_short_coordinate():
    with pytest.raises(ValueError):
        rules.maven_to_path("group:artifact")


# ----------------------------------------------------------------------- rules

def test_no_rules_means_allowed():
    assert rules.rules_allow(None) is True
    assert rules.rules_allow([]) is True


def test_os_rule_matches_current_platform():
    assert rules.rules_allow([{"action": "allow", "os": {"name": rules.os_name()}}])


def test_os_rule_excludes_other_platform():
    other = "osx" if rules.os_name() != "osx" else "linux"
    assert not rules.rules_allow([{"action": "allow", "os": {"name": other}}])


def test_disallow_overrides_earlier_allow():
    spec = [
        {"action": "allow"},
        {"action": "disallow", "os": {"name": rules.os_name()}},
    ]
    assert rules.rules_allow(spec) is False


def test_feature_rules():
    spec = [{"action": "allow", "rules": None, "features": {"is_demo_user": True}}]
    assert rules.rules_allow(spec, {"is_demo_user": True}) is True
    assert rules.rules_allow(spec, {"is_demo_user": False}) is False
    assert rules.rules_allow(spec, {}) is False


def test_arch_rule_respects_machine():
    assert rules.rules_allow([{"action": "allow", "os": {"arch": rules.os_arch()}}])
    assert not rules.rules_allow([{"action": "allow", "os": {"arch": "nonsuch"}}])


# ------------------------------------------------------------- arg substitution

def test_substitute_leaves_unknown_tokens():
    out = rules.substitute("--uuid ${auth_uuid} --nope ${missing}", {"auth_uuid": "abc"})
    assert out == "--uuid abc --nope ${missing}"


def test_flatten_arguments_handles_strings_and_rule_blocks():
    spec = [
        "--username", "${auth_player_name}",
        {"rules": [{"action": "allow", "os": {"name": rules.os_name()}}],
         "value": ["--here", "yes"]},
        {"rules": [{"action": "allow", "os": {"name": "plan9"}}], "value": "--nope"},
        {"rules": [{"action": "allow", "features": {"has_custom_resolution": True}}],
         "value": ["--width", "${resolution_width}"]},
    ]
    ctx = {"auth_player_name": "Steve", "resolution_width": "1280"}
    out = rules.flatten_arguments(spec, ctx, {"has_custom_resolution": True})
    assert out == ["--username", "Steve", "--here", "yes", "--width", "1280"]


def test_flatten_arguments_drops_disabled_features():
    spec = [{"rules": [{"action": "allow", "features": {"is_demo_user": True}}],
             "value": "--demo"}]
    assert rules.flatten_arguments(spec, {}, {"is_demo_user": False}) == []


# --------------------------------------------------------------------- natives

def test_natives_classifier_expands_arch():
    lib = {"name": "org.lwjgl:lwjgl:2.9.4",
           "natives": {rules.os_name(): "natives-" + rules.os_name() + "-${arch}"}}
    got = rules.library_natives_classifier(lib)
    assert got.endswith(rules.natives_arch_word())
    assert "${arch}" not in got


def test_natives_classifier_none_for_plain_library():
    assert rules.library_natives_classifier({"name": "a:b:1"}) is None


def test_is_native_only_detects_modern_layout():
    assert rules.is_native_only({"name": "org.lwjgl:lwjgl:3.3.3:natives-linux"})
    assert not rules.is_native_only({"name": "org.lwjgl:lwjgl:3.3.3"})


# ------------------------------------------------------------ version inheritance

def test_merge_puts_loader_libraries_first_and_keeps_vanilla(tmp_path, monkeypatch):
    from openmcl import config, mojang
    monkeypatch.setattr(config, "VERSIONS_DIR", tmp_path / "versions")

    parent = {
        "id": "1.21.1",
        "mainClass": "net.minecraft.client.main.Main",
        "libraries": [{"name": "com.mojang:logging:1.0"}],
        "arguments": {"game": ["--username", "${auth_player_name}"], "jvm": ["-cp", "${classpath}"]},
        "assetIndex": {"id": "17", "url": "https://example/17.json"},
        "downloads": {"client": {"url": "https://example/client.jar", "sha1": "x"}},
    }
    child = {
        "id": "fabric-loader-0.16.5-1.21.1",
        "inheritsFrom": "1.21.1",
        "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
        "libraries": [{"name": "net.fabricmc:fabric-loader:0.16.5"}],
        "arguments": {"game": [], "jvm": ["-DFabricMcEmu=net.minecraft.client.main.Main"]},
    }

    merged = mojang._merge(child, parent)

    assert merged["id"] == "fabric-loader-0.16.5-1.21.1"
    assert merged["mainClass"].startswith("net.fabricmc")
    # loader library first, vanilla library retained
    assert merged["libraries"][0]["name"].startswith("net.fabricmc")
    assert any(l["name"].startswith("com.mojang") for l in merged["libraries"])
    # vanilla game args survive, loader jvm args are appended
    assert merged["arguments"]["game"][:2] == ["--username", "${auth_player_name}"]
    assert merged["arguments"]["jvm"][-1].startswith("-DFabricMcEmu")
    # the jar still comes from the parent, and inheritsFrom is consumed
    assert merged["_jarVersion"] == "1.21.1"
    assert "inheritsFrom" not in merged
    assert mojang.jar_version_id(merged) == "1.21.1"


def test_merge_chain_keeps_root_jar_version():
    from openmcl import mojang
    base = {"id": "1.20.1", "libraries": []}
    mid = mojang._merge({"id": "forge-1.20.1", "libraries": []}, base)
    top = mojang._merge({"id": "custom", "libraries": []}, mid)
    assert mojang.jar_version_id(top) == "1.20.1"


# ------------------------------------------------------------------- classpath

def test_classpath_excludes_natives_and_ends_with_client_jar(tmp_path, monkeypatch):
    from openmcl import config, mojang
    monkeypatch.setattr(config, "LIBRARIES_DIR", tmp_path / "libs")
    monkeypatch.setattr(config, "VERSIONS_DIR", tmp_path / "versions")

    version = {
        "id": "1.21.1",
        "libraries": [
            {"name": "com.example:plain:1.0",
             "downloads": {"artifact": {"url": "https://e/x.jar", "path": "com/example/plain/1.0/plain-1.0.jar"}}},
            {"name": "org.lwjgl:lwjgl:3.3.3:natives-linux"},
            {"name": "org.other:excluded:1.0", "rules": [{"action": "disallow"}]},
        ],
    }
    entries = mojang.classpath_entries(version)
    names = [p.name for p in entries]
    assert "plain-1.0.jar" in names
    assert not any("natives" in n for n in names)
    assert not any("excluded" in n for n in names)
    assert names[-1] == "1.21.1.jar"


# -------------------------------------------------------------------- instances

def test_instance_roundtrip_and_mod_toggle(tmp_path, monkeypatch):
    from openmcl import config
    from openmcl.instances import InstanceStore, ModRecord
    monkeypatch.setattr(config, "INSTANCES_DIR", tmp_path / "instances")

    store = InstanceStore(tmp_path / "instances")
    inst = store.create("My Pack", "1.21.1", "fabric", "0.16.5")
    assert inst.id == "my-pack"
    assert inst.loader_label == "Fabric 0.16.5"

    inst.mods_dir.mkdir(parents=True, exist_ok=True)
    (inst.mods_dir / "sodium.jar").write_bytes(b"jar")
    inst.add_mod(ModRecord(filename="sodium.jar", source="modrinth", name="Sodium"))

    again = store.get("my-pack")
    assert again is not None
    assert again.mod_records()[0].name == "Sodium"

    again.set_mod_enabled("sodium.jar", False)
    assert (again.mods_dir / "sodium.jar.disabled").exists()
    assert not (again.mods_dir / "sodium.jar").exists()
    assert again.scan_mods_dir() == ["sodium.jar.disabled"]

    again.set_mod_enabled("sodium.jar", True)
    assert (again.mods_dir / "sodium.jar").exists()

    again.remove_mod("sodium.jar")
    assert store.get("my-pack").mod_records() == []


def test_unique_id_avoids_collisions(tmp_path):
    from openmcl.instances import InstanceStore
    store = InstanceStore(tmp_path)
    store.create("Pack", "1.21.1")
    assert store.unique_id("Pack") == "pack-2"
    store.create("Pack", "1.21.1")
    assert store.unique_id("Pack") == "pack-3"


# ----------------------------------------------------------------- launch plan

def _fake_version() -> dict:
    return {
        "id": "1.21.1",
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "javaVersion": {"majorVersion": 21},
        "assetIndex": {"id": "17", "url": "https://example/17.json", "sha1": "a"},
        "libraries": [],
        "arguments": {
            "jvm": ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"],
            "game": [
                "--username", "${auth_player_name}",
                "--uuid", "${auth_uuid}",
                "--accessToken", "${auth_access_token}",
                "--userType", "${user_type}",
                {"rules": [{"action": "allow", "features": {"has_custom_resolution": True}}],
                 "value": ["--width", "${resolution_width}", "--height", "${resolution_height}"]},
            ],
        },
    }


def test_build_plan_substitutes_everything(tmp_path, monkeypatch):
    from openmcl import config, java, launch, mojang
    from openmcl.auth import Account
    from openmcl.config import Settings
    from openmcl.instances import InstanceStore

    monkeypatch.setattr(config, "INSTANCES_DIR", tmp_path / "instances")
    monkeypatch.setattr(config, "NATIVES_DIR", tmp_path / "natives")
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(config, "LIBRARIES_DIR", tmp_path / "libs")
    monkeypatch.setattr(config, "VERSIONS_DIR", tmp_path / "versions")
    monkeypatch.setattr(mojang, "extract_natives", lambda v, d: d.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(mojang, "ensure_asset_index", lambda v: {"objects": {}})
    monkeypatch.setattr(
        java, "select",
        lambda version, override="": java.JavaRuntime(Path("/usr/bin/java"), 21, "21.0.1"),
    )

    store = InstanceStore(tmp_path / "instances")
    inst = store.create("Test", "1.21.1")
    inst.memory_mb = 2048
    inst.save()

    account = Account(uuid="11111111-2222-3333-4444-555555555555", name="Steve",
                      access_token="tok", xuid="xu")
    settings = Settings(jvm_args="", window_width=1280, window_height=720)

    plan = launch.build_plan(inst, account, settings, _fake_version())
    cmd = plan.command

    assert cmd[0] == "/usr/bin/java"
    assert "-Xmx2048M" in cmd
    assert "net.minecraft.client.main.Main" in cmd
    assert cmd[cmd.index("--username") + 1] == "Steve"
    assert cmd[cmd.index("--uuid") + 1] == "111111112222333344445555555555 55".replace(" ", "")
    assert cmd[cmd.index("--accessToken") + 1] == "tok"
    assert cmd[cmd.index("--userType") + 1] == "msa"
    assert cmd[cmd.index("--width") + 1] == "1280"
    # no unexpanded placeholders anywhere
    assert not [c for c in cmd if "${" in c]
    assert plan.cwd == inst.game_dir


def test_legacy_version_uses_minecraft_arguments(tmp_path, monkeypatch):
    from openmcl import config, java, launch, mojang
    from openmcl.auth import Account
    from openmcl.config import Settings
    from openmcl.instances import InstanceStore

    monkeypatch.setattr(config, "INSTANCES_DIR", tmp_path / "instances")
    monkeypatch.setattr(config, "NATIVES_DIR", tmp_path / "natives")
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(config, "VERSIONS_DIR", tmp_path / "versions")
    monkeypatch.setattr(mojang, "extract_natives", lambda v, d: d.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(mojang, "ensure_asset_index", lambda v: {"objects": {}})
    monkeypatch.setattr(
        java, "select",
        lambda version, override="": java.JavaRuntime(Path("/usr/bin/java"), 8, "1.8.0"),
    )

    version = {
        "id": "1.8.9", "type": "release", "assets": "1.8",
        "mainClass": "net.minecraft.client.main.Main",
        "libraries": [],
        "minecraftArguments": "--username ${auth_player_name} --assetsDir ${assets_root}",
    }
    store = InstanceStore(tmp_path / "instances")
    inst = store.create("Old", "1.8.9")
    account = Account(uuid="a" * 32, name="Alex")

    plan = launch.build_plan(inst, account, Settings(), version)
    assert "--username" in plan.command
    assert plan.command[plan.command.index("--username") + 1] == "Alex"
    assert "-cp" in plan.command
    assert not [c for c in plan.command if "${" in c]


# ------------------------------------------------------------------ pack safety

def test_safe_extract_refuses_path_traversal(tmp_path):
    from openmcl.packs import _safe_extract

    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("overrides/config/ok.txt", "fine")
        zf.writestr("overrides/../../escaped.txt", "bad")

    dest = tmp_path / "game"
    dest.mkdir()
    with zipfile.ZipFile(archive) as zf:
        written = _safe_extract(zf, "overrides/", dest)

    assert (dest / "config" / "ok.txt").read_text() == "fine"
    assert not (tmp_path.parent / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()
    assert written == 1


def test_detect_pack_type(tmp_path):
    from openmcl.packs import PackError, detect_pack_type

    mr = tmp_path / "a.mrpack"
    with zipfile.ZipFile(mr, "w") as zf:
        zf.writestr("modrinth.index.json", "{}")
    assert detect_pack_type(mr) == "mrpack"

    cf = tmp_path / "b.zip"
    with zipfile.ZipFile(cf, "w") as zf:
        zf.writestr("manifest.json", "{}")
    assert detect_pack_type(cf) == "curseforge"

    other = tmp_path / "c.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(PackError):
        detect_pack_type(other)


# ------------------------------------------------------------------------ java

@pytest.mark.parametrize("raw,major", [
    ("1.8.0_382", 8), ("17.0.9", 17), ("21", 21), ("11.0.21+9", 11), ("1.7.0_80", 7),
])
def test_java_major_parsing(raw, major):
    from openmcl.java import _parse_major
    assert _parse_major(raw) == major


def test_required_major_falls_back_to_8():
    from openmcl.java import required_major
    assert required_major({"javaVersion": {"majorVersion": 21}}) == 21
    assert required_major({}) == 8


# ----------------------------------------------------------------- loader lists

@pytest.mark.parametrize("mc,prefix", [
    ("1.21.1", "21.1."), ("1.21", "21.0."), ("1.20.6", "20.6."), ("24w03a", ""),
])
def test_neoforge_prefix(mc, prefix):
    from openmcl.loaders import _neoforge_prefix
    assert _neoforge_prefix(mc) == prefix


def test_processor_arg_expansion(tmp_path, monkeypatch):
    from openmcl import config, loaders
    monkeypatch.setattr(config, "LIBRARIES_DIR", tmp_path / "libs")
    data = {"SIDE": "client", "MINECRAFT_JAR": "/jars/1.20.1.jar"}
    assert loaders._expand_arg("--side={SIDE}", data) == "--side=client"
    assert loaders._expand_arg("{MINECRAFT_JAR}", data) == "/jars/1.20.1.jar"
    expanded = loaders._expand_arg("[net.example:tool:1.0]", data)
    assert expanded.endswith("net/example/tool/1.0/tool-1.0.jar")
    assert loaders._expand_arg("{UNKNOWN}", data) == "{UNKNOWN}"


# ----------------------------------------------------------------- theme maths

def test_shade_and_contrast():
    from openmcl.ui.theme import readable_on, shade
    assert shade("#000000", 1.0) == "#ffffff"
    assert shade("#ffffff", -1.0) == "#000000"
    assert shade("#808080", 0.0) == "#808080"
    assert readable_on("#ffffff") == "#111318"
    assert readable_on("#101014") == "#ffffff"
    assert shade("not-a-colour", 0.5) == "not-a-colour"


def test_stylesheet_renders_for_every_theme():
    from openmcl.ui.theme import PALETTES, stylesheet
    for name in PALETTES:
        css = stylesheet(name, "#4f9d69", 13)
        assert "QPushButton#Primary" in css
        assert "{" in css and "${" not in css


# -------------------------------------------------------------- provider shapes

def test_modrinth_facets_include_loader_and_version():
    from openmcl.providers.modrinth import _facets
    facets = json.loads(_facets("mod", "1.21.1", "fabric"))
    assert ["project_type:mod"] in facets
    assert ["versions:1.21.1"] in facets
    assert ["categories:fabric"] in facets


def test_modrinth_facets_skip_loader_for_resource_packs():
    from openmcl.providers.modrinth import _facets
    facets = json.loads(_facets("resourcepack", "1.21.1", "fabric"))
    assert ["categories:fabric"] not in facets


def test_modrinth_version_mapping_picks_primary_file():
    from openmcl.providers.modrinth import _to_file
    payload = {
        "id": "abc", "project_id": "AABB", "name": "Sodium 0.5",
        "version_number": "0.5.8", "version_type": "release",
        "date_published": "2024-06-01T10:00:00Z",
        "game_versions": ["1.21.1"], "loaders": ["fabric"],
        "dependencies": [{"project_id": "DEP", "dependency_type": "required"},
                         {"project_id": None, "dependency_type": "optional"}],
        "files": [
            {"filename": "sources.jar", "url": "u1", "primary": False, "size": 1,
             "hashes": {"sha1": "h1"}},
            {"filename": "sodium.jar", "url": "u2", "primary": True, "size": 2,
             "hashes": {"sha1": "h2"}},
        ],
    }
    f = _to_file(payload)
    assert f.filename == "sodium.jar" and f.sha1 == "h2"
    assert f.dependencies == [("DEP", "required")]
    assert f.label == "0.5.8"


def test_curseforge_file_mapping_and_distribution_flag():
    from openmcl.providers.curseforge import _to_file
    payload = {
        "id": 99, "displayName": "JEI 15.2", "fileName": "jei.jar",
        "downloadUrl": None, "fileLength": 123, "releaseType": 2,
        "fileDate": "2024-05-02T00:00:00Z", "gameVersions": ["1.20.1", "Forge"],
        "hashes": [{"algo": 2, "value": "md5"}, {"algo": 1, "value": "sha1v"}],
        "dependencies": [{"modId": 7, "relationType": 3}, {"modId": 8, "relationType": 2}],
    }
    f = _to_file(payload, "42", distributable=True)
    assert f.sha1 == "sha1v"
    assert f.release_type == "beta"
    assert f.distributable is False  # no downloadUrl
    assert ("7", "required") in f.dependencies
    assert ("8", "optional") in f.dependencies
    assert "forge" in f.loaders


def test_download_task_detects_wrong_checksum(tmp_path):
    from openmcl.net import DownloadTask, sha1_file
    target = tmp_path / "f.bin"
    target.write_bytes(b"hello")
    good = sha1_file(target)
    assert DownloadTask("u", target, sha1=good).is_satisfied()
    assert not DownloadTask("u", target, sha1="0" * 40).is_satisfied()
    assert not DownloadTask("u", target, sha1=good, size=999).is_satisfied()
    assert not DownloadTask("u", tmp_path / "missing.bin").is_satisfied()
