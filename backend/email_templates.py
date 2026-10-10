"""Shared presentation for account and assessment emails."""
from django.template.loader import render_to_string


def render_email(subject, body, *, category='Account notification', code='',
                 action_url='', action_label='', note=''):
    return render_to_string('emails/notification.html', {
        'subject': subject, 'body': body, 'category': category, 'code': code,
        'action_url': action_url, 'action_label': action_label, 'note': note,
    })
