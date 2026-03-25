"""Shared fixtures and path setup for tests."""

import os
import sys

# Add project root to sys.path so tests can import project modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "web"))
