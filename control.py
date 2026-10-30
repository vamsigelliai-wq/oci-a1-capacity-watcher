"""No cloud access: enforce a fixed UTC deadline before starting the watcher."""
import os
import sys
from datetime import datetime, timedelta, timezone


def expired(value, now=None):
    now = now or datetime.now(timezone.utc)
    if not value:
        raise ValueError('Missing OCI_STOP_AT repository variable.')
    deadline = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if deadline.tzinfo is None:
        raise ValueError('OCI_STOP_AT must include a UTC offset.')
    if deadline > now + timedelta(days=7):
        raise ValueError('OCI_STOP_AT must be no more than seven days ahead.')
    return now >= deadline


def window():
    ended = expired(os.environ.get('OCI_STOP_AT', ''))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as f:
            f.write(f'expired={str(ended).lower()}\n')
    print('Provisioning window ended. No OCI requests will be made.' if ended
          else 'Provisioning window is open.')


if __name__ == '__main__':
    try:
        window()
    except Exception:
        print('::error::Missing or invalid provisioning deadline. Set OCI_STOP_AT to a fixed UTC timestamp no more than seven days ahead.')
        sys.exit(1)
