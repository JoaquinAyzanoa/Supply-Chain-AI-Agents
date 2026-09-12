"""Service-to-service plumbing.

Phase 4 adds signed events (``events``): producers POST typed events to the
director with an HMAC signature; the director verifies it before parsing.
Phase 5 adds the A2A server and client helpers next to it.
"""
