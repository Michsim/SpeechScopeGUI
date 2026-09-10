from __future__ import annotations

import pytest

from speechscope_app import contract


def test_parse_all_events() -> None:
    start = contract.parse_event(
        '{"event":"start","protocol":1,"total":2,"task":"story","features":["a"],"providers":[]}'
    )
    assert isinstance(start, contract.StartEvent)
    assert start.total == 2 and start.task == "story"

    ok = contract.parse_event('{"event":"file","index":0,"path":"x.wav","status":"ok"}')
    assert isinstance(ok, contract.FileEvent) and ok.ok and ok.msg is None

    err = contract.parse_event(
        '{"event":"file","index":1,"path":"y.wav","status":"error","msg":"nejde"}'
    )
    assert isinstance(err, contract.FileEvent) and not err.ok and err.msg == "nejde"

    assert contract.parse_event('{"event":"done","n_ok":1}') == contract.DoneEvent(n_ok=1)
    assert contract.parse_event('{"event":"saved","out":"o.csv"}') == contract.SavedEvent(
        out="o.csv"
    )
    assert contract.parse_event("   ") is None


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        '{"total": 1}',
        '{"event":"start","protocol":99,"total":1,"features":[],"providers":[]}',
        '{"event":"file","index":"x"}',
        '{"event":"weird"}',
    ],
)
def test_bad_lines_are_contract_errors(line: str) -> None:
    with pytest.raises(contract.ContractError):
        contract.parse_event(line)


def test_batch_state_accumulates() -> None:
    state = contract.BatchState()
    state.apply(contract.StartEvent(total=2, task="story", features=["f"], providers=["segments"]))
    state.apply(contract.FileEvent(index=0, path="a", status="ok"))
    state.apply(contract.FileEvent(index=1, path="b", status="error", msg="x"))
    assert state.processed == 2 and len(state.errors) == 1
    assert not state.finished
    state.apply(contract.DoneEvent(n_ok=1))
    state.apply(contract.SavedEvent(out="out.csv"))
    assert state.finished and state.n_ok == 1 and state.out == "out.csv"
