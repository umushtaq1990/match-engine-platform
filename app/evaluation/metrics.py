"""Standard IR ranking metrics. Pure functions, no DB — operate on a ranked
list of IDs and a set of "relevant" IDs. Binary relevance only (an item is
relevant or it isn't; no graded relevance in this dataset).
"""
import math


def precision_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    top_k = ranked_ids[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for i in top_k if i in relevant_ids)
    return hits / len(top_k)


def recall_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    if not relevant_ids:
        return 0.0
    top_k = ranked_ids[:k]
    hits = sum(1 for i in top_k if i in relevant_ids)
    return hits / len(relevant_ids)


def dcg_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    score = 0.0
    for idx, item_id in enumerate(ranked_ids[:k], start=1):
        if item_id in relevant_ids:
            score += 1.0 / math.log2(idx + 1)
    return score


def ndcg_at_k(ranked_ids: list[int], relevant_ids: set[int], k: int) -> float:
    dcg = dcg_at_k(ranked_ids, relevant_ids, k)
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(idx + 1) for idx in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def reciprocal_rank(ranked_ids: list[int], relevant_ids: set[int]) -> float:
    for idx, item_id in enumerate(ranked_ids, start=1):
        if item_id in relevant_ids:
            return 1.0 / idx
    return 0.0
