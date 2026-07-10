import hashlib
import io
import json
import zipfile

import pytest

from mission_api.map_bundle import build_bundle, parse_bundle, read_pgm


YAML_BYTES = b"image: map.pgm\nresolution: 0.05\norigin: [-1.0, -2.0, 0.0]\n"
PGM_BYTES = b"P5\n2 1\n255\n" + bytes([10, 200])


def test_parse_bundle_and_binary_pgm_first_pixel():
    payload = build_bundle("lab", YAML_BYTES, PGM_BYTES)

    bundle = parse_bundle(payload, 1024 * 1024)

    assert (bundle.width, bundle.height) == (2, 1)
    assert read_pgm(PGM_BYTES)[2] == bytes([10, 200])
    assert bundle.logical_name == "lab"


def test_parse_bundle_rejects_manifest_file_hash_mismatch():
    payload = build_bundle("lab", YAML_BYTES, PGM_BYTES)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as source:
        manifest = json.loads(source.read("manifest.json"))
    manifest["yaml_sha256"] = hashlib.sha256(b"wrong").hexdigest()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("map.yaml", YAML_BYTES)
        archive.writestr("map.pgm", PGM_BYTES)

    with pytest.raises(ValueError, match="map.yaml hash"):
        parse_bundle(output.getvalue(), 1024 * 1024)


def test_parse_bundle_rejects_extra_archive_path():
    payload = build_bundle("lab", YAML_BYTES, PGM_BYTES)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as source:
        files = {name: source.read(name) for name in source.namelist()}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        archive.writestr("../escape", b"bad")

    with pytest.raises(ValueError, match="exactly"):
        parse_bundle(output.getvalue(), 1024 * 1024)


def test_parse_bundle_rejects_large_decompressed_content():
    large_pgm = b"P5\n1024 1024\n255\n" + bytes(1024 * 1024)
    payload = build_bundle("large", YAML_BYTES, large_pgm)

    with pytest.raises(ValueError, match="Decompressed"):
        parse_bundle(payload, 10_000)
