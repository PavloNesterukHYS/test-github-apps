from bot.state import StateStore


def test_idempotency_roundtrip(tmp_path):
    store = StateStore(str(tmp_path / "state.json"))
    assert store.already_processed("acme/widgets", 1, "headA") is False
    store.mark_processed("acme/widgets", 1, "headA")
    assert store.already_processed("acme/widgets", 1, "headA") is True
    # A new head on the same PR is not yet processed.
    assert store.already_processed("acme/widgets", 1, "headB") is False
