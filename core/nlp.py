"""Lazy access to the shared sentiment-analysis pipeline.

The monolith imported `transformers.pipeline` and instantiated the model
unconditionally at module import time, which meant *every* process import
(including e.g. a throwaway REPL import for testing an unrelated command)
paid the multi-second model-load cost. `get_classifier()` defers that
until something actually needs it, and only pays it once per process.
"""
from __future__ import annotations

_classifier = None
_nlp = None
TOKENIZER_KWARGS = {"padding": True, "truncation": True, "max_length": 128}


def get_classifier():
    global _classifier
    if _classifier is None:
        from transformers import pipeline
        _classifier = pipeline("sentiment-analysis", model="bhadresh-savani/bert-base-go-emotion", top_k=None)
    return _classifier


def get_nlp():
    """Lazy access to the spaCy English pipeline `/8ball` uses for
    question classification. Same reasoning as `get_classifier()` above --
    the monolith loaded this unconditionally at import time."""
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load("en_core_web_md")
    return _nlp
