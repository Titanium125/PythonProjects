import time

import requests

from fun_apiGetSeats import get_seats

BASE_URL = "https://api.datto.com/v1/saas"

# Confirmed against the live API's validation error message - this is the
# full set the service accepts, case-sensitive, across both Office365 and
# GoogleApps products.
VALID_SEAT_TYPES = {"User", "SharedMailbox", "Site", "TeamSite", "SharedDrive", "Team"}

# License = protect/add. Unlicense = actually disable protection - backups
# stop and existing backup data is purged after a 30-day grace period. Pause
# is NOT the same thing: it's a temporary hold - the seat stays protected/
# billed and its backup data is kept, resumable via License at any time.
# This module's job is to disable protection, so it uses Unlicense.
VALID_ACTION_TYPES = {"License", "Unlicense", "Pause"}

# Datto's documented recommendation for bulkSeatChange.
MAX_IDS_PER_CALL = 100

# Observed against the live API: bulkSeatChange can return a plain 200 OK
# for a whole request without every id in it actually flipping state
# server-side (a 300+ seat submit reported full success but only ~50-100
# actually took effect). There's no useful signal in the PUT response
# itself to catch this, so the only reliable check is to re-fetch the seat
# list afterward and see what actually changed, then resubmit whatever
# didn't. This is the seatState each action_type lands on once it has
# actually taken effect.
ACTION_TARGET_STATE = {
    "Pause": "Paused",
    "Unlicense": "Unprotected",
    "License": "Active",
}
VERIFY_SLEEP_SECONDS = 20


def verify_rounds_for(seat_count):
    """How many verify/resubmit rounds to run for a batch of this size.

    Bulk submissions land in batches of MAX_IDS_PER_CALL server-side, and
    larger jobs need more passes for the eventual consistency in
    bulkSeatChange to catch up across all of them. Scale with the size of
    the job: one round per 100 seats (rounded up), e.g. 650 seats -> 7
    rounds, 2001 seats -> 21 rounds.
    """
    return seat_count // 100 + 1


def set_seat_protection(public_key, secret_key, saas_customer_id, external_subscription_id,
                         seat_type, remote_ids, action_type="Unlicense"):
    """
    Changes protection status for up to MAX_IDS_PER_CALL seats of a single
    seat_type via the bulkSeatChange endpoint (the API requires every id in
    one call to share the same seat_type). Defaults to action_type="Unlicense",
    i.e. actually disabling protection (backups stop and data is purged after
    30 days) - not to be confused with action_type="Pause", which just puts
    backups on a resumable hold while the seat stays protected.

    remote_ids: a list of seat remoteId values (pass a single-item list to
    act on just one seat).
    """
    if seat_type not in VALID_SEAT_TYPES:
        raise ValueError(f"Invalid seat_type '{seat_type}'. Must be one of {sorted(VALID_SEAT_TYPES)}")
    if action_type not in VALID_ACTION_TYPES:
        raise ValueError(f"Invalid action_type '{action_type}'. Must be one of {sorted(VALID_ACTION_TYPES)}")
    if len(remote_ids) > MAX_IDS_PER_CALL:
        raise ValueError(
            f"Datto recommends at most {MAX_IDS_PER_CALL} ids per call; got {len(remote_ids)}. "
            "Use disable_many_seats() to batch automatically."
        )

    payload = {
        "seat_type": seat_type,
        "action_type": action_type,
        "ids": list(remote_ids),
    }

    response = requests.put(
        f"{BASE_URL}/{saas_customer_id}/{external_subscription_id}/bulkSeatChange",
        auth=(public_key, secret_key),
        json=payload,
    )
    response.raise_for_status()

    if response.text:
        try:
            return response.json()
        except ValueError:
            return response.text
    return None


def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def disable_many_seats(public_key, secret_key, record, seats, action_type="Unlicense",
                        on_batch_done=None, on_wait=None, on_verify_round=None):
    """
    Applies action_type ("Pause" or "Unlicense" - License is valid too but
    unused by this tool's callers) to many seats at once. Groups seats by
    seat_type (the API requires one seat_type per call) and splits each
    group into batches of at most MAX_IDS_PER_CALL.

    Pause is a temporary, resumable hold - the seat stays protected/billed
    and its backup data is kept. Unlicense actually disables protection -
    backups stop and existing backup data is purged after a 30-day grace
    period. Callers pick which one they want; this function doesn't assume.

    Because bulkSeatChange can report success without every seat actually
    taking effect (see ACTION_TARGET_STATE comment above), after submitting
    a round of batches this waits VERIFY_SLEEP_SECONDS, re-fetches the
    customer's seats, and resubmits whatever hasn't actually landed on
    ACTION_TARGET_STATE[action_type] yet. Repeats up to
    verify_rounds_for(len(seats)) rounds total, then gives up on whatever
    is still left.

    on_batch_done(seat_type, batch_seats, error_or_none, round_num), if
    given, is called after every batch submission.
    on_wait(round_num, seconds), if given, is called right before each
    verification sleep.
    on_verify_round(round_num, still_remaining_seats, max_rounds), if
    given, is called after each verification check, before deciding
    whether to retry.

    Returns (succeeded_seats, failed_seats) - both lists of seat dicts.
    succeeded/failed reflect verified seatState, not just the initial API
    response.
    """
    target_state = ACTION_TARGET_STATE[action_type]
    remaining = {s["remoteId"]: s for s in seats}
    succeeded = []
    max_rounds = verify_rounds_for(len(seats))

    for round_num in range(1, max_rounds + 1):
        if not remaining:
            break

        by_type = {}
        for s in remaining.values():
            by_type.setdefault(s["seatType"], []).append(s)

        for seat_type, group in by_type.items():
            for batch in _chunked(group, MAX_IDS_PER_CALL):
                ids = [s["remoteId"] for s in batch]
                error = None
                try:
                    set_seat_protection(
                        public_key, secret_key,
                        record["saasCustomerId"], record["externalSubscriptionId"],
                        seat_type, ids, action_type=action_type,
                    )
                except Exception as exc:
                    error = exc
                if on_batch_done:
                    on_batch_done(seat_type, batch, error, round_num)

        if on_wait:
            on_wait(round_num, VERIFY_SLEEP_SECONDS)
        time.sleep(VERIFY_SLEEP_SECONDS)

        try:
            fresh_seats = get_seats(public_key, secret_key, record["saasCustomerId"])
            fresh_by_id = {s["remoteId"]: s for s in fresh_seats}
        except Exception:
            # Couldn't verify this round - leave everything in remaining so
            # it gets resubmitted (and re-checked) next round.
            fresh_by_id = {}

        still_remaining = {}
        for rid, s in remaining.items():
            fresh = fresh_by_id.get(rid)
            if fresh is not None and fresh.get("seatState") == target_state:
                succeeded.append(fresh)
            else:
                still_remaining[rid] = s

        if on_verify_round:
            on_verify_round(round_num, list(still_remaining.values()), max_rounds)

        remaining = still_remaining

    failed = list(remaining.values())
    return succeeded, failed
