from msgraph import GraphServiceClient
from app.core.config import settings
from msgraph.generated.models.chat_message import ChatMessage
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.users.item.mail_folders.item.messages.messages_request_builder import MessagesRequestBuilder

async def graph_client():
    scopes = ['https://graph.microsoft.com/.default']  # Important: use .default for app permissions
    client = GraphServiceClient(credentials=settings._azure_credential, scopes=scopes)
    return client


async def list_all_teams():
    client = await graph_client()
    teams = await client.teams.get()
    return [
        {
            "team_id": team.id,
            "team_name": team.display_name
        }
        for team in teams.value
    ]

async def get_user_inbox(user_email: str, top: int = 10, unread_only: bool = False):
    """
    Fetch inbox emails for a specific user.

    :param user_email: User principal name (email)
    :param top: Number of emails to fetch
    :param unread_only: If True, fetch only unread emails
    :return: List of emails
    """
    client = await graph_client()

    query_params = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
        top=top
    )

    if unread_only:
        query_params.filter = "isRead eq false"

    request_config = MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration(
        query_parameters=query_params
    )

    messages = await client.users.by_user_id(user_email) \
        .mail_folders.by_mail_folder_id("Inbox") \
        .messages.get(request_configuration=request_config)

    if not messages or not messages.value:
        return []

    return [
        {
            "id": message.id,
            "subject": message.subject,
            "from": message.from_.email_address.address if message.from_ else None,
            "received": message.received_date_time,
            "is_read": message.is_read
        }
        for message in messages.value
    ]


async def list_all_channels(team_id: str):
    client = await graph_client()
    channels = await client.teams.by_team_id(team_id).channels.get()
    return [
        {
            "channel_id": channel.id,
            "channel_name": channel.display_name,
            "description": channel.description,
            "email": channel.email
        }
        for channel in channels.value
    ]

async def get_channel_details(team_id: str, channel_id: str):
    client = await graph_client()
    channel = await (
        client
        .teams
        .by_team_id(team_id)
        .channels
        .by_channel_id(channel_id)
        .get()
    )

    return {
        "channel_id": channel.id,
        "channel_name": channel.display_name,
        "description": channel.description,
        "email": channel.email,
        "membership_type": channel.membership_type
    }


async def send_message_to_channel(team_id: str, channel_id: str, message: str):
    client = await graph_client()

    chat_message = ChatMessage(
        body=ItemBody(
            content_type="html",
            content=message
        )
    )
    response = await (
        client
        .teams
        .by_team_id(team_id)
        .channels
        .by_channel_id(channel_id)
        .messages
        .post(chat_message)
    )

    return {
        "message_id": response.id,
        "created_time": response.created_date_time
    }

async def provision_channel_email(team_id: str, channel_id: str):
    client = await graph_client()

    response = await (
        client.teams
        .by_team_id(team_id)
        .channels
        .by_channel_id(channel_id)
        .provision_email
        .post()
    )

    return {
        "channel_id": channel_id,
        "email": response.email
    }

async def get_channel_messages(team_id: str, channel_id: str):
    client = await graph_client()

    response = await (
        client.teams
        .by_team_id(team_id)
        .channels
        .by_channel_id(channel_id)
        .messages
        .get()
    )

    return [
        {
            "message_id": msg.id,
            "user": msg.from_.user.display_name if msg.from_ else None,
            "message": msg.body.content if msg.body else None,
            "created_time": msg.created_date_time
        }
        for msg in response.value
    ]
