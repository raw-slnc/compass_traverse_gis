# -*- coding: utf-8 -*-
"""Spreadsheet-like notebook table widget."""

from __future__ import annotations

import re

from qgis.PyQt import QtCore, QtGui, QtWidgets
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
from qgis.gui import QgsMapToolEmitPoint


def _tr(message):
    return QtCore.QCoreApplication.translate("NotebookTableWidget", message)


def _normalize_dms_text(text):
    raw = str(text or "").strip()
    if not raw:
        return ""

    normalized = raw.replace("′", "'").replace("’", "'").replace("”", '"')
    normalized = normalized.replace("″", '"').replace("˚", "°")
    numbers = re.findall(r"[+-]?\d+(?:\.\d+)?", normalized)
    if not numbers:
        return raw

    if len(numbers) >= 3:
        degrees = int(float(numbers[0]))
        minutes = int(float(numbers[1]))
        seconds = float(numbers[2])
        return _format_dms(degrees, minutes, seconds)

    if len(numbers) == 1:
        compact = numbers[0]
        match = re.fullmatch(r"([+-]?\d+)(?:\.(\d+))?", compact)
        if not match:
            return raw
        integer_part = match.group(1)
        fraction_part = match.group(2) or ""
        sign = ""
        if integer_part.startswith(("+", "-")):
            sign = integer_part[0]
            integer_part = integer_part[1:]
        if len(integer_part) < 5:
            return raw
        degree_digits = integer_part[:-4]
        minute_digits = integer_part[-4:-2]
        second_digits = integer_part[-2:]
        degrees = int(f"{sign}{degree_digits}")
        minutes = int(minute_digits)
        seconds = float(f"{second_digits}.{fraction_part}" if fraction_part else second_digits)
        return _format_dms(degrees, minutes, seconds)

    return raw


def _format_dms(degrees, minutes, seconds):
    absolute_seconds = round(float(seconds), 2)
    absolute_minutes = int(minutes)
    absolute_degrees = int(degrees)

    if absolute_seconds >= 60.0:
        absolute_seconds -= 60.0
        absolute_minutes += 1
    if absolute_minutes >= 60:
        absolute_minutes -= 60
        absolute_degrees += 1

    sign = "-" if absolute_degrees < 0 else ""
    degree_value = abs(absolute_degrees)
    return f'{sign}{degree_value}°{absolute_minutes:02d}\'{absolute_seconds:05.2f}"'


def _decimal_to_dms(value):
    sign = -1 if float(value) < 0 else 1
    absolute = abs(float(value))
    degrees = int(absolute)
    minutes_float = (absolute - degrees) * 60.0
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60.0
    if sign < 0:
        degrees *= -1
    return _format_dms(degrees, minutes, seconds)


def _parse_dms_components(text):
    raw = str(text or "").strip()
    if not raw:
        return 0, 0, 0.0

    normalized = _normalize_dms_text(raw)
    numbers = re.findall(r"[+-]?\d+(?:\.\d+)?", normalized)
    if len(numbers) >= 3:
        degrees = int(float(numbers[0]))
        minutes = int(float(numbers[1]))
        seconds = float(numbers[2])
        return degrees, minutes, seconds
    return 0, 0, 0.0


class GeoPointInputDialog(QtWidgets.QDialog):

    def __init__(self, latitude_text="", longitude_text="", canvas=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("Enter Latitude / Longitude"))
        self._canvas = canvas
        self._canvas_point_tool = None
        self._previous_map_tool = None

        lat_deg, lat_min, lat_sec = _parse_dms_components(latitude_text)
        lon_deg, lon_min, lon_sec = _parse_dms_components(longitude_text)
        has_latitude = bool(str(latitude_text or "").strip())
        has_longitude = bool(str(longitude_text or "").strip())

        layout = QtWidgets.QGridLayout(self)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)

        self.latitudeDegreeEdit = _create_dms_line_edit(
            self,
            placeholder=_tr("Deg"),
            text=str(lat_deg) if has_latitude else "",
            validator=QtGui.QIntValidator(-90, 90, self),
        )
        self.latitudeMinuteEdit = _create_dms_line_edit(
            self,
            placeholder=_tr("Min"),
            text=f"{lat_min:02d}" if has_latitude else "",
            validator=QtGui.QIntValidator(0, 59, self),
        )
        self.latitudeSecondEdit = _create_dms_line_edit(
            self,
            placeholder=_tr("Sec"),
            text=f"{lat_sec:05.2f}" if has_latitude else "",
            validator=QtGui.QDoubleValidator(0.0, 59.99, 2, self),
        )
        self.longitudeDegreeEdit = _create_dms_line_edit(
            self,
            placeholder=_tr("Deg"),
            text=str(lon_deg) if has_longitude else "",
            validator=QtGui.QIntValidator(-180, 180, self),
        )
        self.longitudeMinuteEdit = _create_dms_line_edit(
            self,
            placeholder=_tr("Min"),
            text=f"{lon_min:02d}" if has_longitude else "",
            validator=QtGui.QIntValidator(0, 59, self),
        )
        self.longitudeSecondEdit = _create_dms_line_edit(
            self,
            placeholder=_tr("Sec"),
            text=f"{lon_sec:05.2f}" if has_longitude else "",
            validator=QtGui.QDoubleValidator(0.0, 59.99, 2, self),
        )

        layout.addWidget(QtWidgets.QLabel(_tr("Latitude")), 0, 0)
        layout.addWidget(self.latitudeDegreeEdit, 0, 1)
        layout.addWidget(QtWidgets.QLabel(_tr("Deg")), 0, 2)
        layout.addWidget(self.latitudeMinuteEdit, 0, 3)
        layout.addWidget(QtWidgets.QLabel(_tr("Min")), 0, 4)
        layout.addWidget(self.latitudeSecondEdit, 0, 5)
        layout.addWidget(QtWidgets.QLabel(_tr("Sec")), 0, 6)

        layout.addWidget(QtWidgets.QLabel(_tr("Longitude")), 1, 0)
        layout.addWidget(self.longitudeDegreeEdit, 1, 1)
        layout.addWidget(QtWidgets.QLabel(_tr("Deg")), 1, 2)
        layout.addWidget(self.longitudeMinuteEdit, 1, 3)
        layout.addWidget(QtWidgets.QLabel(_tr("Min")), 1, 4)
        layout.addWidget(self.longitudeSecondEdit, 1, 5)
        layout.addWidget(QtWidgets.QLabel(_tr("Sec")), 1, 6)

        example_label = QtWidgets.QLabel(_tr('Example: 34°48\'34.94"'))
        layout.addWidget(example_label, 2, 0, 1, 7)

        self.pickFromCanvasButton = QtWidgets.QPushButton(_tr("Pick from QGIS Canvas"), self)
        self.pickFromCanvasButton.clicked.connect(self._start_canvas_pick)
        layout.addWidget(self.pickFromCanvasButton, 3, 0, 1, 3)

        self.canvasPickStatusLabel = QtWidgets.QLabel("", self)
        self.canvasPickStatusLabel.setWordWrap(True)
        layout.addWidget(self.canvasPickStatusLabel, 3, 3, 1, 4)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box, 4, 0, 1, 7)

    def values(self):
        return (
            self._formatted_triplet(
                self.latitudeDegreeEdit,
                self.latitudeMinuteEdit,
                self.latitudeSecondEdit,
            ),
            self._formatted_triplet(
                self.longitudeDegreeEdit,
                self.longitudeMinuteEdit,
                self.longitudeSecondEdit,
            ),
        )

    def _formatted_triplet(self, degree_edit, minute_edit, second_edit):
        degree_text = degree_edit.text().strip()
        minute_text = minute_edit.text().strip()
        second_text = second_edit.text().strip()

        if not any((degree_text, minute_text, second_text)):
            return ""

        degrees = int(degree_text or "0")
        minutes = int(minute_text or "0")
        seconds = float(second_text or "0")
        return _format_dms(degrees, minutes, seconds)

    def _start_canvas_pick(self):
        if self._canvas is None:
            self.canvasPickStatusLabel.setText(_tr("QGIS canvas is not available."))
            return
        self._stop_canvas_pick()
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self._previous_map_tool = self._canvas.mapTool()
        self._canvas_point_tool = QgsMapToolEmitPoint(self._canvas)
        self._canvas_point_tool.canvasClicked.connect(self._handle_canvas_point_picked)
        self._canvas.setMapTool(self._canvas_point_tool)
        self._canvas.setFocus()
        self.canvasPickStatusLabel.setText(_tr("Click a location on the QGIS canvas."))

    def _handle_canvas_point_picked(self, point, button):
        del button
        self._stop_canvas_pick()
        try:
            source_crs = self._canvas.mapSettings().destinationCrs()
            target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
            transformer = QgsCoordinateTransform(
                source_crs,
                target_crs,
                QgsProject.instance(),
            )
            transformed = transformer.transform(point)
        except Exception:
            self.canvasPickStatusLabel.setText(_tr("Could not convert the canvas coordinate to latitude/longitude."))
            return

        latitude_text = _decimal_to_dms(transformed.y())
        longitude_text = _decimal_to_dms(transformed.x())
        self._set_dms_fields(
            self.latitudeDegreeEdit,
            self.latitudeMinuteEdit,
            self.latitudeSecondEdit,
            latitude_text,
        )
        self._set_dms_fields(
            self.longitudeDegreeEdit,
            self.longitudeMinuteEdit,
            self.longitudeSecondEdit,
            longitude_text,
        )
        self.canvasPickStatusLabel.setText(_tr("Location was set from the canvas."))

    def _set_dms_fields(self, degree_edit, minute_edit, second_edit, text):
        degrees, minutes, seconds = _parse_dms_components(text)
        degree_edit.setText(str(degrees))
        minute_edit.setText(f"{minutes:02d}")
        second_edit.setText(f"{seconds:05.2f}")

    def _stop_canvas_pick(self):
        if self._canvas is None or self._canvas_point_tool is None:
            return
        try:
            self._canvas_point_tool.canvasClicked.disconnect(self._handle_canvas_point_picked)
        except Exception:  # nosec B110
            pass
        if self._previous_map_tool is not None:
            self._canvas.setMapTool(self._previous_map_tool)
        self._canvas_point_tool = None
        self._previous_map_tool = None

    def done(self, result):
        self._stop_canvas_pick()
        super().done(result)


def _create_dms_line_edit(parent, placeholder, text, validator):
    line_edit = QtWidgets.QLineEdit(parent)
    line_edit.setPlaceholderText(placeholder)
    line_edit.setText(text)
    line_edit.setValidator(validator)
    line_edit.installEventFilter(_SelectAllOnFocus(line_edit))
    return line_edit


class _SelectAllOnFocus(QtCore.QObject):

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Type.FocusIn:
            QtCore.QTimer.singleShot(0, watched.selectAll)
        elif event.type() == QtCore.QEvent.Type.MouseButtonPress and watched.hasFocus():
            QtCore.QTimer.singleShot(0, watched.selectAll)
        return False


class NotebookTableDelegate(QtWidgets.QStyledItemDelegate):

    def __init__(self, table_widget):
        super().__init__(table_widget)
        self._table_widget = table_widget
        self._base_even = QtGui.QColor("#ffffff")
        self._base_odd = QtGui.QColor("#f5f6f7")
        self._geo_even = QtGui.QColor("#e7eef2")
        self._geo_odd = QtGui.QColor("#dde7ec")
        self._calc_even = QtGui.QColor("#f1e1e1")
        self._calc_odd = QtGui.QColor("#ead5d5")

    def paint(self, painter, option, index):
        paint_option = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(paint_option, index)

        if not (paint_option.state & QtWidgets.QStyle.StateFlag.State_Selected):
            if index.column() in self._table_widget.calc_columns():
                background = self._calc_even if index.row() % 2 == 0 else self._calc_odd
            elif index.column() in self._table_widget.geo_columns():
                background = self._geo_even if index.row() % 2 == 0 else self._geo_odd
            else:
                background = self._base_even if index.row() % 2 == 0 else self._base_odd
            painter.save()
            painter.fillRect(option.rect, background)
            painter.restore()
            paint_option.backgroundBrush = QtGui.QBrush(QtCore.Qt.BrushStyle.NoBrush)

        super().paint(painter, paint_option, index)


class NotebookTableWidget(QtWidgets.QTableWidget):
    """QTableWidget with familiar spreadsheet editing behavior."""

    geoValueChanged = QtCore.pyqtSignal(int, int, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._geo_columns = set()
        self._calc_columns = set()
        self._geo_values = {}
        self._map_canvas = None
        self._table_editable = True
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setAlternatingRowColors(True)
        palette = self.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#ffffff"))
        palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#f5f6f7"))
        self.setPalette(palette)
        self.setItemDelegate(NotebookTableDelegate(self))

    def keyPressEvent(self, event):
        if event.matches(QtGui.QKeySequence.StandardKey.Copy):
            self.copy_selection()
            return
        if not self._table_editable:
            if event.matches(QtGui.QKeySequence.StandardKey.Cut):
                return
            if event.matches(QtGui.QKeySequence.StandardKey.Paste):
                return
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                return
        if event.matches(QtGui.QKeySequence.StandardKey.Cut):
            self.cut_selection()
            return
        if event.matches(QtGui.QKeySequence.StandardKey.Paste):
            self.paste_from_clipboard()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.clear_selection()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.state() != QtWidgets.QAbstractItemView.State.EditingState:
                self.begin_editing_current_cell()
                return
        super().keyPressEvent(event)

    def begin_editing_current_cell(self):
        if not self._table_editable:
            return
        row = self.currentRow() if self.currentRow() >= 0 else 0
        column = self.currentColumn() if self.currentColumn() >= 0 else 0
        if column in self._geo_columns:
            self._open_geo_dialog(row, column)
            return
        if column in self._calc_columns:
            return
        self.ensure_size_for_range(row, column, row, column)
        item = self.item(row, column)
        if item is None:
            item = QtWidgets.QTableWidgetItem("")
            self.setItem(row, column, item)
        self.setCurrentCell(row, column)
        self.editItem(item)

    def set_geo_columns(self, columns):
        self._geo_columns = set(columns)

    def set_map_canvas(self, map_canvas):
        self._map_canvas = map_canvas

    def set_table_editable(self, is_editable):
        self._table_editable = bool(is_editable)

    def geo_columns(self):
        return self._geo_columns

    def set_calc_columns(self, columns):
        self._calc_columns = set(columns)

    def calc_columns(self):
        return self._calc_columns

    def edit(self, index, trigger, event):
        if not self._table_editable:
            return False
        if index.isValid() and index.column() in self._calc_columns:
            return False
        return super().edit(index, trigger, event)

    def mouseMoveEvent(self, event):
        index = self.indexAt(event.pos())
        if index.isValid() and index.column() in self._geo_columns:
            global_pos = event.globalPos()
            QtWidgets.QToolTip.showText(
                global_pos,
                _tr("Double-click to edit"),
                self,
            )
        else:
            QtWidgets.QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        QtWidgets.QToolTip.hideText()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if not self._table_editable:
            super().mouseDoubleClickEvent(event)
            return
        index = self.indexAt(event.pos())
        if index.isValid() and index.column() in self._geo_columns:
            self._open_geo_dialog(index.row(), index.column())
            return
        super().mouseDoubleClickEvent(event)

    def copy_selection(self):
        matrix = self._selection_matrix()
        if not matrix:
            return
        text = "\n".join("\t".join(row) for row in matrix)
        QtWidgets.QApplication.clipboard().setText(text)

    def cut_selection(self):
        self.copy_selection()
        self.clear_selection()

    def clear_selection(self):
        indexes = self.selectedIndexes()
        if not indexes and self.currentRow() >= 0 and self.currentColumn() >= 0:
            if self.currentColumn() in self._geo_columns:
                self.set_geo_value(self.currentRow(), self.currentColumn(), "", "")
            elif self.currentColumn() not in self._calc_columns:
                self._set_cell_text(self.currentRow(), self.currentColumn(), "")
            return
        for index in indexes:
            if index.column() in self._geo_columns:
                self.set_geo_value(index.row(), index.column(), "", "")
            elif index.column() not in self._calc_columns:
                self._set_cell_text(index.row(), index.column(), "")

    def paste_from_clipboard(self):
        text = QtWidgets.QApplication.clipboard().text()
        if not text:
            return

        matrix = self._clipboard_matrix(text)
        if not matrix:
            return

        if self._paste_single_value_into_selection(matrix):
            return

        start_row, start_column = self._paste_origin()
        end_row = start_row + len(matrix) - 1
        end_column = start_column + max(len(row) for row in matrix) - 1
        self.ensure_size_for_range(start_row, start_column, end_row, end_column)

        max_column = self.columnCount()
        for row_offset, row_values in enumerate(matrix):
            for column_offset, value in enumerate(row_values):
                column_index = start_column + column_offset
                if column_index >= max_column:
                    continue
                if column_index in self._geo_columns or column_index in self._calc_columns:
                    continue
                self._set_cell_text(start_row + row_offset, column_index, value)

    def ensure_size_for_range(self, start_row, start_column, end_row, end_column):
        del start_column, end_column
        if end_row >= self.rowCount():
            self.setRowCount(end_row + 1)

    def _selection_matrix(self):
        selected_indexes = self.selectedIndexes()
        if not selected_indexes:
            if self.currentRow() < 0 or self.currentColumn() < 0:
                return []
            return [[self._cell_text(self.currentRow(), self.currentColumn())]]

        rows = [index.row() for index in selected_indexes]
        columns = [index.column() for index in selected_indexes]
        min_row, max_row = min(rows), max(rows)
        min_column, max_column = min(columns), max(columns)

        matrix = []
        for row_index in range(min_row, max_row + 1):
            row_values = []
            for column_index in range(min_column, max_column + 1):
                row_values.append(self._cell_text(row_index, column_index))
            matrix.append(row_values)
        return matrix

    def _clipboard_matrix(self, text):
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        rows = normalized.split("\n")
        if rows and rows[-1] == "":
            rows.pop()
        return [row.split("\t") for row in rows if row is not None]

    def _paste_single_value_into_selection(self, matrix):
        if len(matrix) != 1 or len(matrix[0]) != 1:
            return False
        selected_indexes = self.selectedIndexes()
        if len(selected_indexes) <= 1:
            return False

        value = matrix[0][0]
        for index in selected_indexes:
            if index.column() in self._geo_columns or index.column() in self._calc_columns:
                continue
            self._set_cell_text(index.row(), index.column(), value)
        return True

    def _paste_origin(self):
        selected_ranges = self.selectedRanges()
        if selected_ranges:
            first_range = selected_ranges[0]
            return first_range.topRow(), first_range.leftColumn()
        row = self.currentRow() if self.currentRow() >= 0 else 0
        column = self.currentColumn() if self.currentColumn() >= 0 else 0
        return row, column

    def _cell_text(self, row_index, column_index):
        item = self.item(row_index, column_index)
        if item is None:
            return ""
        return item.text()

    def _set_cell_text(self, row_index, column_index, value):
        item = self.item(row_index, column_index)
        if item is None:
            item = QtWidgets.QTableWidgetItem("")
            self.setItem(row_index, column_index, item)
        if column_index in self._calc_columns:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setText(value)

    def set_geo_value(self, row_index, column_index, latitude_text, longitude_text):
        if latitude_text:
            latitude_text = _normalize_dms_text(latitude_text)
        if longitude_text:
            longitude_text = _normalize_dms_text(longitude_text)

        key = (row_index, column_index)
        if latitude_text or longitude_text:
            self._geo_values[key] = (latitude_text, longitude_text)
            self._set_cell_text(row_index, column_index, _tr("Set"))
        else:
            self._geo_values.pop(key, None)
            self._set_cell_text(row_index, column_index, "")
        self.geoValueChanged.emit(row_index, column_index, latitude_text, longitude_text)

    def clear_geo_values(self):
        self._geo_values.clear()

    def geo_values_snapshot(self):
        return dict(self._geo_values)

    def restore_geo_values_snapshot(self, values):
        self._geo_values = dict(values or {})

    def _open_geo_dialog(self, row_index, column_index):
        latitude_text, longitude_text = self._geo_values.get((row_index, column_index), ("", ""))
        dialog = GeoPointInputDialog(
            latitude_text,
            longitude_text,
            canvas=self._map_canvas,
            parent=self,
        )
        dialog.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        dialog.show()
        loop = QtCore.QEventLoop(dialog)
        dialog.finished.connect(loop.quit)
        loop.exec()
        if dialog.result() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        latitude_text, longitude_text = dialog.values()
        self.set_geo_value(row_index, column_index, latitude_text, longitude_text)
