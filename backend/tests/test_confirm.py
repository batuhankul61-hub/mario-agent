import pytest

from app.confirm import PendingActions, is_affirmative


@pytest.mark.parametrize(
    "said",
    [["evet"], ["Evet, kapat."], ["EVET EMİNİM"], ["tamam yap"], ["Onaylıyorum"], ["yes please"]],
)
def test_clear_yes(said):
    assert is_affirmative(said)


@pytest.mark.parametrize(
    "said",
    [
        [],
        [""],
        ["hayır"],
        ["tamam yapma"],
        ["emin değilim"],
        ["evet, yok hayır vazgeçtim"],
        ["bir saniye bekle"],
        ["bilgisayarı kapat"],  # the original request is not an answer
        ["evetlemek"],  # whole words only
    ],
)
def test_anything_else_is_not_a_yes(said):
    assert not is_affirmative(said)


def test_pending_expires():
    actions = PendingActions(ttl_seconds=0)
    item = actions.add("shutdown_computer", {"mode": "shutdown"}, "?")
    assert actions.get(item.id) is None


def test_pending_pop_is_single_use():
    actions = PendingActions(ttl_seconds=60)
    item = actions.add("shutdown_computer", {}, "?")
    assert actions.pop(item.id) is item
    assert actions.pop(item.id) is None
