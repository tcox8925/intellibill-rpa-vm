import os

import json
from openai import AzureOpenAI

endpoint = "https://powerbi-chat.cognitiveservices.azure.com/"
model_name = "gpt-4.1-nano"
deployment = "gpt-4.1-nano"

subscription_key = os.getenv("NPI_SUBSCRIPTION_KEY", "")
api_version = "2024-12-01-preview"

client = AzureOpenAI(
    api_version=api_version,
    azure_endpoint=endpoint,
    api_key=subscription_key,
)

def validate_with_openai(npi: str, carriers: list) -> dict:
    """
    Simplified GPT prompt to:
    - Get all credentialing info for the NPI
    - Get NAIC numbers for the carriers list
    carriers is a list of dicts with at least carrier_name keys.
    """
    carriers_names = [c.get("carrier_name") for c in carriers if c.get("carrier_name")]
    carriers_str = ", ".join(carriers_names) if carriers_names else "none"

    messages = [
        {
            "role": "system",
            "content": f"""You are an expert in healthcare provider credentialing and insurance carrier data.
Given an NPI and a list of insurance carriers, your task is:

1. Provide all credentialing information related to the provider with NPI {npi},
   especially government-issued identifiers like PECOS ID.

2. For each carrier in the list, provide detailed NAIC information including legal entity name, NAIC number, group number if available, and state of domicile.

Respond ONLY with a valid JSON object with keys: identifiers (list of credentialing IDs), carriers (list with NAIC info added).
DO NOT include comments, explanations, placeholders, or any text other than valid JSON.
If information is unavailable, omit the field or set it to null.

Example output:
{{
  "identifiers": [
    {{"id_type": "PECOS", "id_value": "1234567890"}}
  ],
  "carriers": [
    {{
      "carrier_name": "Humana",
      "naic_info": {{
        "entity": "Humana Inc.",
        "naic_number": "73288",
        "group_number": "",
        "state_of_domicile": "KY"
      }}
    }}
  ]
}}"""
        },
        {
            "role": "user",
            "content": f"NPI: {npi}\nCarriers: {carriers_str}"
        }
    ]

    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=messages,
            max_tokens=2500,
            temperature=0.8
        )
        raw_text = response.choices[0].message.content.strip()
        print("GPT raw response text:", raw_text)

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as e:
            print(f"Failed to parse GPT JSON: {e}")
            print("Raw GPT output was:", raw_text)
            return {}

    except Exception as e:
        print(f"GPT validation failed: {e}")
        return {}
