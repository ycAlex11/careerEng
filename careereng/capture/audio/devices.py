"""Audio device discovery for local capture."""

from __future__ import annotations

from typing import Any


class AudioCaptureDependencyError(RuntimeError):
    """Raised when optional audio capture dependencies are unavailable."""


def list_audio_devices() -> list[dict[str, Any]]:
    sd = _sounddevice()
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    rows: list[dict[str, Any]] = []
    for index, device in enumerate(devices):
        hostapi_index = int(device.get("hostapi", -1))
        hostapi = hostapis[hostapi_index].get("name", "") if 0 <= hostapi_index < len(hostapis) else ""
        input_channels = int(device.get("max_input_channels") or 0)
        output_channels = int(device.get("max_output_channels") or 0)
        rows.append(
            {
                "index": index,
                "name": str(device.get("name") or ""),
                "hostapi": str(hostapi or ""),
                "input_channels": input_channels,
                "output_channels": output_channels,
                "default_samplerate": float(device.get("default_samplerate") or 0.0),
                "is_input": input_channels > 0,
            }
        )
    return rows


def resolve_input_device(device: str | int | None = None) -> dict[str, Any]:
    devices = [row for row in list_audio_devices() if bool(row.get("is_input"))]
    if not devices:
        raise ValueError("No input-capable audio devices found.")
    if device in (None, ""):
        return devices[0]
    raw = str(device).strip()
    if raw.isdigit():
        wanted = int(raw)
        for row in devices:
            if int(row.get("index") or -1) == wanted:
                return row
        raise ValueError(f"Input audio device index not found: {raw}")
    lowered = raw.lower()
    matches = [row for row in devices if lowered in str(row.get("name") or "").lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(f"{row.get('index')}:{row.get('name')}" for row in matches)
        raise ValueError(f"Audio device name is ambiguous: {raw}. Matches: {names}")
    raise ValueError(f"Input audio device not found: {raw}")


def _sounddevice():
    try:
        import sounddevice as sd  # type: ignore
    except Exception as exc:
        raise AudioCaptureDependencyError(
            "Audio capture requires optional dependency `sounddevice`. "
            "Install it in the project environment with `pip install sounddevice soundfile`."
        ) from exc
    return sd

