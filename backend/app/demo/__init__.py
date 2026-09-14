"""The demo estate: a repeatable AD + SMB + NTFS dataset, and the tool that seeds it.

Read :mod:`app.demo.estate` first. It is the whole design; everything else here is
transport.
"""

from app.demo.estate import FEATURES, PROFILES, DemoEstate, build_estate
from app.demo.transcripts import DemoTranscript, build_transcripts, restamp

__all__ = [
    "FEATURES",
    "PROFILES",
    "DemoEstate",
    "DemoTranscript",
    "build_estate",
    "build_transcripts",
    "restamp",
]
