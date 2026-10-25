"""Assistant: turns engine outputs into plain-language text without letting a model touch a number or a safety line.

Flow (compose.py): fact card -> sanitize -> cache -> model (placeholders only) -> guard -> fill from the card ->
add safety text -> response with provenance. Any failure returns the code-built fallback.
"""
ENGINE_VERSION = "assistant-v1"
