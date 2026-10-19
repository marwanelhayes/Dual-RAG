"""Adversarial routing questions must remain distinct, balanced, and correctly classified."""

import json
from collections import Counter
from pathlib import Path

import agent
import configure as cfg


CHALLENGE_PATH = Path(__file__).parent / "fixtures" / "routing_challenge.json"


def _questions(path):
    """Load routing questions from one JSON fixture."""
    return json.loads(path.read_text(encoding="utf-8"))


def test_routing_challenge_is_balanced_unique_and_new():
    """Protect the challenge set from imbalance or development-set duplication."""
    challenge = _questions(CHALLENGE_PATH)
    development = _questions(cfg.QUESTION_SET_PATH)
    normalized = {case["question"].strip().casefold() for case in development}

    assert Counter(case["expected_query_type"] for case in challenge) == {
        "SPEC": 20, "RTL": 20, "COMPARISON": 20}
    assert len({case["id"] for case in challenge}) == len(challenge)
    assert all(case["question"].strip().casefold() not in normalized for case in challenge)


def test_routing_challenge_matches_declared_intent(routing_vocabulary):
    """Require every adversarial paraphrase to follow its declared evidence route."""
    mismatches = {}
    for case in _questions(CHALLENGE_PATH):
        question = case["question"]
        variants = (question, question.upper(), f"  {question}  ",
                    question.rstrip("?.!"))
        for index, variant in enumerate(variants):
            actual = agent.classify_query(variant)
            if actual != case["expected_query_type"]:
                mismatches[f'{case["id"]}:{index}'] = actual

    assert mismatches == {}
