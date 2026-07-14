#!/usr/bin/env python3
"""Download and verify the small INT8 sherpa-onnx keyword model."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "models" / "kws"
LOCAL_SOURCE = ROOT.parents[2] / "5" / "第5组撞大运小组-结题提交物" / "代码" / "robot_navigation_and_voice_control" / "sherpa_onnx_ros" / "models" / "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
BASE = "https://www.modelscope.cn/models/pkufool/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/resolve/master"
FILES = {
    "tokens.txt": ("tokens.txt", "cd06ca04c7926f37146b1a2b8a12ac382af0457d2bfacfc5a0949945fe6567b6"),
    "encoder.int8.onnx": ("encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx", "dd784973fc9d2fabb3b800d6dcd20fc3b0ca84f8e2415afe54b032878e447f4d"),
    "decoder.int8.onnx": ("decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx", "ed83454004d5bd16d831eaf00adcd181ed7734886aab6ef440f3ffa5aa3cfe3b"),
    "joiner.int8.onnx": ("joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx", "f79760052b87239e325f0567c752ad3130b30d92effb847d4307743c20c59a24"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for target_name, (source_name, expected) in FILES.items():
        target = ROOT / target_name
        if target.is_file() and sha256(target) == expected:
            print(f"ready: {target_name}")
            continue
        fd, staging_name = tempfile.mkstemp(prefix=".kws-", dir=ROOT)
        os.close(fd)
        staging = Path(staging_name)
        try:
            local = LOCAL_SOURCE / source_name
            if local.is_file():
                print(f"copying from prior project: {source_name}")
                shutil.copyfile(local, staging)
            else:
                print(f"downloading: {source_name}")
                urllib.request.urlretrieve(f"{BASE}/{source_name}", staging)
            actual = sha256(staging)
            if actual != expected:
                raise RuntimeError(f"SHA-256 mismatch for {source_name}: {actual}")
            os.replace(staging, target)
        finally:
            staging.unlink(missing_ok=True)
    print(f"KWS model installed in {ROOT}")


if __name__ == "__main__":
    main()
