"""Microsoft sign-in that keeps the ID token's groups claim.

allauth's Microsoft provider stores the Graph /me profile as extra_data, which has no
groups. The groups claim (Entra group object ids; the client's admin enables it in the
app registration's token configuration) is in the ID token of the token response. This
adapter copies it into extra_data["groups"], where users/adapters.py reads it.

The ID token is read without checking its signature: it comes straight from the token
endpoint over TLS in the back channel of the authorization-code flow (OpenID Connect
Core 3.1.3.7).
"""

from __future__ import annotations

import base64
import binascii
import json

from allauth.socialaccount.providers.microsoft.views import MicrosoftGraphOAuth2Adapter
from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView


def id_token_claims(id_token: str | None) -> dict:
    """The claims of a JWT's payload, or {} when it is missing or malformed."""
    if not id_token or not isinstance(id_token, str):
        return {}
    parts = id_token.split(".")
    if len(parts) < 2:  # noqa: PLR2004
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


class EntraOAuth2Adapter(MicrosoftGraphOAuth2Adapter):
    def complete_login(self, request, app, token, **kwargs):
        login = super().complete_login(request, app, token, **kwargs)
        response = kwargs.get("response") or {}
        claims = id_token_claims(response.get("id_token"))
        groups = claims.get("groups")
        if isinstance(groups, list):
            login.account.extra_data["groups"] = [
                group for group in groups if isinstance(group, str)
            ]
        return login


oauth2_callback = OAuth2CallbackView.adapter_view(EntraOAuth2Adapter)
