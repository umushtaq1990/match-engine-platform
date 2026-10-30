"""Golden dataset for the Phase 17 embedding-matching evaluation
(docs/embedding-matching.md). Two parts:

- Ranking ground truth: reuses Phase 4's occupation-only ground truth
  (app/evaluation/ground_truth.py) directly — the same candidate_id -> set
  of relevant job_ids labels, scored here with rule-based/blended/
  embeddings-only modes instead of just rule-based.
- Near-miss pairs: hand-authored (data/golden/embedding_near_miss.yaml),
  testing what Phase 4's ground truth structurally cannot — whether the
  embedding model places semantically related skills close together with
  zero shared skill_id, not just whether it agrees with exact-match rules.
"""
from pathlib import Path

import yaml

NEAR_MISS_FILE = Path(__file__).resolve().parents[2] / "data" / "golden" / "embedding_near_miss.yaml"


def load_near_miss_pairs() -> list[dict]:
    if not NEAR_MISS_FILE.exists():
        return []
    with open(NEAR_MISS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or []
