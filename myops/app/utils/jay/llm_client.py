"""
Jay LLM Client - Shared Azure OpenAI client singleton.

Provides a lazy-initialized, **thread-safe** client used by all pipeline steps.
Double-checked locking is used for both the LLM and embedding client singletons
to prevent race conditions during parallel pipeline execution.

Supported tiers:
  - default:   GPT-5.2      (jai-gpt52) — primary SQL generation
  - fast:      gpt-4o-mini  (sql-test-intent) — intent detection
  - reasoning: GPT-5.2      (jai-gpt52) — same as default for now
  - fallback:  gpt-4o-mini  (sql-test-intent) — fallback
"""

import logging
import os
import threading
from typing import Dict, List, Tuple

from httpx import Timeout
from openai import AzureOpenAI
from azure.identity import ClientSecretCredential, DefaultAzureCredential

from app.core.config import settings

logger = logging.getLogger(__name__)

# Lazy-initialized singleton with thread-safe locking
_client: AzureOpenAI = None
_deployments: Dict[str, str] = {}
_client_lock = threading.Lock()

_VALID_TIERS = {"default", "fast", "reasoning", "fallback"}


def _create_llm_client() -> AzureOpenAI:
    """Create Azure OpenAI client using application credentials."""
    if settings.AZURE_CLIENT_SECRET:
        credential = ClientSecretCredential(
            tenant_id=settings.AZURE_TENANT_ID,
            client_id=settings.AZURE_CLIENT_ID,
            client_secret=settings.AZURE_CLIENT_SECRET,
        )
    else:
        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)

    from azure.keyvault.secrets import SecretClient
    kv_client = SecretClient(
        vault_url=settings.AZURE_KEYVAULT_URL, credential=credential
    )
    api_key = kv_client.get_secret("sql-gen-bot").value

    return AzureOpenAI(
        api_key=api_key,
        azure_endpoint="https://sql-test-resource.cognitiveservices.azure.com/",
        api_version="2024-02-15-preview",
        timeout=Timeout(30.0, connect=10.0),
    )


def _resolve_deployments() -> Dict[str, str]:
    """Resolve deployment names for each tier from env vars."""
    default = os.environ.get("JAI_DEPLOYMENT_DEFAULT", "jai-gpt52")
    fast = os.environ.get(
        "JAI_DEPLOYMENT_FAST",
        os.environ.get("AZURE_OPENAI_FAST_DEPLOYMENT", "sql-test-intent"),
    )
    reasoning = os.environ.get("JAI_DEPLOYMENT_REASONING", default)
    fallback = os.environ.get("JAI_DEPLOYMENT_FALLBACK", "sql-test-intent")

    deployments = {
        "default": default,
        "fast": fast,
        "reasoning": reasoning,
        "fallback": fallback,
    }
    logger.info("LLM deployments resolved: %s", deployments)
    return deployments


def get_llm_client(tier: str = "default") -> Tuple[AzureOpenAI, str]:
    """Get the shared Azure OpenAI client (lazy singleton, thread-safe).

    Args:
        tier: One of ``"default"``, ``"fast"``, ``"reasoning"``, ``"fallback"``.
              Unknown tiers fall back to default with a warning.

    Returns:
        Tuple of (AzureOpenAI client, deployment_name)
    """
    global _client, _deployments
    if _client is None:
        with _client_lock:
            if _client is None:
                _deployments = _resolve_deployments()
                _client = _create_llm_client()

    if tier not in _VALID_TIERS:
        logger.warning("Unknown LLM tier '%s', falling back to default", tier)
        tier = "default"

    deployment = _deployments.get(tier, _deployments["default"])
    logger.debug("get_llm_client(tier=%s) -> deployment=%s", tier, deployment)
    return _client, deployment


def list_available_deployments() -> Dict[str, str]:
    """Return configured deployment names for all tiers."""
    get_llm_client()  # ensure initialized
    return dict(_deployments)


# ---------------------------------------------------------------------------
# Embedding client (reuses the same AzureOpenAI instance, different deployment)
# ---------------------------------------------------------------------------

_embedding_client: AzureOpenAI = None
_embedding_deployment: str = None
_embedding_lock = threading.Lock()

_EMBEDDING_BATCH_SIZE = 16  # Azure OpenAI per-request limit


def get_embedding_client() -> Tuple[AzureOpenAI, str]:
    """Get the shared embedding client (lazy singleton, thread-safe).

    Reuses the same AzureOpenAI instance as the LLM client since the
    endpoint is identical; only the deployment name differs.

    Uses double-checked locking to prevent duplicate initialization
    when multiple threads call this concurrently.

    The deployment name is read from the ``JAI_EMBEDDING_DEPLOYMENT``
    environment variable (default: ``text-embedding-3-small``).

    Returns:
        Tuple of (AzureOpenAI client, embedding_deployment_name)
    """
    global _embedding_client, _embedding_deployment
    if _embedding_client is None:
        with _embedding_lock:
            if _embedding_client is None:
                # Reuse the LLM client instance (same endpoint & API key)
                llm_client, _ = get_llm_client()
                _embedding_client = llm_client
                _embedding_deployment = os.environ.get(
                    "JAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small"
                )
                logger.info(
                    "Embedding client initialised (deployment=%s)", _embedding_deployment
                )
    return _embedding_client, _embedding_deployment


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Generate embeddings for a list of texts.

    Texts are processed in batches of ``_EMBEDDING_BATCH_SIZE`` to stay
    within Azure OpenAI per-request limits.

    Args:
        texts: Input strings to embed.

    Returns:
        A list of embedding vectors (each a list of floats, typically
        1536-dimensional) in the same order as *texts*.

    Raises:
        RuntimeError: If the Azure OpenAI API call fails after logging.
    """
    if not texts:
        return []

    client, deployment = get_embedding_client()
    all_embeddings: List[List[float]] = []

    for start in range(0, len(texts), _EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + _EMBEDDING_BATCH_SIZE]
        try:
            response = client.embeddings.create(input=batch, model=deployment)
            # Results are returned sorted by index; sort to be safe
            batch_embeddings = [
                item.embedding
                for item in sorted(response.data, key=lambda d: d.index)
            ]
            all_embeddings.extend(batch_embeddings)
        except Exception:
            logger.exception(
                "Embedding API call failed for batch starting at index %d "
                "(batch_size=%d, total=%d)",
                start,
                len(batch),
                len(texts),
            )
            raise RuntimeError(
                f"Failed to generate embeddings for batch at index {start}"
            )

    logger.debug("Generated %d embeddings across %d texts", len(all_embeddings), len(texts))
    return all_embeddings
