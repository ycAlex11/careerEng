# /// script
# dependencies = [
#   "openai",
# ]
# ///
import sys
from pathlib import Path

from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from careereng.config.loader import load_auth, load_config

config = load_config(PROJECT_ROOT)
auth = load_auth(PROJECT_ROOT)
api_key = auth.openai_api_key
api_base = config.providers.openai.api_base or "https://api.openai.com/v1"
model = config.agent.default_model

if not api_key:
    raise SystemExit("Missing OpenAI-compatible API key in auth.json providers.openai.api_key")

client = OpenAI(
    api_key=api_key,
    base_url=api_base,
)

print(f"api_base={api_base}")
print(f"model={model}")
print(f"has_key={bool(api_key)}")

event_types: list[str] = []
text_parts: list[str] = []

try:
    with client.responses.stream(
        model=model,
        # PackyAPI accepts the curl-style array input for streaming, but rejects
        # the simple string form with a 400 compatibility error.
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Return exactly STREAM_OK.",
                    }
                ],
            }
        ],
        store=False,
        include=["reasoning.encrypted_content"],
    ) as stream:
        for event in stream:
            event_type = str(getattr(event, "type", "") or "")
            if event_type and event_type not in event_types:
                event_types.append(event_type)
            if event_type == "response.output_text.delta":
                text_parts.append(str(getattr(event, "delta", "") or ""))
except TypeError as exc:
    # Some OpenAI-compatible gateways stream valid deltas but return a malformed
    # final response object that the OpenAI SDK cannot parse.
    if not text_parts:
        raise
    print(f"stream_final_parse_warning={exc}")

text = "".join(text_parts).strip()
print(f"event_types={event_types}")
print(f"output_text={text!r}")
print(f"stream_salvage_ok={text == 'STREAM_OK'}")
