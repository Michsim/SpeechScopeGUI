"""Karty protokolů s přepínačem úlohy."""

from __future__ import annotations

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from speechscope_app.backend.protocol import Protocol
from speechscope_app.ui.widgets.protocol_list import ProtocolCardInfo, ProtocolList

PROTOCOLS = [
    Protocol(name="Fonace", task="phonation", builtin=True),
    Protocol(name="Pohádka A", task="story", description="jen akustika", builtin=True),
    Protocol(name="Pohádka B", task="story", builtin=True),
    Protocol(name="Moje pohádka", task="story"),
]
INFOS = {
    "Pohádka A": ProtocolCardInfo(["segments"], "desítky sekund na nahrávku"),
    "Moje pohádka": ProtocolCardInfo([], "naposledy 3 s na nahrávku"),
}


def test_tasks_and_selection(qtbot: QtBot) -> None:
    widget = ProtocolList()
    qtbot.addWidget(widget)
    seen: list[str] = []
    widget.current_changed.connect(lambda p: seen.append(p.name if p else ""))
    widget.set_protocols(PROTOCOLS, INFOS, current="Pohádka B")
    assert widget.task_buttons["story"].isChecked()
    assert widget.task_buttons["ddk"].isHidden()
    assert widget.current_name() == "Pohádka B"
    assert widget.list.count() == 3
    assert seen[-1] == "Pohádka B"

    assert widget.select("Fonace")  # jiná úloha: přepne přepínač
    assert widget.task_buttons["phonation"].isChecked() and widget.list.count() == 1
    assert widget.current_name() == "Fonace"
    assert not widget.select("neexistuje")

    widget._task_clicked("story")
    assert widget.current_name() == "Pohádka A"  # první k úloze
    card = widget._cards["Moje pohádka"]
    assert card.edit_btn.isHidden()  # základní režim bez Upravit…
    widget.set_advanced(True)
    assert not card.edit_btn.isHidden()
    asked: list[str] = []
    widget.edit_requested.connect(lambda p: asked.append(p.name))
    card.edit_btn.click()
    assert asked == ["Moje pohádka"]


def test_empty_list(qtbot: QtBot) -> None:
    widget = ProtocolList()
    qtbot.addWidget(widget)
    widget.set_protocols([], {})
    assert widget.current() is None and all(b.isHidden() for b in widget.task_buttons.values())


def test_card_link_and_double_click_request_details(qtbot: QtBot) -> None:
    widget = ProtocolList()
    qtbot.addWidget(widget)
    widget.set_protocols(PROTOCOLS, INFOS, current="Pohádka A")
    asked: list[str] = []
    widget.details_requested.connect(lambda p: asked.append(p.name))
    widget._cards["Pohádka A"].details.linkActivated.emit("#")
    assert asked == ["Pohádka A"]
    widget.list.itemDoubleClicked.emit(widget.list.item(1))
    assert asked[-1] == widget.list.item(1).data(Qt.ItemDataRole.UserRole).name
