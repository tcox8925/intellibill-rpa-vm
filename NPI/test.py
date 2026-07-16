from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI
import json
import os
import sys
import traceback

def main():
    try:
        # 1) Get MI token
        cred = DefaultAzureCredential()
        token = cred.get_token("https://cognitiveservices.azure.com/.default")
        print("✅ Got MI token (length):", len(token.token))

        # 2) Init client (use your real api_version + endpoint)
        client = AzureOpenAI(
            azure_endpoint="https://powerbi-chat.cognitiveservices.azure.com/",
            api_version="2024-12-01-preview",
            azure_ad_token=token.token,
        )

        # 3) Call your deployment
        resp = client.chat.completions.create(
            model="data-validator",  # deployment name
            messages=[{"role": "user", "content": "Hello from Managed Identity!"}],
        )

        # 4) Print the assistant message
        msg = resp.choices[0].message.content if resp.choices else "(no choices)"
        print("🗨️  Response:", msg)

        # (Optional) Full JSON
        # print(json.dumps(resp.model_dump(), indent=2))

    except Exception as e:
        print("❌ Error:", e)
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
