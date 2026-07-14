# Peacekeeper KWS model

The voice gateway uses the INT8 subset of
`pkufool/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01`.

Install the four runtime files before enabling `voice.enabled`:

```bash
python3 tools/install_kws_model.py
```

The installer first reuses the matching model in the adjacent Group 5 historical
materials, then falls back to ModelScope when that checkout is unavailable. It
verifies SHA-256 hashes and writes these names into this folder:

```text
tokens.txt
encoder.int8.onnx
decoder.int8.onnx
joiner.int8.onnx
keywords.txt
```

The upstream model is Apache-2.0 licensed. `keywords.txt` contains the local
wake phrase and safety-stop phrase and may be tuned without retraining.
