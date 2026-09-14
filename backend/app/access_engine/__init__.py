"""Effective-access semantics.

The backend owns authorization math: SMB and NTFS rights algebra, ACE precedence, group
expansion, and access explanation. Collectors and the frontend must never reimplement it.
"""
