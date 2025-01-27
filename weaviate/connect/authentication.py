from __future__ import annotations

from typing import Dict, List, Union
from typing import TYPE_CHECKING

import requests
from authlib.integrations.requests_client import OAuth2Session

from weaviate.auth import (
    AuthCredentials,
    AuthClientPassword,
    AuthBearerToken,
    AuthClientCredentials,
)
from weaviate.exceptions import MissingScopeException, AuthenticationFailedException
from ..warnings import _Warnings

if TYPE_CHECKING:
    from . import BaseConnection


class _Auth:
    def __init__(
        self,
        oidc_config: Dict[str, Union[str, List[str]]],
        credentials: AuthCredentials,
        connection: BaseConnection,
    ) -> None:
        self._credentials: AuthCredentials = credentials
        self._connection: BaseConnection = connection

        self._open_id_config_url: str = oidc_config["href"]
        self._client_id: str = oidc_config["clientId"]
        self._default_scopes: List[str] = oidc_config["scopes"]

        self._token_endpoint: str = self._get_token_endpoint()
        self._validate(oidc_config)

    def _validate(self, oidc_config: Dict[str, str]) -> None:
        if isinstance(self._credentials, AuthClientPassword):
            if self._token_endpoint.startswith("https://login.microsoftonline.com"):
                raise AuthenticationFailedException(
                    """Microsoft/azure does not recommend to authenticate using username and password and this method is
                    not supported by the python client."""
                )

            # The grant_types_supported field is optional and does not have to be present in the response
            if (
                "grant_types_supported" in oidc_config
                and "password" not in oidc_config["grant_types_supported"]
            ):
                raise AuthenticationFailedException(
                    """The grant_types supported by the third-party authentication service are insufficient. Please add
                    the 'password' grant type."""
                )

    def _get_token_endpoint(self) -> str:
        response_auth = requests.get(self._open_id_config_url, proxies=self._connection.proxies)
        return response_auth.json()["token_endpoint"]

    def get_auth_session(self) -> OAuth2Session:
        if isinstance(self._credentials, AuthBearerToken):
            session = self._get_session_auth_bearer_token(self._credentials)
        elif isinstance(self._credentials, AuthClientCredentials):
            session = self._get_session_client_credential(self._credentials)
        else:
            assert isinstance(self._credentials, AuthClientPassword)
            session = self._get_session_user_pw(self._credentials)

        return session

    def _get_session_auth_bearer_token(self, config: AuthBearerToken) -> OAuth2Session:
        token = {"access_token": config.access_token}
        if config.expires_in is not None:
            token["expires_in"] = config.expires_in
        if config.refresh_token is not None:
            token["refresh_token"] = config.refresh_token

        if "refresh_token" not in token:
            _Warnings.auth_no_refresh_token(config.expires_in)

        # token endpoint and clientId are needed for token refresh
        return OAuth2Session(
            token=token, token_endpoint=self._token_endpoint, client_id=self._client_id
        )

    def _get_session_user_pw(self, config: AuthClientPassword) -> OAuth2Session:
        scope: List[str] = self._default_scopes.copy()
        scope.extend(config.scope)
        session = OAuth2Session(
            client_id=self._client_id,
            token_endpoint=self._token_endpoint,
            grant_type="password",
            scope=scope,
        )
        token = session.fetch_token(username=config.username, password=config.password)
        if "refresh_token" not in token:
            _Warnings.auth_no_refresh_token(token["expires_in"])

        return session

    def _get_session_client_credential(self, config: AuthClientCredentials) -> OAuth2Session:
        scope: List[str] = self._default_scopes.copy()

        # remove openid scopes from the scopes returned by weaviate (these are returned by default). These are not
        # accepted by some providers for client credentials
        scope = list(filter(lambda s: s != "openid" and s != "email", scope))

        if config.scope is not None:
            scope.extend(config.scope)
        if len(scope) == 0:
            # hardcode commonly used scopes
            if self._token_endpoint.startswith("https://login.microsoftonline.com"):
                scope = [self._client_id + "/.default"]
            else:
                raise MissingScopeException

        session = OAuth2Session(
            client_id=self._client_id,
            client_secret=config.client_secret,
            token_endpoint_auth_method="client_secret_post",
            scope=scope,
            token_endpoint=self._token_endpoint,
            grant_type="client_credentials",
            token={"access_token": None, "expires_in": -100},
        )
        # explicitly fetch tokens. Otherwise, authlib will do it in the background and we might have race-conditions
        session.fetch_token()
        return session
