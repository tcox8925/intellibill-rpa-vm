import os

import fitz  # PyMuPDF for PDF handling
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.identity import ClientSecretCredential
from azure.core.exceptions import HttpResponseError
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import ListSortOrder
import logging

# ------------------ Logging Setup ------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ocr-pipeline")

# Reduce verbose Azure SDK logs
for lib in ["azure", "azure.core.pipeline", "azure.identity", "azure.ai.documentintelligence", "azure.ai"]:
    logging.getLogger(lib).setLevel(logging.WARNING)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.ERROR)

# ------------------ Azure Configuration ------------------
DOCINT_ENDPOINT = "https://pdfmedicaldetail.cognitiveservices.azure.com/"
# DOCINT_KEY = os.getenv("MYOPS_DOCINTEL_KEY", "")

TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
CLIENT_ID = os.getenv("MYOPS_AZURE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("MYOPS_AZURE_CLIENT_SECRET", "")


PROJECT_ENDPOINT = "https://call-centre-agent.services.ai.azure.com/api/projects/call-centre-agent"
AGENT_NAME = "pdf_extraction2"


# ------------------ OCR Setup ------------------
# Create a single reusable client for OCR
# ocr_client = DocumentIntelligenceClient(endpoint=DOCINT_ENDPOINT, credential=AzureKeyCredential(DOCINT_KEY))
ocr_client = DocumentIntelligenceClient(endpoint=DOCINT_ENDPOINT, credential=ClientSecretCredential(TENANT_ID, CLIENT_ID, CLIENT_SECRET))
def extract_images_from_pdf(pdf_path: str) -> list[bytes]:
    """
    Extract all images from a PDF using PyMuPDF.
    Returns a list of image bytes.
    """
    logger.info(f"Loading PDF: {pdf_path}")
    doc = fitz.open(pdf_path)
    images = []

    for page_num, page in enumerate(doc, start=1):
        for img in page.get_images(full=True):
            xref = img[0]  # Image reference
            images.append(doc.extract_image(xref)["image"])

    logger.info(f"Extracted {len(images)} images from {len(doc)} pages")
    return images

def ocr_bytes(content_bytes: bytes) -> str:
    """
    Perform OCR on bytes (either image or PDF) using Azure Document Intelligence.
    Returns extracted text as string.
    """
    poller = ocr_client.begin_analyze_document(model_id="prebuilt-read", body={"base64Source": content_bytes})
    result = poller.result()
    return result.content or ""

def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Determine if PDF is scanned or text-based and extract text accordingly.
    If scanned, runs OCR on each image; otherwise, runs OCR directly on PDF bytes.
    """
    images = extract_images_from_pdf(pdf_path)

    if images:
        logger.info("Detected scanned PDF. Running OCR on images...")
        return "\n".join(ocr_bytes(img) for img in images).strip()
    else:
        logger.info("Text-based PDF detected. Extracting text directly...")
        with open(pdf_path, "rb") as f:
            return ocr_bytes(f.read())

# ------------------ Azure LLM Agent ------------------
def get_foundry_client() -> AIProjectClient:
    """
    Authenticate and return an AIProjectClient to interact with Azure LLM agents.
    """
    client = AIProjectClient(endpoint=PROJECT_ENDPOINT,
                             credential=ClientSecretCredential(TENANT_ID, CLIENT_ID, CLIENT_SECRET))
    try:
        # Test if we can list agents
        next(iter(client.agents.list_agents()), None)
        return client
    except HttpResponseError:
        logger.error("Cannot access Azure Project Agents")
        raise

def resolve_agent_id(project: AIProjectClient, agent_name: str) -> str:
    """
    Get the agent ID for a given agent name.
    """
    for agent in project.agents.list_agents():
        if getattr(agent, "name", "") == agent_name:
            return agent.id
    raise RuntimeError(f"Agent '{agent_name}' not found")

def send_to_llm_agent(text: str) -> str:
    """
    Send OCR text to Azure LLM agent and return its response.
    """
    project = get_foundry_client()
    agent_id = resolve_agent_id(project, AGENT_NAME)

    # Create a new thread for the conversation
    thread = project.agents.threads.create()
    project.agents.messages.create(thread_id=thread.id, role="user", content=text)

    # Run the agent on the thread
    run = project.agents.runs.create_and_process(thread_id=thread.id, agent_id=agent_id)
    if run.status == "failed":
        raise RuntimeError(run.last_error)

    # Collect assistant messages
    chunks = []
    for msg in project.agents.messages.list(thread_id=thread.id, order=ListSortOrder.ASCENDING):
        if msg.run_id == run.id and msg.role == "assistant":
            if getattr(msg, "text_messages", None):
                chunks.extend([tm.text.value for tm in msg.text_messages])
            elif getattr(msg, "content", None):
                chunks.extend([c.text.value for c in msg.content if getattr(c, "text", None)])

    return "\n".join(chunks).strip()

# ------------------ Full Pipeline ------------------
def process_pdf_and_get_llm_response(pdf_path: str) -> str:
    """
    Full pipeline: PDF -> OCR -> LLM -> return LLM output.
    """
    logger.info("🚀 Starting PDF -> OCR -> LLM pipeline")
    ocr_text = extract_text_from_pdf(pdf_path)

    if not ocr_text.strip():
        logger.warning("OCR returned no text!")
        return "OCR returned no text."

    return send_to_llm_agent(ocr_text)
