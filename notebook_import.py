# -*- coding: utf-8 -*-
"""Notebook table import helpers.

This module isolates file parsing, header auto-detection, and the column
mapping dialog so that import behavior can evolve independently from the
dockwidget UI.
"""

from __future__ import annotations

import csv
import re

from qgis.PyQt import QtWidgets

from .survey_model import IMPORTABLE_NOTEBOOK_COLUMNS


IMPORT_FIELD_LABELS = {
    "from_station": "測量点",
    "target_station": "目標点",
    "connect_to": "接続点",
    "close_to": "閉合点",
    "slope_distance": "SD",
    "inclination": "INC",
    "azimuth": "AZ",
    "horizontal_distance": "HD",
    "latitude_dms": "緯度",
    "longitude_dms": "経度",
}

HEADER_KEYWORDS = {
    "from_station": ("測量点", "測点", "起点", "from"),
    "target_station": ("目標点", "目標", "到達点", "target", "to"),
    "connect_to": ("接続点", "接続"),
    "close_to": ("閉合点", "閉合"),
    "slope_distance": ("sd", "斜距離", "射距離", "実測距離"),
    "inclination": ("inc", "高低", "高低角", "鉛直角"),
    "azimuth": ("az", "方角", "方位角", "方位", "方向角"),
    "horizontal_distance": ("hd", "水平距離"),
    "latitude_dms": ("緯度", "lat", "latitude"),
    "longitude_dms": ("経度", "lon", "lng", "longitude"),
}


def normalize_header_text(value):
    return re.sub(r"[\s_\-・/]+", "", str(value or "").strip()).lower()


def detect_column_mapping(headers):
    normalized_headers = [normalize_header_text(header) for header in headers]
    mapping = {}
    used_indexes = set()

    for field_name, keywords in HEADER_KEYWORDS.items():
        for keyword in keywords:
            normalized_keyword = normalize_header_text(keyword)
            for header_index, normalized_header in enumerate(normalized_headers):
                if header_index in used_indexes:
                    continue
                if normalized_keyword and normalized_keyword in normalized_header:
                    mapping[field_name] = header_index
                    used_indexes.add(header_index)
                    break
            if field_name in mapping:
                break

    return mapping


def read_import_rows(file_path, header_row=0):
    """ファイルを読み込み (headers, rows) を返す。

    Parameters
    ----------
    header_row : int
        ヘッダーとして扱う行番号（0始まり）。
    """
    extension = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    if extension == "csv":
        dataframe = read_csv_dataframe(file_path, header_row=header_row)
    else:
        dataframe = read_excel_dataframe(file_path, header_row=header_row)

    headers = [str(column).strip() for column in dataframe.columns]
    rows = dataframe.fillna("").astype(str).values.tolist()
    return headers, rows


def read_csv_dataframe(file_path, header_row=0):
    pd = _require_pandas()
    last_error = None
    for encoding in ("utf-8-sig", "cp932", "utf-8"):
        try:
            with open(file_path, "r", encoding=encoding, newline="") as handle:
                sample = handle.read(2048)
                handle.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample)
                    separator = dialect.delimiter
                except csv.Error:
                    separator = ","
            return pd.read_csv(
                file_path, dtype=str, encoding=encoding,
                sep=separator, header=header_row,
            )
        except Exception as error:
            last_error = error
    raise last_error or ValueError("CSVを読み込めませんでした。")


def read_excel_dataframe(file_path, header_row=0):
    pd = _require_pandas()
    dataframe = pd.read_excel(file_path, dtype=str, header=header_row)
    if dataframe is None:
        raise ValueError("Excelシートを読み込めませんでした。")
    return dataframe


def _require_pandas():
    try:
        import pandas as pd  # type: ignore
    except Exception as error:
        raise ImportError(
            "pandas is required for table import. Install pandas (and openpyxl for Excel)."
        ) from error
    return pd


class ColumnMappingDialog(QtWidgets.QDialog):

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("読込列の対応")
        self._file_path = file_path
        self._headers: list[str] = []
        self._rows: list[list] = []
        self._combos: dict = {}

        layout = QtWidgets.QVBoxLayout(self)

        # ヘッダー行選択
        header_row_layout = QtWidgets.QHBoxLayout()
        header_row_layout.addWidget(QtWidgets.QLabel("ヘッダー行:"))
        self._header_spin = QtWidgets.QSpinBox()
        self._header_spin.setMinimum(1)
        self._header_spin.setMaximum(50)
        self._header_spin.setValue(1)
        self._header_spin.setSuffix(" 行目")
        self._header_spin.setFixedWidth(90)
        header_row_layout.addWidget(self._header_spin)
        self._status_label = QtWidgets.QLabel("")
        self._status_label.setStyleSheet("color: red; font-size: 10px;")
        header_row_layout.addWidget(self._status_label)
        header_row_layout.addStretch()
        layout.addLayout(header_row_layout)

        description = QtWidgets.QLabel("対応列を確認し、必要なら変更してから読み込みます。")
        description.setWordWrap(True)
        layout.addWidget(description)

        self._form_layout = QtWidgets.QGridLayout()
        self._form_layout.setHorizontalSpacing(8)
        self._form_layout.setVerticalSpacing(6)
        layout.addLayout(self._form_layout)

        self._button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

        # コンボボックスを初期構築（空）
        for row_index, field_name in enumerate(IMPORTABLE_NOTEBOOK_COLUMNS):
            label = QtWidgets.QLabel(IMPORT_FIELD_LABELS[field_name])
            combo = QtWidgets.QComboBox()
            self._combos[field_name] = combo
            self._form_layout.addWidget(label, row_index, 0)
            self._form_layout.addWidget(combo, row_index, 1)

        self._header_spin.valueChanged.connect(self._reload)
        self._reload()

    def _reload(self):
        header_row = self._header_spin.value() - 1  # UI は1始まり → 0始まりに変換
        try:
            headers, rows = read_import_rows(self._file_path, header_row=header_row)
        except Exception as error:
            self._status_label.setText(str(error)[:80])
            self._button_box.button(
                QtWidgets.QDialogButtonBox.StandardButton.Ok
            ).setEnabled(False)
            return

        self._headers = headers
        self._rows = rows
        self._status_label.setText("")
        self._button_box.button(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
        ).setEnabled(True)

        auto_mapping = detect_column_mapping(headers)
        choices = ["未使用"] + headers
        for field_name, combo in self._combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(choices)
            detected = auto_mapping.get(field_name)
            if detected is not None:
                combo.setCurrentIndex(detected + 1)
            combo.blockSignals(False)

    def header_row(self) -> int:
        """0始まりのヘッダー行インデックスを返す。"""
        return self._header_spin.value() - 1

    def headers(self) -> list[str]:
        return self._headers

    def rows(self) -> list[list]:
        return self._rows

    def mapping(self) -> dict:
        result = {}
        for field_name, combo in self._combos.items():
            if combo.currentIndex() <= 0:
                continue
            result[field_name] = combo.currentIndex() - 1
        return result
