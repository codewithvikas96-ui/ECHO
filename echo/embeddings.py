from functools import cached_property
from pathlib import Path
from threading import Lock

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from .model_setup import reranker_ready

DIMENSIONS = 384


def token_inputs(tokens, names):
    values = {
        "input_ids": [token.ids for token in tokens],
        "attention_mask": [token.attention_mask for token in tokens],
        "token_type_ids": [token.type_ids for token in tokens],
    }
    return {name: np.array(values[name], dtype=np.int64) for name in names}


def onnx_session(path: Path):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


class Reranker:
    def __init__(self, directory: Path):
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.tokenizer.enable_truncation(max_length=256)
        self.model = onnx_session(directory / "model.onnx")
        self.inputs = {item.name for item in self.model.get_inputs()}

    def score(self, query, passages):
        scores = []
        for start in range(0, len(passages), 8):
            tokens = self.tokenizer.encode_batch([(query, text) for text in passages[start:start + 8]])
            logits = self.model.run(None, token_inputs(tokens, self.inputs))[0].reshape(-1)
            scores.extend(logits.tolist())
        return scores


class Embedder:
    def __init__(self, directory: Path):
        self.directory = directory
        self._reranker_lock = Lock()
        assets = directory / "model"
        # Independent tokenizers keep chunking offsets unpadded and untruncated.
        tokenizer_path = str(assets / "tokenizer.json")
        self.chunk_tokenizer = Tokenizer.from_file(tokenizer_path)
        self.chunk_tokenizer.no_padding()
        self.chunk_tokenizer.no_truncation()
        self.encoder = Tokenizer.from_file(tokenizer_path)
        self.encoder.enable_padding(pad_id=0, pad_token="[PAD]")
        self.encoder.enable_truncation(max_length=256)
        self.model = onnx_session(assets / "model.onnx")
        self.inputs = {item.name for item in self.model.get_inputs()}

    @cached_property
    def reranker(self) -> Reranker:
        return Reranker(self.directory / "reranker")

    def rerank(self, query, passages):
        if not reranker_ready(self.directory):
            return None
        with self._reranker_lock:
            reranker = self.reranker
        return reranker.score(query, passages)

    def encode(self, texts: list[str]) -> np.ndarray:
        vectors = []
        for start in range(0, len(texts), 24):
            tokens = self.encoder.encode_batch(texts[start:start + 24])
            inputs = token_inputs(tokens, self.inputs | {"attention_mask"})
            mask = inputs["attention_mask"]
            hidden = self.model.run(None, {name: inputs[name] for name in self.inputs})[0]
            pooled = (hidden * mask[..., None].astype(np.float32)).sum(axis=1) / mask.sum(axis=1, keepdims=True)
            normalized = pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            vectors.append(normalized.astype(np.float32))
        return np.ascontiguousarray(np.concatenate(vectors))
