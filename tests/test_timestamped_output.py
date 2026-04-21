"""Tests for timestamp-prefixed stderr/stdout wrappers."""

import io
import re

from vocal_more.infrastructure.timestamped_output import TimestampedTextStream


def test_timestamped_stream_prefixes_each_completed_line():
    target = io.StringIO()
    stream = TimestampedTextStream(target)

    stream.write("first line\nsecond")
    stream.write(" line\n")
    stream.flush()

    lines = target.getvalue().splitlines()
    assert len(lines) == 2
    assert re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\] first line$", lines[0])
    assert re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\] second line$", lines[1])
