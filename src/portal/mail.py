"""An email backend that keeps mail in the database (OutboundEmail) instead
of sending it. The portal runs with the network off, so there is no SMTP
server to hand mail to; site admins read it in the admin and pass links on.
Set DJANGO_EMAIL_BACKEND to Django's SMTP backend to really send."""
from django.core.mail.backends.base import BaseEmailBackend

from .models import OutboundEmail


class OutboxBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        for message in email_messages:
            OutboundEmail.objects.create(to=", ".join(message.recipients()), subject=message.subject,
                                         body=message.body)
        return len(email_messages)
