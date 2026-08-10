"""
memory_layers.py — FinMem-inspired layered memory management.

Three layers with content-type enforcement:
  shallow      (Q=14 days)   — daily regime observations, short-term signals
  intermediate (Q=90 days)   — parameter insights, mechanism findings
  deep         (Q=365 days)  — failure diagnoses, strategic learnings, rationales

Importance scoring (from FinMem):
  Base values {40, 60, 80} assigned stochastically per layer probability.
  Importance decays: I_t = I_0 * (alpha_l ^ delta_days)
  Layer alpha: shallow=0.90/day, intermediate=0.967/day, deep=0.988/day

Compound score (for ranking within layer):
  S_compound = S_recency + I_current / 100
  where I_current = compute_importance_score(initial, created_at, layer)

Jump thresholds (from FinMem):
  shallow → intermediate: I >= 60 and recency > 0.7
  intermediate → deep:    I >= 80 and recency > 0.7
  deep → intermediate:    I < 80
  intermediate → shallow: I < 60
  (shallow cannot be demoted — it has no lower layer)
"""
import math
import random
import time
from typing import Optional

LAYER_CONFIG = {
    "shallow":      {"q": 14,  "alpha": 0.90},
    "intermediate": {"q": 90,  "alpha": 0.967},
    "deep":         {"q": 365, "alpha": 0.988},
}

_LAYER_BASE_VALUES = [40, 60, 80]
_LAYER_PROBABILITIES = {
    "shallow":      [0.80, 0.15, 0.05],
    "intermediate": [0.05, 0.80, 0.15],
    "deep":         [0.05, 0.15, 0.80],
}

# Thresholds to trigger upward/downward layer transitions
_JUMP_UPPER = {"shallow": 60, "intermediate": 80}
_JUMP_LOWER = {"deep": 80, "intermediate": 60}

PURGE_IMPORTANCE_THRESHOLD = 5
PURGE_RECENCY_THRESHOLD = 0.05

# Which KB categories belong in which layer
LAYER_CONTENT_TYPES = {
    "shallow":      ["market_regime", "general"],
    "intermediate": ["parameter_insight"],
    "deep":         ["failure_diagnosis"],
}


def assign_layer_and_importance(category: str) -> tuple:
    """
    Assign initial layer and importance to a new KB entry.

    Returns (layer: str, importance: int).
    Layer is determined by content type; importance is sampled stochastically.
    """
    if category == "failure_diagnosis":
        layer = "deep"
    elif category == "parameter_insight":
        layer = "intermediate"
    else:  # market_regime, general
        layer = "shallow"

    importance = random.choices(
        _LAYER_BASE_VALUES,
        weights=_LAYER_PROBABILITIES[layer],
        k=1,
    )[0]
    return layer, importance


def compute_recency_score(entry_created_at_ms: int, layer: str) -> float:
    """
    S_Recency = e^(-delta / Q_l)

    delta = current_time - entry_time (in days)
    Q_l   = layer stability period (Q)
    """
    delta_days = (time.time() * 1000 - entry_created_at_ms) / (1000 * 60 * 60 * 24)
    q = LAYER_CONFIG.get(layer, LAYER_CONFIG["shallow"])["q"]
    return math.exp(-delta_days / q)


def compute_importance_score(initial_importance: int, entry_created_at_ms: int, layer: str) -> float:
    """
    I_t = I_0 * (alpha_l ^ delta_days)

    Importance decays exponentially over time. Rate depends on layer stability.
    """
    delta_days = (time.time() * 1000 - entry_created_at_ms) / (1000 * 60 * 60 * 24)
    alpha = LAYER_CONFIG.get(layer, LAYER_CONFIG["shallow"])["alpha"]
    return initial_importance * (alpha ** delta_days)


def compute_compound_score(entry_created_at_ms: int, layer: str, importance: int) -> float:
    """
    FinMem compound score: S_compound = S_recency + I_current / 100

    Used for ranking entries within a layer before returning Top-K.
    """
    recency = compute_recency_score(entry_created_at_ms, layer)
    current_importance = compute_importance_score(importance, entry_created_at_ms, layer)
    return recency + current_importance / 100


# Structured relevancy weights — our stand-in for FinMem's embedding cosine.
RELEVANCY_REGIME_WEIGHT = 0.6
RELEVANCY_MECHANISM_WEIGHT = 0.4


def compute_structural_relevancy(entry: dict, current_regime: str = None,
                                 mechanism: str = None) -> float:
    """
    S_Relevancy in [0, 1] — the third term of FinMem's compound score.

    FinMem computes relevancy as cosine similarity between embeddings of the memory
    text and the query prompt. We substitute a structured match over tags every
    finding already carries (regime, mechanism). That costs no embedding provider,
    is deterministic, and uses domain structure FinMem did not have.

    Whether this beats a real embedding model is the open question in
    claude_docs/trials/2026-08-07-kb-structure-measured.md.
    """
    score = 0.0
    if current_regime and entry.get("regime") == current_regime:
        score += RELEVANCY_REGIME_WEIGHT
    if mechanism and entry.get("mechanism") == mechanism:
        score += RELEVANCY_MECHANISM_WEIGHT
    return score


def should_purge(entry_created_at_ms: int, layer: str, importance: int) -> bool:
    """Return True if the entry has decayed below both importance and recency thresholds."""
    if importance < PURGE_IMPORTANCE_THRESHOLD:
        return True
    recency = compute_recency_score(entry_created_at_ms, layer)
    return recency < PURGE_RECENCY_THRESHOLD


def check_promotion(entry_created_at_ms: int, layer: str, importance: int) -> Optional[str]:
    """
    Return the target layer if an entry meets criteria for upward promotion, else None.

    shallow → intermediate: current importance >= 60 AND recency > 0.7
    intermediate → deep:    current importance >= 80 AND recency > 0.7
    deep: never promoted further.
    """
    if layer not in _JUMP_UPPER:
        return None
    recency = compute_recency_score(entry_created_at_ms, layer)
    current_importance = compute_importance_score(importance, entry_created_at_ms, layer)
    threshold = _JUMP_UPPER[layer]
    if current_importance >= threshold and recency > 0.7:
        return "intermediate" if layer == "shallow" else "deep"
    return None


def check_demotion(entry_created_at_ms: int, layer: str, importance: int) -> Optional[str]:
    """
    Return the target layer if an entry meets criteria for downward demotion, else None.

    deep → intermediate:    current importance < 80
    intermediate → shallow: current importance < 60
    shallow: cannot be demoted.
    """
    if layer == "shallow":
        return None  # bottom layer — never demote
    if layer not in _JUMP_LOWER:
        return None
    current_importance = compute_importance_score(importance, entry_created_at_ms, layer)
    threshold = _JUMP_LOWER[layer]
    if current_importance < threshold:
        return "intermediate" if layer == "deep" else "shallow"
    return None
