"""
Azure Communication Services Email Domain Management Utility

This module provides functionality to manage email senders in Azure Communication Services.
"""

from azure.mgmt.communication import CommunicationServiceManagementClient
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from app.core.config import settings
import logging
import os

logger = logging.getLogger(__name__)


class AzureCommunicationManager:
    """Manages Azure Communication Services email domain configurations."""
    
    def __init__(self):
        """Initialize the Communication Service Management Client."""
        # Use SP credentials locally, Managed Identity on Azure
        if settings.AZURE_CLIENT_SECRET:
            credential = ClientSecretCredential(
                tenant_id=settings.AZURE_TENANT_ID,
                client_id=settings.AZURE_CLIENT_ID,
                client_secret=settings.AZURE_CLIENT_SECRET,
            )
        else:
            credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        
        self.client = CommunicationServiceManagementClient(
            credential=credential,
            subscription_id=settings.AZURE_SUBSCRIPTION_ID
        )
        
        # Configuration from settings
        self.resource_group = settings.ACS_RESOURCE_GROUP
        self.email_service_name = settings.ACS_EMAIL_SERVICE_NAME  # "MyOpsEmailService"
        
    def get_domain_for_email(self, email: str) -> str:
        """
        Extract domain from email address.
        
        Args:
            email: Email address (e.g., "admin123@834labs.com")
            
        Returns:
            Domain part (e.g., "834labs.com")
        """
        if "@" not in email:
            raise ValueError(f"Invalid email format: {email}")
        return email.split("@")[1].lower()
    
    def get_username_from_email(self, email: str) -> str:
        """
        Extract username part from email address.
        
        Args:
            email: Email address (e.g., "admin123@834labs.com")
            
        Returns:
            Username part (e.g., "admin123")
        """
        if "@" not in email:
            raise ValueError(f"Invalid email format: {email}")
        return email.split("@")[0]
    
    def find_domain_resource(self, domain: str) -> dict:
        """
        Find the Azure Communication Services domain resource matching the given domain.
        
        Args:
            domain: Domain to search for (e.g., "834labs.com")
            
        Returns:
            Dictionary containing domain resource information
            
        Raises:
            ValueError: If domain is not found
        """
        try:
            # List all domains under the email service
            domains = self.client.domains.list_by_email_service_resource(
                resource_group_name=self.resource_group,
                email_service_name=self.email_service_name
            )
            
            for domain_resource in domains:
                # Check if the domain matches
                if domain_resource.mail_from_sender_domain and domain.lower() in domain_resource.mail_from_sender_domain.lower():
                    return {
                        "name": domain_resource.name,
                        "mail_from_domain": domain_resource.mail_from_sender_domain,
                        "resource": domain_resource
                    }
            
            raise ValueError(f"Domain '{domain}' not found in Azure Communication Services")
            
        except Exception as e:
            logger.error(f"Error finding domain resource: {str(e)}")
            raise
    
    def add_sender_username(
        self, 
        domain_name: str,
        username: str, 
        display_name: str
    ) -> dict:
        """
        Add a sender username to a specific domain in Azure Communication Services.
        
        Args:
            domain_name: The domain resource name in Azure
            username: The username part (e.g., "admin123")
            display_name: Display name for the sender (e.g., "John Doe")
            
        Returns:
            Dictionary containing the created sender information
        """
        try:
            # Create sender username
            sender_username_resource = self.client.sender_usernames.create_or_update(
                resource_group_name=self.resource_group,
                email_service_name=self.email_service_name,
                domain_name=domain_name,
                sender_username=username,
                parameters={
                    "username": username,
                    "display_name": display_name
                }
            )
            
            logger.info(f"Successfully added sender: {username} with display name: {display_name}")
            
            return {
                "username": sender_username_resource.username,
                "display_name": sender_username_resource.display_name,
                "data_location": getattr(sender_username_resource, 'data_location', None)
            }
            
        except Exception as e:
            logger.error(f"Error adding sender username: {str(e)}")
            raise
    
    def create_email_sender_from_login(
        self, 
        login_email: str, 
        first_name: str, 
        last_name: str
    ) -> dict:
        """
        Create an email sender in Azure Communication Services based on login email.
        
        This method:
        1. Extracts the domain from the login email
        2. Finds the matching domain in Azure Communication Services
        3. Adds the username with the display name
        
        Args:
            login_email: The login email (e.g., "admin123@834labs.com")
            first_name: User's first name
            last_name: User's last name
            
        Returns:
            Dictionary with sender creation details
            
        Raises:
            ValueError: If domain is not found or invalid email format
        """
        try:
            # Extract domain and username
            domain = self.get_domain_for_email(login_email)
            username = self.get_username_from_email(login_email)
            display_name = f"{first_name} {last_name}".strip()
            
            logger.info(f"Processing email sender creation for: {login_email}")
            logger.info(f"Domain: {domain}, Username: {username}, Display Name: {display_name}")
            
            # Find the domain resource
            domain_info = self.find_domain_resource(domain)
            domain_resource_name = domain_info["name"]
            
            logger.info(f"Found domain resource: {domain_resource_name}")
            
            # Add sender username
            sender_info = self.add_sender_username(
                domain_name=domain_resource_name,
                username=username,
                display_name=display_name
            )
            
            return {
                "success": True,
                "login_email": login_email,
                "domain": domain,
                "domain_resource": domain_resource_name,
                "sender_info": sender_info
            }
            
        except Exception as e:
            logger.error(f"Failed to create email sender: {str(e)}")
            return {
                "success": False,
                "login_email": login_email,
                "error": str(e)
            }


def get_communication_manager() -> AzureCommunicationManager:
    """Dependency function to get Azure Communication Manager instance."""
    return AzureCommunicationManager()
