"""Pure-logic tests for the near-miss scoring/classification in
app/embeddings/run_embedding_eval.py — the embed() call itself is
monkeypatched so these run without a live Ollama service.
"""
from app.embeddings import run_embedding_eval
from app.embeddings.run_embedding_eval import _avg, _cosine_similarity, score_near_miss_pairs


def test_cosine_similarity_identical_vectors():
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_avg_computes_mean():
    rows = [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}]
    assert _avg(rows, "x") == 2.0


def test_avg_empty_list_returns_zero():
    assert _avg([], "x") == 0.0


def test_score_near_miss_pairs_perfect_classification(monkeypatch):
    # Fixed vectors: pairs sharing a dimension score cosine=1.0 (similar),
    # orthogonal pairs score 0.0 (dissimilar) — independent of embed()'s
    # actual string argument, so the threshold logic is isolated from model
    # behavior entirely.
    fixed_vectors = {
        "Docker": [1.0, 0.0], "Kubernetes": [1.0, 0.0],
        "Welding": [0.0, 1.0],
    }
    monkeypatch.setattr(run_embedding_eval, "embed", lambda model, text: fixed_vectors[text])
    monkeypatch.setattr(run_embedding_eval, "load_near_miss_pairs", lambda: [
        {"pair": ["Docker", "Kubernetes"], "expected": "similar"},
        {"pair": ["Docker", "Welding"], "expected": "dissimilar"},
    ])

    accuracy, detail = score_near_miss_pairs("fake-model")

    assert accuracy == 1.0
    assert len(detail) == 2
    assert all(d["correct"] for d in detail)


def test_score_near_miss_pairs_empty_dataset_returns_zero(monkeypatch):
    monkeypatch.setattr(run_embedding_eval, "load_near_miss_pairs", lambda: [])
    accuracy, detail = score_near_miss_pairs("fake-model")
    assert accuracy == 0.0
    assert detail == []
