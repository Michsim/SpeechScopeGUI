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

    begin = contract.parse_event('{"event":"begin","index":0,"path":"x.wav"}')
    assert begin == contract.BeginEvent(index=0, path="x.wav")
    running = contract.parse_event(
        '{"event":"stage","index":0,"provider":"transcript","status":"running"}'
    )
    assert isinstance(running, contract.StageEvent) and running.running
    assert running.seconds is None
    done = contract.parse_event(
        '{"event":"stage","index":0,"provider":"transcript","status":"cached","seconds":0.5}'
    )
    assert isinstance(done, contract.StageEvent) and done.seconds == 0.5 and not done.running
    failed = contract.parse_event(
        '{"event":"stage","index":1,"provider":"nlp","status":"error","seconds":1,"msg":"x"}'
    )
    assert isinstance(failed, contract.StageEvent) and failed.msg == "x"


def test_protocol_versions() -> None:
    v2 = contract.parse_event(
        '{"event":"start","protocol":2,"total":1,"features":[],"providers":[]}'
    )
    assert isinstance(v2, contract.StartEvent) and v2.protocol == 2
    # stará knihovna bez begin/stage se stále přijme
    v1 = contract.parse_event(
        '{"event":"start","protocol":1,"total":1,"features":[],"providers":[]}'
    )
    assert isinstance(v1, contract.StartEvent) and v1.protocol == 1
    legacy = contract.parse_event('{"event":"start","total":1,"features":[],"providers":[]}')
    assert isinstance(legacy, contract.StartEvent) and legacy.protocol == 1


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        '{"total": 1}',
        '{"event":"start","protocol":99,"total":1,"features":[],"providers":[]}',
        '{"event":"file","index":"x"}',
        '{"event":"stage","index":0,"provider":"nlp","status":"maybe"}',
        '{"event":"stage","index":0,"status":"done"}',
        '{"event":"begin","index":0}',
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


def test_batch_state_tracks_stages() -> None:
    state = contract.BatchState()
    state.apply(
        contract.StartEvent(
            total=2, task="story", features=["f"], providers=["segments", "transcript"]
        )
    )
    assert state.detailed and state.current is None and state.running_stage() is None
    state.apply(contract.BeginEvent(index=0, path="a"))
    assert state.current is not None and state.current.path == "a"
    state.apply(contract.StageEvent(index=0, provider="segments", status="running"))
    assert state.running_stage().provider == "segments"
    state.apply(contract.StageEvent(index=0, provider="segments", status="done", seconds=2.0))
    assert state.running_stage() is None
    state.apply(contract.StageEvent(index=0, provider="transcript", status="running"))
    state.apply(contract.StageEvent(index=0, provider="transcript", status="cached", seconds=0.1))
    state.apply(contract.FileEvent(index=0, path="a", status="ok"))
    assert state.current is None
    assert state.stages[0]["segments"].seconds == 2.0
    assert state.stages[0]["transcript"].status == "cached"

    v1 = contract.BatchState()
    v1.apply(contract.StartEvent(total=1, task=None, features=[], providers=[], protocol=1))
    assert not v1.detailed
