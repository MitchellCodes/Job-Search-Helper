"""Meaning matching: a small bundled sentence-embedding model (MiniLM, quantized ONNX).

Runs fully offline on CPU. `Embedder.embed` returns L2-normalised vectors, so the dot
product of two vectors is their cosine similarity.
"""
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
MAX_TOKENS = 256


class Embedder:
    def __init__(self, model_dir: Path = MODEL_DIR):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2  # modest laptops: keep CPU use polite
        self.session = ort.InferenceSession(
            str(model_dir / "model_quantized.onnx"), opts, providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=MAX_TOKENS)
        self.tokenizer.enable_padding()
        self.dim = 384

    def embed(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        out = []
        for start in range(0, len(texts), batch_size):
            enc = self.tokenizer.encode_batch(texts[start:start + batch_size])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self.session.run(None, feeds)[0]            # (batch, tokens, 384)
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
            out.append(pooled.astype(np.float32))
        return np.vstack(out)


def similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return a @ b.T


class CachedEmbedder:
    """Wraps an Embedder so each distinct text is embedded once and stored in SQLite."""

    def __init__(self, embedder: Embedder, conn):
        self.embedder = embedder
        self.conn = conn
        self.dim = embedder.dim

    @staticmethod
    def _key(text: str) -> str:
        import hashlib

        return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()

    def embed(self, texts: list[str]) -> np.ndarray:
        keys = [self._key(t) for t in texts]
        found: dict[str, np.ndarray] = {}
        for k in set(keys):
            row = self.conn.execute("SELECT vec FROM embeddings WHERE text_hash = ?", (k,)).fetchone()
            if row:
                found[k] = np.frombuffer(row["vec"], dtype=np.float32)
        missing = [(k, t) for k, t in dict(zip(keys, texts)).items() if k not in found]
        if missing:
            vecs = self.embedder.embed([t for _, t in missing])
            for (k, _), v in zip(missing, vecs):
                found[k] = v
                self.conn.execute("INSERT OR REPLACE INTO embeddings (text_hash, vec) VALUES (?,?)",
                                  (k, v.astype(np.float32).tobytes()))
            self.conn.commit()
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.vstack([found[k] for k in keys])
