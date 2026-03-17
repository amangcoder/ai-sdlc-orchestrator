"""AI SDLC Orchestrator — coordinates AI agents through a complete Software Development Life Cycle."""

try:
    from orchestrator._version import __version__
except ModuleNotFoundError:
    # Editable install or running from source without build — fall back
    __version__ = "0.0.0.dev0"
# this is for updating knowledge