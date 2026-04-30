"""Print the installed distribution version for packaging scripts."""

from importlib.metadata import version

print(version("vocal-more"))
