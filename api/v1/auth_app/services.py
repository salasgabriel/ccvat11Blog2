from datetime import date
from typing import TYPE_CHECKING, Literal, NamedTuple
from urllib.parse import urlencode, urljoin

import requests
from dj_rest_auth.jwt_auth import set_jwt_refresh_cookie
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from main.decorators import except_shell
from main.tasks import send_information_email

if TYPE_CHECKING:
    from main.models import UserType


User: 'UserType' = get_user_model()


class CreateUserData(NamedTuple):
    first_name: str
    last_name: str
    email: str
    password_1: str
    password_2: str


class BaseEmailHandler:
    FRONTEND_PATH = ''
    FRONTEND_URL = settings.FRONTEND_URL
    TEMPLATE_NAME = ''

    def __init__(self, user: User, language: str = 'en'):
        self.user = user
        self._locale: str = language if not language else get_language()

    @property
    def locale(self) -> str:
        return self._locale


class Confirmation(BaseEmailHandler):
    FRONTEND_PATH = '/confirm/'
    TEMPLATE_NAME = 'emails/verify_email.html'

    def _get_activate_url(self) -> str:
        url = urljoin(self.FRONTEND_URL, self.FRONTEND_PATH)
        query_params: str = urlencode(
            {
                'key': self.user.confirmation_key,
            },
            safe=':+',
        )
        return f'{url}?{query_params}'

    def send_confirmation_email(self):
        kwargs = {
            'subject': _('Register confirmation email'),
            'template_name': self.TEMPLATE_NAME,
            'to_email': self.user.email,
            'letter_language': self.locale,
            'context': {
                'user': self.user.full_name,
                'activate_url': self._get_activate_url(),
            },
        }
        send_information_email.apply_async(kwargs=kwargs)


class AuthAppService:
    @staticmethod
    def is_user_exist(email: str) -> bool:
        return User.objects.filter(email=email).exists()

    @staticmethod
    @except_shell((User.DoesNotExist,))
    def get_user(email: str) -> User:
        return User.objects.get(email=email)

    @transaction.atomic()
    def create_user(self, validated_data: dict):
        data = CreateUserData(**validated_data)
        print(f'{data=}')
        return User


def full_logout(request):
    response = Response({"detail": _("Successfully logged out.")}, status=status.HTTP_200_OK)
    if cookie_name := getattr(settings, 'JWT_AUTH_COOKIE', None):
        response.delete_cookie(cookie_name)
    refresh_cookie_name = getattr(settings, 'JWT_AUTH_REFRESH_COOKIE', None)
    refresh_token = request.COOKIES.get(refresh_cookie_name)
    if refresh_cookie_name:
        response.delete_cookie(refresh_cookie_name)
    if 'rest_framework_simplejwt.token_blacklist' in settings.INSTALLED_APPS:
        # add refresh token to blacklist
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except KeyError:
            response.data = {"detail": _("Refresh token was not included in request data.")}
            response.status_code = status.HTTP_401_UNAUTHORIZED
        except (TokenError, AttributeError, TypeError) as error:
            if hasattr(error, 'args'):
                if 'Token is blacklisted' in error.args or 'Token is invalid or expired' in error.args:
                    response.data = {"detail": _(error.args[0])}
                    response.status_code = status.HTTP_401_UNAUTHORIZED
                else:
                    response.data = {"detail": _("An error has occurred.")}
                    response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

            else:
                response.data = {"detail": _("An error has occurred.")}
                response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    else:
        message = _(
            "Neither cookies or blacklist are enabled, so the token "
            "has not been deleted server side. Please make sure the token is deleted client side."
        )
        response.data = {"detail": message}
        response.status_code = status.HTTP_200_OK
    return response
