from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

DIMENSIONS = 384


class Embedder:
    def __init__(self, directory: Path):
        assets = directory / "model"
        # Independent tokenizers keep chunking offsets unpadded and untruncated.
        tokenizer_path = str(assets / "tokenizer.json")
        self.chunk_tokenizer = Tokenizer.from_file(tokenizer_path)
        self.chunk_tokenizer.no_padding()
        self.chunk_tokenizer.no_truncation()
        self.encoder = Tokenizer.from_file(tokenizer_path)
        self.encoder.enable_padding(pad_id=0, pad_token="[PAD]")
        self.encoder.enable_truncation(max_length=256)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.model = ort.InferenceSession(str(assets / "model.onnx"), options, providers=["CPUExecutionProvider"])
        self.inputs = {item.name for item in self.model.get_inputs()}

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = []
        for start in range(0, len(texts), 24):
            tokens = self.encoder.encode_batch(texts[start:start + 24])
            mask = np.array([token.attention_mask for token in tokens], dtype=np.int64)
            inputs = {
                "input_ids": np.array([token.ids for token in tokens], dtype=np.int64),
                "attention_mask": mask,
                "token_type_ids": np.array([token.type_ids for token in tokens], dtype=np.int64),
            }
            hidden = self.model.run(None, {name: inputs[name] for name in self.inputs})[0]
            pooled = (hidden * mask[..., None].astype(np.float32)).sum(axis=1) / mask.sum(axis=1, keepdims=True)
            normalized = pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            vectors.append(normalized.astype(np.float32))
        return np.ascontiguousarray(np.concatenate(vectors))
