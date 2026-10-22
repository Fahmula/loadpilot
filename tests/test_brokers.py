import pytest

from loadpilot.brokers import Broker, apply_known_broker, broker_key, known_brokers


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("TQL", "tql"),
        ("TQL, Inc.", "tql"),
        ("C.H. Robinson", "chrobinson"),
        ("CH Robinson Company", "chrobinson"),
        ("Echo Global Logistics LLC", "echogloballogistics"),
        ("", ""),
        (None, ""),
    ],
)
def test_broker_key(name, key):
    assert broker_key(name) == key


def test_known_brokers_keep_latest_spelling_and_email():
    contacts = [
        ("TQL, Inc.", None),  # newest load, no email on it
        ("TQL", "loads@tql.example"),
        ("TQL", "old@tql.example"),
        ("Echo", "ops@echo.example"),
        ("", None),
    ]

    brokers = known_brokers(contacts)

    assert brokers == {
        "tql": Broker("TQL, Inc.", "loads@tql.example"),
        "echo": Broker("Echo", "ops@echo.example"),
    }


def test_scanned_broker_matches_history():
    brokers = {"chrobinson": Broker("C.H. Robinson", "carriers@chr.example")}

    matched = apply_known_broker({"broker_name": "CH Robinson Company", "broker_email": ""}, brokers)
    own_email = apply_known_broker({"broker_name": "C.H. Robinson", "broker_email": "new@chr.example"}, brokers)
    unknown = {"broker_name": "Arrive Logistics", "broker_email": ""}

    assert matched == {"broker_name": "C.H. Robinson", "broker_email": "carriers@chr.example"}
    assert own_email["broker_email"] == "new@chr.example"  # the rate con's address wins
    assert apply_known_broker(unknown, brokers) == unknown
