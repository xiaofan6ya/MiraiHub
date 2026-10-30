"""Persisted manual order is shared by the dashboard and the real selector."""
from admin.pool import expiry_key


def manual_priority(account):
    value = getattr(account, 'selection_priority', None)
    return value if value is not None else float('inf')


def ordered_accounts(accounts):
    return sorted(accounts, key=lambda a: (manual_priority(a), expiry_key(a),
                                          -int(a.balance_remain or 0), a.id))