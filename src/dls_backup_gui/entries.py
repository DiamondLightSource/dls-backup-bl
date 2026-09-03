#!/usr/bin/python

# WHY DOES THIS NOT WORK??
# from .backupeditor import BackupEditor

from functools import partial
from logging import getLogger

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from dls_backup_bl.config import to_bool

log = getLogger(__name__)


def field_value(widget):
    """The value in a field, whichever kind of widget is holding it."""
    return widget.isChecked() if isinstance(widget, QCheckBox) else widget.text()


def set_field(widget, value) -> None:
    """Put a value into a field, coming from the table as displayed text."""
    if isinstance(widget, QCheckBox):
        widget.setChecked(to_bool(value))
    else:
        widget.setText(value)


class EntryPopup(QDialog):
    def __init__(self, EditMode, parent=None):
        QDialog.__init__(self, parent)
        self.parent = parent
        self.EditMode = EditMode

        # Create the required layouts
        self.GridLayout = QGridLayout()
        self.ButtonsLayout = QHBoxLayout()
        self.VerLayout = QVBoxLayout()

        this_tab = self.parent.Tabs.currentIndex()
        self.SelectedDevice = str(self.parent.Tabs.tabText(this_tab))
        self.data_type = parent.tab_entry_type[this_tab]
        self.FieldsList = self.data_type.keys()

        field_types = self.data_type.types()
        self.FieldWidgets = []

        for i, Field in enumerate(self.FieldsList):
            self.GridLayout.addWidget(QLabel(str(Field)), i, 0)
            # a bool is a checkbox: a text box would make an optional flag
            # mandatory, since a blank field disables the save buttons
            if field_types.get(Field) is bool:
                temp = QCheckBox()
            else:
                temp = QLineEdit()
            self.FieldWidgets.append(temp)
            self.GridLayout.addWidget(temp, i, 1)

        # Create the cancel button and add it to the buttons layout
        self.CancelButton = QPushButton("Cancel", self)
        self.CancelButton.clicked.connect(self.close)
        self.ButtonsLayout.addWidget(self.CancelButton)

        # Setup the add next button and add it to the button layout
        self.AddNextButton = QPushButton("Add Next", self)
        self.AddNextButton.setEnabled(False)
        self.AddNextButton.clicked.connect(partial(self.AddEditEntry, EditMode, True))
        self.ButtonsLayout.addWidget(self.AddNextButton)

        if EditMode:
            self.AddNextButton.setVisible(False)
            self.setWindowTitle("Edit Entry")
            for n in range(0, len(self.FieldsList)):
                set_field(
                    self.FieldWidgets[n],
                    self.parent.DeviceList.selectedIndexes()[n].data(),
                )
        else:
            self.setWindowTitle("Add Entry")

        # Setup the finish button and add it to the button layout
        self.AddFinishButton = QPushButton("Finished", self)
        self.AddFinishButton.setEnabled(False)
        self.AddFinishButton.clicked.connect(
            partial(self.AddEditEntry, EditMode, False)
        )
        # self.Name.textChanged.connect(partial(self.TextChanged, EditMode))
        self.ButtonsLayout.addWidget(self.AddFinishButton)

        # these have to be here, as the buttons in the function now exist
        for widget in self.FieldWidgets:
            if isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.ButtonVisibility)
            else:
                widget.textChanged.connect(self.ButtonVisibility)

        # Add both layouts to the final layout
        self.VerLayout.addLayout(self.GridLayout)
        self.VerLayout.addLayout(self.ButtonsLayout)
        self.setLayout(self.VerLayout)

    def TextChanged(self, thing, obj):
        for _n, letter in enumerate(thing.text()):
            UnicodeNum = letter.toUtf8()
            ord(UnicodeNum)

    def ButtonVisibility(self):
        Present = False
        EmptyLineEdit = False

        if self.EditMode:
            Present = False

        for widget in self.FieldWidgets:
            # a cleared checkbox is a valid answer, not a blank field
            if not isinstance(widget, QCheckBox) and len(widget.text()) < 1:
                EmptyLineEdit = True

        if Present or EmptyLineEdit:
            ButtonVisibility = False
            self.AddNextButton.setEnabled(ButtonVisibility)
            self.AddFinishButton.setEnabled(ButtonVisibility)
            # self.setTabOrder(self.TraPluText, self.CancelButton)
            self.CancelButton.setDefault(True)
        else:
            ButtonVisibility = True
            self.AddNextButton.setEnabled(ButtonVisibility)
            self.AddFinishButton.setEnabled(ButtonVisibility)
            # self.setTabOrder(self.TraPluText, self.AddNextButton)
            self.setTabOrder(self.AddNextButton, self.AddFinishButton)
            if self.EditMode:
                self.AddFinishButton.setDefault(True)
            else:
                self.AddNextButton.setDefault(True)

    def AddEditEntry(self, EditMode, NextEntry):
        self.SelectedDevice = str(
            self.parent.Tabs.tabText(self.parent.Tabs.currentIndex())
        )
        self.RowNumber = self.parent.DeviceList.selectionModel().currentIndex().row()

        values = [field_value(w) for w in self.FieldWidgets]
        try:
            new_data = self.data_type(*values)
        except ValueError as e:
            QMessageBox.warning(
                self, "Warning", "Invalid field\n" + str(e), QMessageBox.Ok
            )
        else:
            if EditMode:
                self.parent.config[self.SelectedDevice][self.RowNumber] = new_data
            else:
                self.parent.config[self.SelectedDevice].append(new_data)

            self.parent.config.save(self.parent.file)
            self.parent.display_entries()
            if NextEntry:
                for widget in self.FieldWidgets:
                    set_field(widget, "")
                self.FieldWidgets[0].setFocus()
            else:
                self.close()
