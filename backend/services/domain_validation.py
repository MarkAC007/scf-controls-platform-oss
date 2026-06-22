"""
Domain Validation Service - Enforces email domain rules for organisation invitations.

Rules:
- Public email domains (gmail, outlook, etc.) cannot send invitations
- Invited users must share the same email domain as the inviting admin
"""
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

PUBLIC_EMAIL_DOMAINS = frozenset({
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "hotmail.co.uk",
    "live.com",
    "live.co.uk",
    "msn.com",
    "yahoo.com",
    "yahoo.co.uk",
    "yahoo.co.in",
    "aol.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "protonmail.com",
    "proton.me",
    "pm.me",
    "tutanota.com",
    "tuta.io",
    "zoho.com",
    "yandex.com",
    "mail.com",
    "gmx.com",
    "gmx.co.uk",
    "fastmail.com",
    "hey.com",
    "qq.com",
    "163.com",
    "126.com",
    "rediffmail.com",
    "btinternet.com",
    "sky.com",
    "virginmedia.com",
    "talktalk.net",
})


def get_email_domain(email: str) -> str:
    """Extract the domain from an email address."""
    return email.strip().lower().rsplit("@", 1)[-1]


def is_public_domain(email: str) -> bool:
    """Check if an email uses a public/free email provider."""
    return get_email_domain(email) in PUBLIC_EMAIL_DOMAINS


def validate_invite_domain(inviter_email: str, invitee_email: str) -> Tuple[bool, str]:
    """
    Validate that an invitation is allowed based on email domain rules.

    Returns:
        Tuple of (is_valid, error_message). error_message is empty when valid.
    """
    inviter_domain = get_email_domain(inviter_email)
    invitee_domain = get_email_domain(invitee_email)

    if inviter_domain in PUBLIC_EMAIL_DOMAINS:
        return (
            False,
            "Organisation must use a corporate email domain to invite team members. "
            "Public email providers (e.g. Gmail, Outlook) are not supported for invitations."
        )

    if invitee_domain != inviter_domain:
        return (
            False,
            f"Invited users must share the same email domain as the organisation ({inviter_domain})."
        )

    return (True, "")
