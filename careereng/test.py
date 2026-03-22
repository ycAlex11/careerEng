# /// script
# dependencies = [
#   "openai",
# ]
# ///
import os

from openai import OpenAI

client = OpenAI(
    api_key="sk-zTUvYLUttPkawkfUlxsGTQhJDWN0PJNT3ra58kKbD7M3z4xv",
    base_url="https://www.packyapi.com/v1",
)

with client.responses.stream(
    model="gpt-5.4",
    # PackyAPI accepts the curl-style array input for streaming, but rejects
    # the simple string form with a 400 compatibility error.
    input=[
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "如何使用 python sdk 调用你",
                }
            ],
        }
    ],
    store=False,
    include=["reasoning.encrypted_content"],
) as stream:
    for event in stream:
        if event.type == "response.output_text.delta":
            print(event.delta, end="", flush=True)

print()
