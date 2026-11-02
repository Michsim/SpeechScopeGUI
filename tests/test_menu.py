"""Nabídky podoby karty: ikony, zkratky, nebezpečné položky a důvod nedostupné položky."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QWidgetAction

from speechscope_app.ui.widgets.menu import DANGER_PROP, ICON_PROP, Menu


def test_reason_shows_only_while_disabled(qtbot) -> None:  # noqa: ANN001
    host = QWidget()
    qtbot.addWidget(host)
    menu = Menu(host)
    calls: list[str] = []
    section = menu.section("vybráno běhů: 2")
    open_action = menu.add("Otevřít", lambda: calls.append("open"), icon="open", key="Enter")
    compare = menu.add("Porovnat…", icon="compare", reason="běhy mají různé úlohy")
    delete = menu.add("Smazat běh…", icon="delete", danger=True)

    assert isinstance(section, QWidgetAction) and not section.isEnabled()
    assert open_action.text() == "Otevřít\tEnter" and open_action.property(ICON_PROP) == "open"
    open_action.trigger()  # slot bez argumentu checked
    assert calls == ["open"]
    assert not compare.isEnabled() and compare.toolTip() == "Běhy mají různé úlohy."
    assert menu.reason_visible(compare)
    # důvod je hned pod položkou
    actions = menu.actions()
    assert isinstance(actions[actions.index(compare) + 1], QWidgetAction)
    assert delete.property(DANGER_PROP) and delete.isEnabled()

    compare.setEnabled(True)  # nabídka si důvod srovná při otevření
    menu.aboutToShow.emit()
    assert not menu.reason_visible(compare)


def test_submenu_and_drawing(qtbot) -> None:  # noqa: ANN001
    host = QWidget()
    qtbot.addWidget(host)
    menu = Menu(host)
    sub = menu.add_menu("Odebrat sloupec", icon="remove")
    sub.add("věk")
    menu.addSeparator()
    menu.add("Konec", icon="exit")
    assert isinstance(sub, Menu) and menu.actions()[0].menu() is sub
    assert not menu.grab().isNull()  # kreslení bez výjimky


def test_file_menu_disabled_for_many(qtbot, tmp_path) -> None:  # noqa: ANN001
    from speechscope_app.ui.file_actions import file_menu

    host = QWidget()
    qtbot.addWidget(host)
    one = file_menu(host, tmp_path / "a.wav")
    many = file_menu(host, tmp_path / "a.wav", single=False)
    assert all(a.isEnabled() for a in one.actions())
    assert not any(a.isEnabled() for a in many.actions())
    assert many.actions()[0].toolTip() == "Jen pro jednu nahrávku."


def test_meta_table_header_menu(qtbot, tmp_path) -> None:  # noqa: ANN001
    from pathlib import Path

    from speechscope_app.ui.widgets.meta_table import COLUMN_PROP, MetaRow, MetaTable

    table = MetaTable(["nahrávka", "délka"], labels=True, right_columns=(1,))
    qtbot.addWidget(table)
    rows = [
        MetaRow(Path(f"/x/{n}.wav"), [f"{n}.wav", "0:40"], {"group": g})
        for n, g in (("a", "PD"), ("b", ""), ("c", ""))
    ]
    table.set_rows(rows, ["group"])
    add = Menu(table, "Přidat sloupec")
    age = add.add("Věk")
    age.setProperty(COLUMN_PROP, "age")
    group = add.add("Skupina")
    group.setProperty(COLUMN_PROP, "group")
    grouped: list[str] = []
    table.set_add_menu(add)
    table.group_column = grouped.append
    table.show()
    assert table.add_btn.isVisible()

    fixed = table.header_menu(0)  # pevný sloupec: Přidat sloupec ›, Odebrat šedě s důvodem
    assert fixed is not None
    assert any(a.menu() is add for a in fixed.actions())
    remove = [a for a in fixed.actions() if a.text() == "Odebrat sloupec…"][0]
    assert not remove.isEnabled() and fixed.reason_visible(remove)

    menu = table.header_menu(2)  # sloupec group: vyplnit 2 prázdné, seskupit, odebrat
    assert menu is not None
    texts = [a.text() for a in menu.actions()]
    assert "Vyplnit prázdné buňky (2)…" in texts and "Seskupit podle sloupce" in texts
    assert "Přidat sloupec" not in texts
    assert table.fill_column("group", "HC") == 2
    assert table.merged(Path("/x/b.wav"))["group"] == "HC"
    assert table.merged(Path("/x/a.wav"))["group"] == "PD"  # vyplněná buňka zůstala
    [a for a in menu.actions() if a.text() == "Seskupit podle sloupce"][0].trigger()
    assert grouped == ["group"]

    add.aboutToShow.emit()  # sloupec, který už v tabulce je, je šedý
    assert age.isEnabled() and not group.isEnabled()
