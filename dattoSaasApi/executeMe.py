import json
import os
import sys

from dotenv import load_dotenv

from fun_apiDisableSeatProtection import disable_many_seats
from fun_apiGetDomainAttributes import get_matching_domains
from fun_apiGetDomainInformation import get_domains
from fun_apiGetSeats import get_seats

PAGE_SIZE = 20
DOMAINS_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "domains.json")

# Only User seats are in scope for now - everything else (shared mailboxes,
# sites, teams, shared drives) is fetched but filtered out before the picker
# ever sees it. Active and Paused are both pulled in scope so the "purge all
# Paused" shortcut still has Paused seats to act on, but the picker menu
# itself only ever lists Active seats - see visible_seats in main().
#
# The picker menu (numbers/all/file) only Pauses seats - a resumable hold,
# nothing is purged. Actually disabling protection (Unlicense, which purges
# backup data after 30 days) is only ever done via the (x) shortcut on
# seats that are already Paused - see choose_seats().
SCOPE_SEAT_TYPE = "User"
SCOPE_SEAT_STATES = {"Active", "Paused"}
MENU_SEAT_STATE = "Active"


def load_credentials():
    load_dotenv()
    try:
        return os.environ["DATTO_PUBLIC_KEY"], os.environ["DATTO_SECRET_KEY"]
    except KeyError as missing:
        sys.exit(f"Missing {missing} in .env - see .env for the expected keys.")


def load_domains(public_key, secret_key):
    """Pulls the current domain/customer list from the API so lookups
    reflect what's live right now. Falls back to the cached domains.json
    if the API call fails for some reason."""
    try:
        data = get_domains(public_key, secret_key)
        if data:
            return data
    except Exception as exc:
        print(f"Warning: could not refresh domains from the API ({exc}).")

    print(f"Falling back to cached {DOMAINS_CACHE}")
    with open(DOMAINS_CACHE, "r") as f:
        return json.load(f)


def choose_domain_record(domains_data):
    """Prompts for a client domain and resolves it to exactly one
    domains.json record (customer id, subscription id, product type)."""
    while True:
        domain_input = input("\nEnter the client domain (e.g. acme.com): ").strip()
        if not domain_input:
            continue

        matches = get_matching_domains(domain_input, domains_data)
        if not matches:
            print(f"No domain records found matching '{domain_input}'. Try again.")
            continue

        if len(matches) == 1:
            return matches[0]

        print(f"\nMultiple records matched '{domain_input}':")
        for i, m in enumerate(matches, 1):
            print(f"  {i}. {m['saasCustomerName']}  [{m['productType']}]  "
                  f"customerId={m['saasCustomerId']}  sub={m['externalSubscriptionId']}")
        choice = input(f"Select a record (1-{len(matches)}): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(matches):
            return matches[int(choice) - 1]
        print("Invalid selection, try again.")


def filter_seats(seats):
    term = input("\nFilter seats by name/email (Enter to list all): ").strip().lower()
    if not term:
        return seats
    return [
        s for s in seats
        if term in (s.get("name") or "").lower() or term in (s.get("mainId") or "").lower()
    ]


def parse_number_list(raw, max_n):
    """Parses '5' / '1,3,7' / '5-800' / '1,3,5-9' into a sorted list of
    unique seat numbers within [1, max_n]. Returns [] if nothing valid
    was found."""
    result = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, _, hi_s = part.partition("-")
            lo_s, hi_s = lo_s.strip(), hi_s.strip()
            if lo_s.isdigit() and hi_s.isdigit():
                lo, hi = int(lo_s), int(hi_s)
                if lo > hi:
                    lo, hi = hi, lo
                result.update(n for n in range(lo, hi + 1) if 1 <= n <= max_n)
        elif part.isdigit():
            n = int(part)
            if 1 <= n <= max_n:
                result.add(n)
    return sorted(result)


def match_seats_from_file(path, seats):
    """Reads one identifier per line (email/mainId, display name, or
    remoteId - plain text or CSV, first column used) and matches each
    against the full seat list. Returns (matched_seats, unmatched_lines)."""
    with open(path, "r", encoding="utf-8-sig") as f:
        raw_lines = [line.strip() for line in f]

    wanted = []
    for line in raw_lines:
        if not line:
            continue
        first_field = line.split(",")[0].strip().strip('"').strip("'")
        if first_field:
            wanted.append(first_field)

    lookup = {}
    for s in seats:
        for key in (s.get("mainId"), s.get("name"), s.get("remoteId")):
            if key:
                lookup.setdefault(key.strip().lower(), s)

    matched, unmatched = [], []
    seen_ids = set()
    for w in wanted:
        seat = lookup.get(w.lower())
        if seat is None:
            unmatched.append(w)
        elif seat["remoteId"] not in seen_ids:
            matched.append(seat)
            seen_ids.add(seat["remoteId"])

    return matched, unmatched


def choose_seats(working_set, all_seats, disabled_this_session, start_page=0):
    """Interactive picker over working_set (the current, possibly filtered,
    list). Returns a (result, page, action_type) tuple, where result is a
    non-empty list of seats to act on, None to quit, or the string 'filter'
    to re-filter (action_type is meaningless in those last two cases).
    Every path through the picker (numbers/all/file) only Pauses seats -
    a resumable hold. The (x) shortcut is the only one that actually
    disables protection (Unlicense), and only on seats already Paused.
    The returned page lets the caller resume on the same page next time
    (e.g. after acting on a batch and choosing to act on more)."""
    page = start_page
    while True:
        # Clamp in case the working set shrank since start_page was set.
        max_page = max(0, (len(working_set) - 1) // PAGE_SIZE) if working_set else 0
        page = min(page, max_page)

        start = page * PAGE_SIZE
        end = start + PAGE_SIZE
        chunk = working_set[start:end]

        print(f"\n--- Seats {start + 1}-{min(end, len(working_set))} of {len(working_set)} ---")
        for i, s in enumerate(chunk, start + 1):
            marker = "  [modified this session]" if s["remoteId"] in disabled_this_session else ""
            label = s.get("name") or s.get("mainId")
            print(f"  {i:>4}. [{s['seatState']:<11}] {s['seatType']:<13} {label}{marker}")

        paused_count = sum(1 for s in all_seats if s["seatState"] == "Paused")

        print("\nSeat number(s): single (5), list (1,3,7), or range (5-800) - Pauses them")
        print("(a)ll seats currently listed, (l)oad list from file, (n)ext page, (p)rev page, (f)ilter again, (q)uit")
        print(f"(x) PURGE (Unlicense) ALL Paused seats at once ({paused_count} found)")
        choice = input("> ").strip()
        low = choice.lower()

        if low == "q":
            return None, page, None
        if low == "f":
            return "filter", page, None
        if low == "x":
            paused = [s for s in all_seats if s["seatState"] == "Paused"]
            if not paused:
                print("No seats are currently Paused.")
                continue
            return paused, page, "Unlicense"
        if low == "n":
            if end < len(working_set):
                page += 1
            else:
                print("Already on the last page.")
            continue
        if low == "p":
            if page > 0:
                page -= 1
            else:
                print("Already on the first page.")
            continue
        if low == "a":
            return list(working_set), page, "Pause"
        if low == "l":
            path = input("Path to file (plain text or CSV, one email/name/remoteId per line): ").strip().strip('"')
            try:
                matched, unmatched = match_seats_from_file(path, all_seats)
            except OSError as exc:
                print(f"Couldn't read that file: {exc}")
                continue
            if unmatched:
                print(f"\n{len(unmatched)} line(s) didn't match any seat:")
                for u in unmatched[:20]:
                    print(f"  - {u}")
                if len(unmatched) > 20:
                    print(f"  ... and {len(unmatched) - 20} more")
            if not matched:
                print("Nothing in that file matched a seat. Try again.")
                continue
            print(f"Matched {len(matched)} seat(s) from the file.")
            return matched, page, "Pause"

        numbers = parse_number_list(choice, len(working_set))
        if not numbers:
            print("Didn't understand that, try again.")
            continue
        return [working_set[n - 1] for n in numbers], page, "Pause"


def confirm_and_apply(public_key, secret_key, record, seats, action_type):
    """Shows a summary, requires explicit confirmation scaled to the size
    of the action, then applies action_type ("Pause" or "Unlicense") to
    every seat given (batched automatically). Returns the set of remoteIds
    that succeeded."""
    counts = {}
    for s in seats:
        counts[s["seatType"]] = counts.get(s["seatType"], 0) + 1

    if action_type == "Pause":
        verb = "Pause"
        print(f"\nAbout to Pause {len(seats)} seat(s):")
        print("  Backups pause immediately. The seat stays protected/billed and its")
        print("  existing backup data is kept - resumable via License at any time.")
    else:
        verb = "PURGE (Unlicense)"
        print(f"\nAbout to {verb} {len(seats)} seat(s):")
        print("  Backups stop immediately and existing backup data is purged after a")
        print("  30-day grace period. This is not the same as a temporary Pause.")

    for seat_type, count in sorted(counts.items()):
        print(f"  {count:>5}  {seat_type}")

    preview = ", ".join((s.get("name") or s.get("mainId")) for s in seats[:5])
    if len(seats) > 5:
        preview += f", ... and {len(seats) - 5} more"
    print(f"  e.g. {preview}")

    if len(seats) == 1:
        confirm = input(f"Type 'yes' to {verb} this seat: ").strip().lower()
        ok = confirm == "yes"
    else:
        confirm = input(f"Type {len(seats)} to confirm {verb} on ALL {len(seats)} seats: ").strip()
        ok = confirm == str(len(seats))

    if not ok:
        print("Skipped - nothing changed.")
        return set()

    def report(seat_type, batch, error, round_num):
        prefix = f"round {round_num}: " if round_num > 1 else ""
        if error:
            print(f"  FAILED  {prefix}{seat_type:<13} batch of {len(batch)}: {error}")
        else:
            print(f"  OK      {prefix}{seat_type:<13} batch of {len(batch)} submitted")

    def report_wait(round_num, seconds):
        print(f"  Waiting {seconds}s to verify round {round_num}...")

    def report_verify(round_num, still_remaining, max_rounds):
        if not still_remaining:
            print(f"  After round {round_num}: all seats verified.")
        elif round_num < max_rounds:
            print(f"  After round {round_num}: {len(still_remaining)} seat(s) not yet "
                  f"applied - retrying.")
        else:
            print(f"  After round {round_num}: {len(still_remaining)} seat(s) still not "
                  f"applied - giving up after {max_rounds} rounds.")

    succeeded, failed = disable_many_seats(
        public_key, secret_key, record, seats, action_type=action_type,
        on_batch_done=report, on_wait=report_wait, on_verify_round=report_verify,
    )

    print(f"\nDone: {len(succeeded)} succeeded (verified), {len(failed)} failed.")
    if failed:
        print("Failed seats:")
        for s in failed[:20]:
            print(f"  - {s.get('name') or s.get('mainId')}")
        if len(failed) > 20:
            print(f"  ... and {len(failed) - 20} more")

    return {s["remoteId"] for s in succeeded}


def main():
    public_key, secret_key = load_credentials()
    domains_data = load_domains(public_key, secret_key)
    record = choose_domain_record(domains_data)

    print(f"\nUsing: {record['saasCustomerName']}  [{record['productType']}]")
    print(f"  customerId     = {record['saasCustomerId']}")
    print(f"  subscriptionId = {record['externalSubscriptionId']}")

    all_seats = get_seats(public_key, secret_key, record["saasCustomerId"])
    if not all_seats:
        print("No seats found for this customer.")
        return

    # Scoped to Active/Paused Users for now - see SCOPE_SEAT_TYPE/SCOPE_SEAT_STATES.
    # Paused seats stay in scope only to feed the "disable all Paused" shortcut;
    # the picker menu itself only ever lists Active seats (visible_seats below).
    scoped_seats = [s for s in all_seats if s["seatType"] == SCOPE_SEAT_TYPE and s["seatState"] in SCOPE_SEAT_STATES]
    visible_seats = [s for s in scoped_seats if s["seatState"] == MENU_SEAT_STATE]
    print(f"\n{len(visible_seats)} of {len(all_seats)} seats are {MENU_SEAT_STATE} {SCOPE_SEAT_TYPE} seats - that's what's shown below.")
    if not visible_seats:
        print(f"No {MENU_SEAT_STATE} {SCOPE_SEAT_TYPE} seats found for this customer.")
        return

    visible_seats.sort(key=lambda s: (s.get("name") or s.get("mainId") or "").lower())

    disabled_this_session = set()
    working_set = filter_seats(visible_seats)
    page = 0

    while True:
        if not working_set:
            print("No seats match that filter.")
            working_set = filter_seats(visible_seats)
            page = 0
            continue

        selection, page, action_type = choose_seats(working_set, scoped_seats, disabled_this_session, start_page=page)
        if selection is None:
            print("Done.")
            break
        if selection == "filter":
            working_set = filter_seats(visible_seats)
            page = 0
            continue

        newly_applied = confirm_and_apply(public_key, secret_key, record, selection, action_type)
        disabled_this_session |= newly_applied

        again = input("\nAct on more seats? (y/n): ").strip().lower()
        if again != "y":
            break


if __name__ == "__main__":
    main()
