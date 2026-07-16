import os

import json
from openai import AzureOpenAI
from typing import Callable, List, Dict, Any, Optional
from azure.identity import ClientSecretCredential
from logger import logger

# TODO Add below creds in config/env
OPENAI_ENDPOINT = "https://call-responder-bot-resource.openai.azure.com/"
DEPLOYMENT_NAME = "Call_responder_bot"
TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
CLIENT_ID = os.getenv("MYOPS_AZURE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("MYOPS_AZURE_CLIENT_SECRET", "")

class AzureADTokenProvider:
    """Provides Azure AD tokens using client credentials."""

    def __init__(
        self,
        tenant_id: str = TENANT_ID,
        client_id: str = CLIENT_ID,
        client_secret: str = CLIENT_SECRET,
    ):
        """
        Initializes the token provider.

        Args:
            tenant_id: Your Azure AD tenant ID.
            client_id: Your Azure AD application client ID.
            client_secret: Your Azure AD application client secret.
        """
        self.credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        # The scope for Azure OpenAI services
        self.scope = "https://cognitiveservices.azure.com/.default"

    def __call__(self) -> str:
        """Makes the class instance callable and returns an Azure AD token."""
        token = self.credential.get_token(self.scope)
        return token.token


class OpenAIFoundryClient:
    """
    A client to interact with OpenAI models hosted in Azure.
    It handles authentication, session management, and tool definitions with handlers.
    """

    def __init__(
        self,
        azure_ad_token_provider: Callable[[], str],
        api_version: str,
        azure_endpoint: str,
        deployment_name: str,
        temperature: float = 0.7,
        max_tokens: int = 800,
        system_prompt: str = "You are a helpful assistant.",
        history_limit: int = 15,
    ):
        """
        Initializes the OpenAIFoundryClient.

        Args:
            azure_ad_token_provider: A callable that returns an Azure AD token.
            api_version: The API version to use for the request.
            azure_endpoint: The base URL for the Azure OpenAI resource.
            deployment_name: The name of the deployment to use.
            temperature: What sampling temperature to use.
            max_tokens: The maximum number of tokens to generate.
            system_prompt: The initial system message to set the assistant's behavior.
            history_limit: The max number of messages to keep in history.
        """
        self.client = AzureOpenAI(
            api_version=api_version,
            azure_endpoint=azure_endpoint,
            azure_ad_token_provider=azure_ad_token_provider,
        )
        self.deployment_name = deployment_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.history_limit = history_limit

        self.tools: List[Dict[str, Any]] = []
        self.tool_handlers: Dict[str, Callable] = {}
        self.messages: List[Dict[str, Any]] = []
        self.reset_session()

    def register_tool(self, tool_definition: Dict[str, Any], handler: Callable):
        """
        Registers a tool and its handler.

        Args:
            tool_definition: The OpenAI tool definition.
            handler: The function to call when the tool is invoked.
        """
        function_name = tool_definition.get("function", {}).get("name")
        if not function_name:
            raise ValueError("Tool definition must include a function name.")
        self.tools.append(tool_definition)
        self.tool_handlers[function_name] = handler

    def reset_session(self):
        """Clears the conversation history and resets to the system prompt."""
        self.messages = [{"role": "system", "content": self.system_prompt}]

    def chat(
        self, prompt: str, stream_callback: Optional[Callable[[str], None]] = None
    ) -> Optional[str]:
        """
        Sends a prompt to the model, handles tool calls, and returns the response.

        Args:
            prompt: The user's message.
            stream_callback: An optional callback to handle streaming response chunks.

        Returns:
            The assistant's final text response, or None if there is no content.
        """
        self.messages.append({"role": "user", "content": prompt})

        while True:
            # Trim history if it exceeds the limit, keeping the system prompt and latest messages.
            if len(self.messages) > self.history_limit + 1:
                self.messages = [self.messages[0]] + self.messages[
                    -(self.history_limit) :
                ]

            response = self.client.chat.completions.create(
                model=self.deployment_name,
                messages=self.messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                tools=self.tools if self.tools else None,
                tool_choice="auto" if self.tools else None,
                stream=stream_callback is not None,
            )

            if stream_callback:
                # Collect both content and tool_calls during streaming
                full_response_content = ""
                assembled_tool_calls: List[Dict[str, Any]] = []

                def ensure_tc_idx(idx: int):
                    while len(assembled_tool_calls) <= idx:
                        assembled_tool_calls.append(
                            {
                                "id": None,
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        )

                for chunk in response:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    # Stream textual content immediately
                    if getattr(delta, "content", None):
                        content_chunk = delta.content
                        full_response_content += content_chunk
                        stream_callback(content_chunk)

                    # Assemble tool_calls across deltas
                    tool_call_deltas = getattr(delta, "tool_calls", None)
                    if tool_call_deltas:
                        for tc in tool_call_deltas:
                            idx = getattr(tc, "index", 0) or 0
                            ensure_tc_idx(idx)
                            acc = assembled_tool_calls[idx]
                            if getattr(tc, "id", None):
                                acc["id"] = tc.id
                            fn = getattr(tc, "function", None)
                            if fn:
                                if getattr(fn, "name", None):
                                    acc["function"]["name"] = fn.name
                                if getattr(fn, "arguments", None):
                                    acc["function"]["arguments"] += fn.arguments

                # If tool calls were produced, execute them and continue loop
                if assembled_tool_calls:
                    assistant_tool_msg = {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["function"]["name"],
                                    "arguments": tc["function"]["arguments"],
                                },
                            }
                            for tc in assembled_tool_calls
                        ],
                        "content": None,
                    }
                    self.messages.append(assistant_tool_msg)

                    for tc in assembled_tool_calls:
                        function_name = tc["function"]["name"]
                        raw_args = tc["function"]["arguments"] or "{}"
                        try:
                            function_args = json.loads(raw_args)
                        except Exception as e:
                            logger.warning(
                                f"Failed to parse tool args for {function_name}: {e}; raw={raw_args!r}"
                            )
                            function_args = {}

                        handler = self.tool_handlers.get(function_name)
                        if handler:
                            try:
                                function_response = handler(**function_args)
                            except Exception as e:
                                function_response = (
                                    f"Error while executing tool '{function_name}': {e}"
                                )
                        else:
                            function_response = (
                                f"Error: Tool '{function_name}' not found."
                            )

                        # Ensure string content
                        if not isinstance(function_response, str):
                            try:
                                function_response = json.dumps(function_response)
                            except Exception:
                                function_response = str(function_response)

                        self.messages.append(
                            {
                                "tool_call_id": tc["id"],
                                "role": "tool",
                                "name": function_name,
                                "content": function_response,
                            }
                        )
                    # Continue loop to get final assistant response after tools
                    continue

                # No tool calls: finalize streamed content
                response_message = {
                    "role": "assistant",
                    "content": full_response_content,
                }
                self.messages.append(response_message)
                return full_response_content
            else:
                response_message = response.choices[0].message
                self.messages.append(response_message)

                if not getattr(response_message, "tool_calls", None):
                    return response_message.content

                logger.info(f"Tool calls detected: {response_message.tool_calls}")

                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    handler = self.tool_handlers.get(function_name)

                    # Safe JSON parse of function arguments
                    raw_args = getattr(tool_call.function, "arguments", "") or "{}"
                    try:
                        function_args = json.loads(raw_args)
                    except Exception as e:
                        logger.warning(
                            f"Failed to parse tool args for {function_name}: {e}; raw={raw_args!r}"
                        )
                        function_args = {}

                    if handler:
                        try:
                            function_response = handler(**function_args)
                        except Exception as e:
                            function_response = (
                                f"Error while executing tool '{function_name}': {e}"
                            )
                    else:
                        function_response = f"Error: Tool '{function_name}' not found."

                    # Ensure string content
                    if not isinstance(function_response, str):
                        try:
                            function_response = json.dumps(function_response)
                        except Exception:
                            function_response = str(function_response)

                    self.messages.append(
                        {
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": function_name,
                            "content": function_response,
                        }
                    )


if __name__ == "__main__":
    # This is an example of how to use the OpenAIFoundryClient.
    # It's recommended to use environment variables for sensitive data.
    try:
        # 1. Set up the token provider
        token_provider = AzureADTokenProvider(
            tenant_id=TENANT_ID,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )

        # 2. Initialize the OpenAIFoundryClient
        foundry_client = OpenAIFoundryClient(
            azure_ad_token_provider=token_provider,
            api_version="2024-02-01",  # Use your desired API version
            azure_endpoint=OPENAI_ENDPOINT,
            deployment_name=DEPLOYMENT_NAME,
            system_prompt="You are a helpful assistant that can provide weather information.",
        )

        # 3. (Optional) Define a tool and its handler
        def get_current_weather(location: str, unit: str = "celsius") -> str:
            """Get the current weather in a given location."""
            # This is a mock function. In a real scenario, you would call a weather API.
            return f"The weather in {location} is 25 degrees {unit}."

        weather_tool_definition = {
            "type": "function",
            "function": {
                "name": "get_current_weather",
                "description": "Get the current weather in a given location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The city and state, e.g., San Francisco, CA",
                        },
                        "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                    },
                    "required": ["location"],
                },
            },
        }

        # 4. Register the tool with the client
        foundry_client.register_tool(weather_tool_definition, get_current_weather)

        # 5. Start a chat
        print("Chat with the assistant (type 'exit' to quit).")
        while True:
            user_prompt = input("You: ")
            if user_prompt.lower() == "exit":
                break

            response = foundry_client.chat(user_prompt)
            print(f"Assistant: {response}")

    except KeyError as e:
        print(f"Error: Environment variable {e} not set.")
        print(
            "Please set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, "
            "AZURE_OPENAI_ENDPOINT, and AZURE_OPENAI_DEPLOYMENT_NAME."
        )
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
