import time

import pytest

from jobhelper.matching.embeddings import Embedder, MODEL_DIR, similarity_matrix

pytestmark = pytest.mark.skipif(
    not (MODEL_DIR / "model_quantized.onnx").exists(), reason="bundled model not present"
)


@pytest.fixture(scope="module")
def embedder():
    return Embedder()


def test_vectors_are_normalised(embedder):
    v = embedder.embed(["Built REST services in Python"])
    assert v.shape == (1, 384)
    assert abs(float((v[0] ** 2).sum()) - 1.0) < 1e-3


def test_paraphrase_beats_unrelated(embedder):
    req = ["Experience developing backend APIs"]
    bullets = [
        "Built REST services that handled 2M requests per day",   # paraphrase, no shared keywords
        "Organised weekly team lunches and birthday celebrations",  # unrelated
    ]
    sims = similarity_matrix(embedder.embed(req), embedder.embed(bullets))[0]
    assert sims[0] > sims[1] + 0.15


def test_batch_matches_single(embedder):
    texts = ["SQL reporting dashboards", "Managed a team of five engineers", "Tableau"]
    batch = embedder.embed(texts)
    single = embedder.embed([texts[1]])
    # The int8-quantized model gives slightly different numbers depending on padding, so
    # batch and single results are near-identical (~0.99) rather than exactly equal.
    assert float(batch[1] @ single[0]) > 0.98


def test_speed_is_reasonable_on_cpu(embedder):
    texts = ["Built dashboards in Tableau that cut reporting time by 40 percent"] * 60
    start = time.perf_counter()
    embedder.embed(texts)
    assert time.perf_counter() - start < 10
