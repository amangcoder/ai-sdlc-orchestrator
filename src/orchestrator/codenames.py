"""Generate fun random codenames for parallel agents (Docker container name style)."""

from __future__ import annotations

import random

ADJECTIVES = [
    "bold", "calm", "deft", "epic", "fast", "keen", "sage", "warm",
    "brave", "crisp", "eager", "fleet", "lucid", "noble", "sharp",
    "vivid", "witty", "agile", "bright", "steady", "swift", "clever",
    "gentle", "mighty", "nimble", "quiet", "rustic", "serene", "plucky",
    "cosmic", "frosty", "golden", "lively", "mystic", "radiant", "zesty",
]

NOUNS = [
    "falcon", "otter", "panda", "raven", "tiger", "whale", "cedar",
    "comet", "ember", "forge", "nexus", "prism", "spark", "atlas",
    "crest", "drift", "frost", "grove", "haven", "lunar", "orbit",
    "pulse", "quest", "ridge", "shore", "summit", "phoenix", "maple",
    "coral", "aurora", "breeze", "canyon", "delta", "flint", "harbor",
]


def generate_codename(exclude: set[str] | None = None) -> str:
    """Generate a unique adjective-noun codename not in the exclude set."""
    exclude = exclude or set()
    for _ in range(200):
        name = f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}"
        if name not in exclude:
            return name
    return f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}-{random.randint(10, 99)}"
