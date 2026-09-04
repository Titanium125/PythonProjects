def get_matching_domains(domain, entries):
    """
    Finds every domains.json entry whose 'domain' field matches the given
    client domain (case-insensitive). A domain can legitimately map to more
    than one record (e.g. a re-onboarded tenant), so this always returns a
    list rather than a single result.

    Tries an exact match first; if none is found, falls back to a
    contains-match so a partial/typo'd domain still surfaces candidates.
    Returns a list of entry dicts (each has saasCustomerId,
    externalSubscriptionId, saasCustomerName, productType, etc.) - empty if
    nothing matches.
    """
    needle = domain.strip().lower()
    if not needle:
        return []

    exact = [
        e for e in entries
        if (e.get("domain") or "").lower() == needle
    ]
    if exact:
        return exact

    return [
        e for e in entries
        if needle in (e.get("domain") or "").lower()
    ]
