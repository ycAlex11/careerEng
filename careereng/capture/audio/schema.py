"""Audio capture schema constants."""

from __future__ import annotations


AUDIO_MARKER_QUESTION = "question"
AUDIO_MARKER_ANSWER = "answer"
AUDIO_MARKER_UNKNOWN = "unknown"
AUDIO_MARKER_STOP = "stop"
AUDIO_MARKERS = {
    AUDIO_MARKER_QUESTION,
    AUDIO_MARKER_ANSWER,
    AUDIO_MARKER_UNKNOWN,
    AUDIO_MARKER_STOP,
}

SOURCE_MANUAL_CAPTURE = "manual_capture"

