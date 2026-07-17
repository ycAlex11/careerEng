"""Continuous audio recorder with manual chunk rollover."""

from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
import sys
import termios
import tty
from typing import Any

from careereng.career.interviews.capture.devices import resolve_input_device
from careereng.career.interviews.capture.schema import (
    AUDIO_MARKER_ANSWER,
    AUDIO_MARKER_QUESTION,
    AUDIO_MARKER_STOP,
    AUDIO_MARKER_UNKNOWN,
    SOURCE_MANUAL_CAPTURE,
)
from careereng.utils import ensure_dir, make_id, now_iso


KEY_MARKERS = {
    "q": AUDIO_MARKER_QUESTION,
    "a": AUDIO_MARKER_ANSWER,
    "n": AUDIO_MARKER_UNKNOWN,
    "s": AUDIO_MARKER_STOP,
}


def capture_audio_chunks(
    *,
    output_dir: Path | str,
    device: str | int | None = None,
    sample_rate: int = 16000,
    channels: int = 1,
    source: str = SOURCE_MANUAL_CAPTURE,
) -> list[dict[str, Any]]:
    """Record audio continuously and save a chunk on each q/a/n/s keypress."""
    sd, sf = _audio_dependencies()
    resolved = resolve_input_device(device)
    output_path = ensure_dir(Path(output_dir))
    queue: Queue[Any] = Queue()
    chunks: list[dict[str, Any]] = []

    def callback(indata, frames, time_info, status):  # noqa: ANN001
        if status:
            queue.put(("status", str(status)))
        queue.put(("audio", indata.copy()))

    print("Recording. Press q=question, a=answer, n=unknown, s=stop.", flush=True)
    started_at = now_iso()
    frames: list[Any] = []
    with _raw_terminal():
        with sd.InputStream(
            samplerate=sample_rate,
            channels=channels,
            device=int(resolved["index"]),
            callback=callback,
        ):
            while True:
                frames.extend(_drain_audio(queue))
                key = _read_key()
                if key not in KEY_MARKERS:
                    continue
                marker = KEY_MARKERS[key]
                frames.extend(_drain_audio(queue))
                chunk = _write_chunk(
                    sf=sf,
                    output_dir=output_path,
                    frames=frames,
                    started_at=started_at,
                    marker=marker,
                    sample_rate=sample_rate,
                    channels=channels,
                    source=source,
                    device=resolved,
                )
                if chunk:
                    chunks.append(chunk)
                    print(f"\nSaved {marker}: {chunk['audio_path']}", flush=True)
                if marker == AUDIO_MARKER_STOP:
                    break
                started_at = now_iso()
                frames = []
    return chunks


def _audio_dependencies():
    try:
        import sounddevice as sd  # type: ignore
        import soundfile as sf  # type: ignore
    except Exception as exc:
        raise RuntimeError("Audio recording requires `sounddevice` and `soundfile`.") from exc
    return sd, sf


def _drain_audio(queue: Queue[Any]) -> list[Any]:
    frames: list[Any] = []
    while True:
        try:
            kind, payload = queue.get_nowait()
        except Empty:
            break
        if kind == "audio":
            frames.append(payload)
    return frames


def _write_chunk(
    *,
    sf: Any,
    output_dir: Path,
    frames: list[Any],
    started_at: str,
    marker: str,
    sample_rate: int,
    channels: int,
    source: str,
    device: dict[str, Any],
) -> dict[str, Any] | None:
    if not frames:
        return None
    import numpy as np

    chunk_id = make_id("audio_chunk")
    ended_at = now_iso()
    audio = np.concatenate(frames, axis=0)
    audio_path = output_dir / f"{chunk_id}.wav"
    sf.write(str(audio_path), audio, sample_rate)
    duration = float(len(audio)) / float(sample_rate) if sample_rate else 0.0
    return {
        "chunk_id": chunk_id,
        "created_at": ended_at,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": round(duration, 3),
        "audio_path": str(audio_path),
        "device": str(device.get("name") or ""),
        "device_index": int(device.get("index") or 0),
        "sample_rate": int(sample_rate),
        "channels": int(channels),
        "marker": marker,
        "source": source,
    }


def _read_key() -> str:
    return sys.stdin.read(1).lower()


class _raw_terminal:
    def __enter__(self):
        self.fd = sys.stdin.fileno()
        self.old_settings = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)
        return False

