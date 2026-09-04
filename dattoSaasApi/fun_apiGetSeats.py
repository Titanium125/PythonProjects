import requests

BASE_URL = "https://api.datto.com/v1/saas"


def get_seats(public_key, secret_key, saas_customer_id):
    """
    Fetches every seat (protected item - user, shared mailbox, site, team,
    team site, or shared drive) under the given SaaS customer.

    The live API returns the full list in one response (no pagination
    params are honored), so this makes a single request.

    Each seat dict looks like:
        {
            "mainId": "user@domain.com",
            "name": "Some User",
            "seatType": "User",
            "seatState": "Active",   # Active, Unprotected, Paused, Archived
            "billable": "1",
            "dateAdded": "2024-09-06T20:53:17+00:00",
            "remoteId": "100046291322292424174"
        }
    """
    response = requests.get(
        f"{BASE_URL}/{saas_customer_id}/seats",
        auth=(public_key, secret_key)
    )
    response.raise_for_status()

    return response.json()
