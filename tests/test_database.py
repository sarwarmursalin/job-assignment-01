from telemetry_gateway.database import TelemetryStore
from telemetry_gateway.models import BootRegistrationInput, TelemetryInput


def telemetry(**overrides) -> TelemetryInput:
    values = {
        "deviceId": "device-01",
        "bootId": "boot-a",
        "sequence": 1,
        "deviceTime": "2026-08-12T09:00:00+00:00",
        "metric": "temperature",
        "value": 21.4,
    }
    values.update(overrides)
    return TelemetryInput.model_validate(values)


def test_registers_a_boot_idempotently() -> None:
    store = TelemetryStore(":memory:")
    try:
        event = BootRegistrationInput(deviceId="device-01", bootId="boot-a")

        first = store.register_boot(event)
        second = store.register_boot(event)

        assert first.to_api() == {
            "deviceId": "device-01",
            "bootId": "boot-a",
            "generation": 1,
            "created": True,
        }
        assert second.to_api() == {
            "deviceId": "device-01",
            "bootId": "boot-a",
            "generation": 1,
            "created": False,
        }
    finally:
        store.close()


def test_stores_a_basic_event_and_calculates_current_state() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))

        result = store.ingest(telemetry(), "2026-08-12T09:00:01+00:00")

        assert result.duplicate is False
        assert result.current_changed is True
        assert store.list_current_states()[0].to_api() == {
            "deviceId": "device-01",
            "bootId": "boot-a",
            "generation": 1,
            "sequence": 1,
            "deviceTime": "2026-08-12T09:00:00+00:00",
            "receivedAt": "2026-08-12T09:00:01+00:00",
            "metric": "temperature",
            "value": 21.4,
        }
        assert len(store.list_events(10)) == 1
    finally:
        store.close()


def test_repeated_event_from_same_boot_is_a_duplicate() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))
        store.ingest(telemetry(), "2026-08-12T09:00:01+00:00")

        duplicate = store.ingest(telemetry(), "2026-08-12T09:00:02+00:00")

        assert duplicate.to_api() == {
            "accepted": True,
            "duplicate": True,
            "currentChanged": False,
        }
        assert len(store.list_events(10)) == 1
    finally:
        store.close()


def test_cross_boot_sequence_reuse_is_not_treated_as_duplicate() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-b"))

        first_boot_event = store.ingest(
            telemetry(bootId="boot-a", sequence=1), "2026-08-12T09:00:01+00:00"
        )
        second_boot_event = store.ingest(
            telemetry(bootId="boot-b", sequence=1), "2026-08-12T09:00:02+00:00"
        )

        assert first_boot_event.duplicate is False
        assert second_boot_event.duplicate is False
        assert len(store.list_events(10)) == 2
    finally:
        store.close()


def test_repeated_event_within_a_restarted_boot_is_still_a_duplicate() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-b"))
        store.ingest(telemetry(bootId="boot-a", sequence=1), "2026-08-12T09:00:01+00:00")
        store.ingest(telemetry(bootId="boot-b", sequence=1), "2026-08-12T09:00:02+00:00")

        duplicate = store.ingest(
            telemetry(bootId="boot-b", sequence=1), "2026-08-12T09:00:03+00:00"
        )

        assert duplicate.duplicate is True
        assert len(store.list_events(10)) == 2
    finally:
        store.close()
