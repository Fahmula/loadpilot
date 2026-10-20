import os

# Set at image build time from the git tag (see Dockerfile / release workflow).
__version__ = os.getenv("LOADPILOT_VERSION", "dev")
