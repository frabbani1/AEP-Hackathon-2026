"""HTTP layer for AWS Lambda behind an API Gateway HTTP API (payload format 2.0).

Handlers stay thin: parse and validate the request, call a deterministic engine, serialize the engine's
whitelisted output. No handler computes, rounds, or invents a number.
"""
