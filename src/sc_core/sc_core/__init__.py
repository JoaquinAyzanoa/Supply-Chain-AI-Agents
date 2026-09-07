"""Shared library for every Supply Chain AI Agents service.

Sub-packages (filled in by the phase 0 stories):

- ``infra``: settings, logging, health checks, exceptions
- ``app``: FastAPI application factory, middleware and common routes
- ``schema``: strict base models and event contracts
- ``shared``: domain errors and small utilities (time, money, idempotency)

Later phases add ``odoo``, ``mail``, ``llm``, ``a2a``, ``graph`` and ``prompts``.
"""
