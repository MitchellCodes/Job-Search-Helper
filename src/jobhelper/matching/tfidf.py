"""Pure-Python IDF weights over the user's saved listings.

A skill asked for by nearly every listing is boilerplate; a rare one is distinctive.
With few listings the weights stay close to 1 so scores don't swing wildly.
"""
import math


def idf_weights(listing_term_sets: list[set[str]]) -> dict[str, float]:
    n = len(listing_term_sets)
    df: dict[str, int] = {}
    for terms in listing_term_sets:
        for t in terms:
            df[t] = df.get(t, 0) + 1
    # smoothed IDF, shrunk toward 1.0 when the corpus is small
    shrink = min(1.0, n / 20)
    return {t: 1.0 + shrink * (math.log((1 + n) / (1 + c)) + 0.0) for t, c in df.items()}


def weight(idf: dict[str, float], term: str) -> float:
    return idf.get(term, 1.0)
