"""Operator tooling for the machine that FILLS the vault.

A package so these modules can be IMPORTED (tools/adspower_profile.py
reuses the proxy rules from assign_proxies rather than copying them)
as well as run directly, which is how they have always been invoked.
Never reached by the actor: `src/` imports nothing from here, and
.dockerignore/.actorignore keep the whole directory out of the image
and the upload.
"""
