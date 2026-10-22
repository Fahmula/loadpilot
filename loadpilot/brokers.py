"""Brokers learned from past loads, so a returning broker's name and email fill themselves in."""

import re
from dataclasses import dataclass

# Dropped when comparing names, so "TQL, Inc." and "TQL" are the same broker.
# templates/partials/load_form.html repeats this list for matching as the driver types.
SUFFIXES = {"inc", "llc", "co", "corp", "corporation", "company", "ltd"}


@dataclass(frozen=True)
class Broker:
    name: str
    email: str | None


def broker_key(name: str | None) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    while words and words[-1] in SUFFIXES:
        words.pop()
    return "".join(words)


def known_brokers(contacts_newest_first: list[tuple[str, str | None]]) -> dict[str, Broker]:
    """Each broker once, keyed by broker_key: the spelling used most recently and the latest email on file."""
    brokers: dict[str, Broker] = {}
    for name, email in contacts_newest_first:
        key = broker_key(name)
        if not key:
            continue
        known = brokers.get(key)
        if not known:
            brokers[key] = Broker(name, email or None)
        elif not known.email and email:
            brokers[key] = Broker(known.name, email)
    return brokers


def apply_known_broker(data: dict, brokers: dict[str, Broker]) -> dict:
    """Matches scanned rate con data to a broker hauled for before: their usual spelling, and email if missing."""
    known = brokers.get(broker_key(data.get("broker_name")))
    if not known:
        return data
    return {**data, "broker_name": known.name, "broker_email": data.get("broker_email") or known.email or ""}
