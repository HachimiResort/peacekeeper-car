# Peacekeeper KWS 模型

语音网关使用 `pkufool/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01` 的 INT8 子集。

启用 `voice.enabled` 前，请安装四个运行时模型文件：

```bash
python3 tools/install_kws_model.py
```

安装程序会优先复用相邻第 5 组历史材料中的同款模型；若该目录不可用，则从 ModelScope 下载。它会校验 SHA-256 哈希，并在此目录写入以下 4 个模型文件：

```text
tokens.txt
encoder.int8.onnx
decoder.int8.onnx
joiner.int8.onnx
```

上游模型采用 Apache-2.0 许可证。仓库随附的 `keywords.txt` 包含本地唤醒短语与安全停车短语，无需重新训练即可调整。安装程序会保留现有 `keywords.txt`。
