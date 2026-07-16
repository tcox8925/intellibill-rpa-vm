import os
# ==========================================================
#  utils/ai_utils.py
# ==========================================================
"""
ai_utils.py
-----------
Purpose:
    - Connect to Azure AI Foundry via OpenAI-compatible SDK.
    - API key pulled from Key Vault (same vault as all other secrets).
    - Provides call_ai_model() for the intelligence layer.

Model:
    Configurable — deployment name set in AI_CONFIG.
    Update 'deployment_name' and 'endpoint' when swapping models.
"""

import re
from typing import Optional

from openai import OpenAI
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient


# ==========================================================
#  CONFIGURATION
# ==========================================================
KEYVAULT_NAME = os.getenv("KEY_VAULT_NAME", "")
KEYVAULT_URL = f"https://{KEYVAULT_NAME}.vault.azure.net/"
AI_API_KEY_SECRET = "834-acu-bob-ai"  # secret name in Key Vault

AI_CONFIG = {
    "endpoint": "https://sql-test-resource.services.ai.azure.com/openai/v1/",
    "deployment_name": "acu_bob_intelligence",
    "max_tokens": 2048,
    "temperature": 0.3,
}

_cached_api_key: Optional[str] = None


# ==========================================================
#  KEY VAULT
# ==========================================================
def _get_api_key() -> Optional[str]:
    """Fetch AI Foundry API key from Key Vault (cached after first call)."""
    global _cached_api_key
    if _cached_api_key is not None:
        return _cached_api_key
    try:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=KEYVAULT_URL, credential=credential)
        _cached_api_key = client.get_secret(AI_API_KEY_SECRET).value
        print(f"  [ai] API key loaded from Key Vault ({AI_API_KEY_SECRET})")
        return _cached_api_key
    except Exception as e:
        print(f"  [ai] Key Vault error: {e}")
        return None


# ==========================================================
#  CLIENT
# ==========================================================
def _get_ai_client() -> Optional[OpenAI]:
    """Return an OpenAI-compatible client for Azure AI Foundry."""
    api_key = _get_api_key()
    if not api_key:
        return None
    return OpenAI(
        base_url=AI_CONFIG["endpoint"],
        api_key=api_key,
    )


def get_ai_client_config() -> Optional[dict]:
    """Returns AI config if Key Vault secret is accessible, else None."""
    api_key = _get_api_key()
    if not api_key:
        return None
    print(f"  [ai] Config ready (deployment: {AI_CONFIG['deployment_name']})")
    return AI_CONFIG


# ==========================================================
#  MODEL CALL
# ==========================================================
def call_ai_model(prompt: str, system_message: Optional[str] = None) -> Optional[str]:
    """
    Send a prompt to the model via Azure AI Foundry.

    Parameters
    ----------
    prompt : str
        User prompt (structured data to analyze).
    system_message : str, optional
        System message to set model behavior.

    Returns
    -------
    str or None
        Model response text, or None on failure.
    """
    client = _get_ai_client()
    if not client:
        print("  [ai] Not configured — skipping model call")
        return None

    try:
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})

        completion = client.chat.completions.create(
            model=AI_CONFIG["deployment_name"],
            messages=messages,
            max_tokens=AI_CONFIG["max_tokens"],
            temperature=AI_CONFIG["temperature"],
        )

        content = completion.choices[0].message.content or ""

        # Strip <think>...</think> blocks (some reasoning models output these)
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"<think>.*", "", content, flags=re.DOTALL).strip()

        print(f"  [ai] Model responded ({len(content)} chars)")
        return content if content else None

    except Exception as e:
        print(f"  [ai] Model call error: {e}")
        return None