# -*- coding: utf-8 -*-
"""Export modal and HTML/PDF bundle generation."""

from __future__ import annotations

import base64
import html as html_module
import math
import os
import tempfile
from datetime import datetime

from qgis.PyQt import QtCore, QtGui, QtWidgets
from qgis.core import QgsMapLayerType, QgsProject, QgsRectangle

try:
    from qgis.gui import QgsMapCanvas
    _HAS_MAP_CANVAS = True
except ImportError:
    _HAS_MAP_CANVAS = False

try:
    from qgis.PyQt.QtWebEngineWidgets import QWebEngineView
    _HAS_WEBENGINE = True
except ImportError:
    _HAS_WEBENGINE = False

# ---------------------------------------------------------------------------
# Paper sizes: (width_mm, height_mm)
# ---------------------------------------------------------------------------
PAPER_SIZES = {
    "A4 縦": (210, 297),
    "A4 横": (297, 210),
    "A3 縦": (297, 420),
    "A3 横": (420, 297),
}

_CSS_PAGE_SIZE = {
    "A4 縦": "A4 portrait",
    "A4 横": "A4 landscape",
    "A3 縦": "A3 portrait",
    "A3 横": "A3 landscape",
}

_MARGIN_MM = 12
_HEADER_MM = 25
_PDF_DPI = 300


def _floor_ha_str(m2):
    """Return ha string truncated (not rounded) to 2 decimal places."""
    val = math.floor(m2 / 10000.0 * 100) / 100
    return f"{val:.2f} ha"


# ---------------------------------------------------------------------------
# Qt-based fallback traverse preview widget (no QgsMapCanvas)
# ---------------------------------------------------------------------------

class A4PreviewWidget(QtWidgets.QWidget):
    """Simple traverse preview widget (fallback)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paper_key = "A4 横"
        self._project_name = ""
        self._work_name = ""
        self._scale_text = "1:1000"
        self._note_text = ""
        self._points = []
        self._auto_scale_text = ""
        self.setMinimumSize(420, 300)

    def set_preview_state(self, *, paper_key, project_name, work_name,
                          scale_text, note_text, points):
        self._paper_key = paper_key or "A4 横"
        self._project_name = project_name or ""
        self._work_name = work_name or ""
        self._scale_text = scale_text or ""
        self._note_text = note_text or ""
        self._points = list(points or [])
        self._auto_scale_text = ""
        self.update()

    def _paper_ratio(self):
        w, h = PAPER_SIZES.get(self._paper_key, (297, 210))
        return w / h

    def paintEvent(self, event):  # noqa: N802
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor("#eef2f7"))

        view_rect = self.rect().adjusted(16, 16, -16, -16)
        ratio = self._paper_ratio()
        if view_rect.width() / max(1, view_rect.height()) > ratio:
            paper_h = view_rect.height()
            paper_w = int(round(paper_h * ratio))
        else:
            paper_w = view_rect.width()
            paper_h = int(round(paper_w / ratio))

        paper_x = view_rect.center().x() - paper_w // 2
        paper_y = view_rect.center().y() - paper_h // 2
        paper_rect = QtCore.QRect(paper_x, paper_y, paper_w, paper_h)

        painter.setPen(QtGui.QPen(QtGui.QColor("#334155"), 2))
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
        painter.drawRect(paper_rect)

        margin = max(10, int(min(paper_w, paper_h) * 0.05))
        content_rect = paper_rect.adjusted(margin, margin, -margin, -margin)

        header_h = max(32, int(content_rect.height() * 0.10))
        map_rect = QtCore.QRect(
            content_rect.left(), content_rect.top() + header_h,
            content_rect.width(), content_rect.height() - header_h)

        painter.setPen(QtGui.QPen(QtGui.QColor("#cbd5e1"), 1))
        painter.drawLine(
            content_rect.left(), content_rect.top() + header_h,
            content_rect.right(), content_rect.top() + header_h)

        self._draw_traverse(painter, map_rect)

        painter.setPen(QtGui.QPen(QtGui.QColor("#0f172a"), 1))
        painter.setFont(QtGui.QFont("Meiryo", 8))
        painter.drawText(
            content_rect.adjusted(4, 2, -4, -int(content_rect.height() * 0.90)),
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            f"{self._project_name}  {self._work_name}")
        painter.drawText(
            content_rect.adjusted(4, 2, -4, -int(content_rect.height() * 0.90)),
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
            f"1:{self._auto_scale_text or self._scale_text}  {self._paper_key}")
        painter.end()

    def _draw_traverse(self, painter, content_rect):
        if len(self._points) < 2:
            return
        xs = [p[0] for p in self._points]
        ys = [p[1] for p in self._points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        ex_w = max(1e-9, (max_x - min_x)) * 1.15
        ex_h = max(1e-9, (max_y - min_y)) * 1.15
        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0
        scale_px = min(content_rect.width() / ex_w, content_rect.height() / ex_h)

        def tr(pt):
            px = content_rect.left() + (pt[0] - cx + ex_w / 2) * scale_px
            py = content_rect.bottom() - (pt[1] - cy + ex_h / 2) * scale_px
            return QtCore.QPointF(px, py)

        poly = QtGui.QPolygonF([tr(p) for p in self._points])
        painter.save()
        pen = QtGui.QPen(QtGui.QColor("#d8112a"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPolyline(poly)
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
        for pt in poly:
            painter.drawEllipse(pt, 3.0, 3.0)
        painter.restore()

        mm_w = max(1.0, content_rect.width() * 25.4 / max(1.0, self.logicalDpiX()))
        mm_h = max(1.0, content_rect.height() * 25.4 / max(1.0, self.logicalDpiY()))
        denom = max((max_x - min_x) / (mm_w / 1000.0),
                    (max_y - min_y) / (mm_h / 1000.0))
        self._auto_scale_text = f"{int(round(denom / 10.0) * 10)}"


# ---------------------------------------------------------------------------
# Paper frame widget — wraps QgsMapCanvas inside an A4/A3 paper outline
# ---------------------------------------------------------------------------

class PaperFrameWidget(QtWidgets.QWidget):
    """Draws a paper outline and keeps QgsMapCanvas positioned inside it."""

    _MARGIN = 20

    def __init__(self, paper_key="A4 横", parent=None):
        super().__init__(parent)
        self._paper_key = paper_key
        self.setMinimumSize(200, 150)
        self._canvas = None
        if _HAS_MAP_CANVAS:
            from qgis.gui import QgsMapToolPan
            self._canvas = QgsMapCanvas(self)
            self._canvas.setCanvasColor(QtGui.QColor(255, 255, 255))
            self._canvas.enableAntiAliasing(True)
            self._pan_tool = QgsMapToolPan(self._canvas)
            self._canvas.setMapTool(self._pan_tool)

    def canvas(self):
        return self._canvas

    def set_paper_key(self, key):
        self._paper_key = key
        self._reposition_canvas()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_canvas()

    def _paper_rect(self):
        m = self._MARGIN
        w_mm, h_mm = PAPER_SIZES.get(self._paper_key, (297, 210))
        ratio = w_mm / h_mm
        aw = max(1, self.width() - 2 * m)
        ah = max(1, self.height() - 2 * m)
        if aw / ah > ratio:
            ph, pw = ah, int(ah * ratio)
        else:
            pw, ph = aw, int(aw / ratio)
        return QtCore.QRect(
            (self.width() - pw) // 2,
            (self.height() - ph) // 2,
            pw, ph)

    def _header_height(self, paper_rect):
        """Compute header strip height proportional to _HEADER_MM."""
        _, h_mm = PAPER_SIZES.get(self._paper_key, (297, 210))
        content_h_mm = max(1, h_mm - 2 * _MARGIN_MM)
        return max(18, int(paper_rect.height() * _HEADER_MM / content_h_mm))

    def _reposition_canvas(self):
        if self._canvas is None:
            return
        pr = self._paper_rect()
        hh = self._header_height(pr)
        cr = pr.adjusted(1, hh + 1, -1, -1)
        if cr.width() > 0 and cr.height() > 0:
            self._canvas.setGeometry(cr)

    def paintEvent(self, event):
        del event
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor("#dde3ec"))
        pr = self._paper_rect()
        hh = self._header_height(pr)
        # shadow
        p.fillRect(pr.adjusted(3, 3, 3, 3), QtGui.QColor("#9aa3b2"))
        # paper
        p.fillRect(pr, QtGui.QColor("#ffffff"))
        # border
        p.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1))
        p.drawRect(pr)
        # header separator
        p.setPen(QtGui.QPen(QtGui.QColor("#94a3b8"), 1))
        p.drawLine(pr.left(), pr.top() + hh, pr.right(), pr.top() + hh)
        # label
        p.setPen(QtGui.QPen(QtGui.QColor("#475569")))
        p.setFont(QtGui.QFont("Meiryo", 7))
        p.drawText(
            QtCore.QRect(pr.left() + 4, pr.top(), pr.width() - 8, hh),
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            self._paper_key)
        p.end()


# ---------------------------------------------------------------------------
# Export settings dialog
# ---------------------------------------------------------------------------

class ExportSettingsDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        layer_names=None,
        preview_context=None,
        export_callback=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Export Settings"))
        self.resize(1180, 760)
        self._layer_names = list(layer_names or [])
        self._pc = preview_context or {}
        self._export_callback = export_callback
        self._tmp_html_path = None
        self._suggested_scale = None
        self._map_canvas_initialized = False
        self._no_background_text = self.tr("(None)")
        self._build_ui()
        self._refresh_table_previews()

    def sizeHint(self):
        return QtCore.QSize(1180, 760)

    def minimumSizeHint(self):
        return QtCore.QSize(900, 600)

    def event(self, e):
        # Block LayoutRequest after initial render to prevent canvas sizeHint
        # from propagating up and auto-growing the dialog window.
        if (getattr(self, '_map_canvas_initialized', False)
                and e.type() == QtCore.QEvent.Type.LayoutRequest):
            return True
        return super().event(e)

    # ---- UI construction ----

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)

        # Left: settings
        left = QtWidgets.QVBoxLayout()
        root.addLayout(left, 0)

        grp = QtWidgets.QGroupBox(self.tr("Output Settings"))
        form = QtWidgets.QGridLayout(grp)
        left.addWidget(grp)

        row = 0
        form.addWidget(QtWidgets.QLabel(self.tr("Output Folder")), row, 0)
        self.output_dir_edit = QtWidgets.QLineEdit()
        self.output_dir_edit.setText(
            QtCore.QStandardPaths.writableLocation(
                QtCore.QStandardPaths.StandardLocation.DocumentsLocation))
        form.addWidget(self.output_dir_edit, row, 1)
        browse_btn = QtWidgets.QPushButton(self.tr("Browse..."))
        browse_btn.clicked.connect(self._browse_output_dir)
        form.addWidget(browse_btn, row, 2)

        row += 1
        form.addWidget(QtWidgets.QLabel(self.tr("Paper Size (Map)")), row, 0)
        self.paper_combo = QtWidgets.QComboBox()
        self.paper_combo.addItems(["A4 縦", "A3 横"])
        self.paper_combo.setCurrentText("A4 縦")
        form.addWidget(self.paper_combo, row, 1, 1, 2)

        row += 1
        form.addWidget(QtWidgets.QLabel(self.tr("Scale (1:n)")), row, 0)
        self.scale_combo = QtWidgets.QComboBox()
        self.scale_combo.setEditable(True)
        self.scale_combo.lineEdit().setPlaceholderText(
            self.tr("The recommended scale is filled in automatically.")
        )
        form.addWidget(self.scale_combo, row, 1)
        self._apply_scale_btn = QtWidgets.QPushButton(self.tr("Use Recommended Scale"))
        self._apply_scale_btn.setEnabled(False)
        self._apply_scale_btn.clicked.connect(self._apply_suggested_scale)
        form.addWidget(self._apply_scale_btn, row, 2)

        row += 1
        self._auto_scale_label = QtWidgets.QLabel("")
        self._auto_scale_label.setStyleSheet("color:#4b7bb5;font-size:11px;")
        form.addWidget(self._auto_scale_label, row, 0, 1, 3)

        row += 1
        form.addWidget(QtWidgets.QLabel(self.tr("Background Layer")), row, 0)
        self.background_layer_combo = QtWidgets.QComboBox()
        self.background_layer_combo.addItem(self._no_background_text)
        self.background_layer_combo.addItems(self._layer_names)
        form.addWidget(self.background_layer_combo, row, 1, 1, 2)

        row += 1
        form.addWidget(QtWidgets.QLabel(self.tr("Drawing Number")), row, 0)
        self.drawing_number_edit = QtWidgets.QLineEdit()
        self.drawing_number_edit.setPlaceholderText(self.tr("Example: No.1"))
        form.addWidget(self.drawing_number_edit, row, 1, 1, 2)

        row += 1
        form.addWidget(QtWidgets.QLabel(self.tr("Station Label Interval")), row, 0)
        self.label_interval_spin = QtWidgets.QSpinBox()
        self.label_interval_spin.setRange(1, 100)
        self.label_interval_spin.setValue(5)
        self.label_interval_spin.setSuffix(self.tr(" points"))
        form.addWidget(self.label_interval_spin, row, 1, 1, 2)

        row += 1
        form.addWidget(QtWidgets.QLabel(self.tr("Notes (Calculation Sheets)")), row, 0)
        self.note_edit = QtWidgets.QLineEdit()
        self.note_edit.setPlaceholderText(self.tr("Example: Internal document"))
        form.addWidget(self.note_edit, row, 1, 1, 2)

        left.addStretch(1)

        open_browser_btn = QtWidgets.QPushButton(
            self.tr("Review Notebook / Area Calculations in Browser...")
        )
        open_browser_btn.setToolTip(
            self.tr("Open the survey notebook and area calculation HTML in a browser.")
        )
        open_browser_btn.clicked.connect(self._open_table_html_in_browser)
        left.addWidget(open_browser_btn)
        self._export_btn = QtWidgets.QPushButton(self.tr("Export Deliverables"))
        self._export_btn.setToolTip(
            self.tr("Export the deliverables with the current settings.")
        )
        self._export_btn.clicked.connect(self._export_deliverables)
        left.addWidget(self._export_btn)

        # Right: preview tabs
        right = QtWidgets.QVBoxLayout()
        root.addLayout(right, 1)

        info = QtWidgets.QLabel(
            self.tr(
                "The map preview uses the current QGIS layers as-is. "
                "Changing the scale or paper updates the preview."
            )
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#555;font-size:11px;")
        right.addWidget(info)

        self.preview_tabs = QtWidgets.QTabWidget()

        _ignored = QtWidgets.QSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Ignored)

        # Map preview: QgsMapCanvas embedded inside a paper frame widget
        if _HAS_MAP_CANVAS:
            self._frame_plain = PaperFrameWidget("A4 縦")
            self._map_canvas_plain = self._frame_plain.canvas()
            self.preview_tabs.addTab(self._frame_plain, self.tr("Map (PDF Export)"))

            self._frame_bg = PaperFrameWidget("A4 縦")
            self._map_canvas_bg = self._frame_bg.canvas()
            self.preview_tabs.addTab(self._frame_bg, self.tr("Map + Background (PDF Export)"))
        else:
            self.a4_preview = A4PreviewWidget()
            self.preview_tabs.addTab(self.a4_preview, self.tr("Map (Simple Preview)"))

        # 測量野帳タブ
        nb_widget = QtWidgets.QWidget()
        nb_layout = QtWidgets.QVBoxLayout(nb_widget)
        nb_layout.setContentsMargins(6, 6, 6, 4)
        nb_title = QtWidgets.QLabel(self.tr("Survey Notebook"))
        nb_title.setStyleSheet("font-weight:bold;font-size:12px;padding:2px 0;")
        nb_layout.addWidget(nb_title)
        self._nb_info_table = QtWidgets.QTableWidget()
        self._nb_info_table.setColumnCount(4)
        self._nb_info_table.horizontalHeader().setVisible(False)
        self._nb_info_table.verticalHeader().setVisible(False)
        self._nb_info_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        _nih = self._nb_info_table.horizontalHeader()
        for _c in range(4):
            _nih.setSectionResizeMode(
                _c,
                QtWidgets.QHeaderView.ResizeMode.ResizeToContents if _c % 2 == 0
                else QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._nb_info_table.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nb_info_table.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nb_info_table.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        nb_layout.addWidget(self._nb_info_table)
        nb_summary_label = QtWidgets.QLabel(self.tr("Export Summary"))
        nb_summary_label.setStyleSheet("font-weight:bold;font-size:11px;padding:2px 0;")
        nb_layout.addWidget(nb_summary_label)
        self._nb_export_summary_table = self._make_summary_table([
            self.tr("Type"),
            self.tr("Name"),
            self.tr("Details"),
        ])
        nb_layout.addWidget(self._nb_export_summary_table)
        nb_select_row = QtWidgets.QHBoxLayout()
        nb_select_row.addWidget(QtWidgets.QLabel(self.tr("Displayed Section")))
        self._nb_section_combo = QtWidgets.QComboBox()
        nb_select_row.addWidget(self._nb_section_combo, 1)
        nb_layout.addLayout(nb_select_row)
        self._nb_section_summary_table = self._make_summary_table([
            self.tr("Item"),
            self.tr("Value"),
        ])
        nb_layout.addWidget(self._nb_section_summary_table)
        self._notebook_table = self._make_table([
            self.tr("From"),
            self.tr("To"),
            self.tr("Azimuth"),
            self.tr("Inclination"),
            self.tr("Slope Distance (m)"),
            self.tr("Horizontal Distance (m)"),
            self.tr("Elevation Difference (m)"),
            self.tr("dX"),
            self.tr("dY"),
            self.tr("Connect To"),
            self.tr("Close To"),
            self.tr("Notes"),
        ])
        nb_layout.addWidget(self._notebook_table, 1)
        self.preview_tabs.addTab(nb_widget, self.tr("Survey Notebook"))

        # 面積計算簿タブ
        calc_widget = QtWidgets.QWidget()
        calc_layout = QtWidgets.QVBoxLayout(calc_widget)
        calc_layout.setContentsMargins(6, 6, 6, 4)
        calc_title = QtWidgets.QLabel(self.tr("Area Calculation Sheet (Double Meridian Distance)"))
        calc_title.setStyleSheet("font-weight:bold;font-size:12px;padding:2px 0;")
        calc_layout.addWidget(calc_title)
        self._calc_info_table = QtWidgets.QTableWidget()
        self._calc_info_table.setColumnCount(8)
        self._calc_info_table.horizontalHeader().setVisible(False)
        self._calc_info_table.verticalHeader().setVisible(False)
        self._calc_info_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        _ih = self._calc_info_table.horizontalHeader()
        for _c in range(8):
            _ih.setSectionResizeMode(
                _c,
                QtWidgets.QHeaderView.ResizeMode.ResizeToContents if _c % 2 == 0
                else QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._calc_info_table.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._calc_info_table.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._calc_info_table.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        calc_layout.addWidget(self._calc_info_table)
        calc_summary_label = QtWidgets.QLabel(self.tr("Area Summary"))
        calc_summary_label.setStyleSheet("font-weight:bold;font-size:11px;padding:2px 0;")
        calc_layout.addWidget(calc_summary_label)
        self._calc_export_summary_table = self._make_summary_table([
            self.tr("Parcel"),
            self.tr("Area (m²)"),
            self.tr("Area (ha)"),
        ])
        calc_layout.addWidget(self._calc_export_summary_table)
        calc_select_row = QtWidgets.QHBoxLayout()
        calc_select_row.addWidget(QtWidgets.QLabel(self.tr("Displayed Parcel")))
        self._calc_section_combo = QtWidgets.QComboBox()
        calc_select_row.addWidget(self._calc_section_combo, 1)
        calc_layout.addLayout(calc_select_row)
        self._calc_table = self._make_table([
            self.tr("From"),
            self.tr("To"),
            self.tr("Azimuth"),
            self.tr("Inclination"),
            self.tr("Slope Distance (m)"),
            self.tr("Horizontal Distance (m)"),
            self.tr("Elevation Difference (m)"),
            self.tr("Y (m)"),
            self.tr("X (m)"),
            self.tr("Z (m)"),
            self.tr("Double Meridian Distance"),
            self.tr("Latitude"),
            self.tr("Double Area"),
        ])
        calc_layout.addWidget(self._calc_table, 1)
        self.preview_tabs.addTab(calc_widget, self.tr("Area Calculation Sheet"))
        self._calc_tab_index = self.preview_tabs.indexOf(calc_widget)

        right.addWidget(self.preview_tabs, 1)
        self.preview_tabs.currentChanged.connect(self._on_tab_changed)

        close_row = QtWidgets.QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QtWidgets.QPushButton(self.tr("Close Settings"))
        close_btn.clicked.connect(self.reject)
        close_row.addWidget(close_btn)
        right.addLayout(close_row)

        # Connect settings → preview refresh
        self.paper_combo.currentTextChanged.connect(self._on_setting_changed)
        self.scale_combo.currentTextChanged.connect(self._on_setting_changed)
        self.background_layer_combo.currentTextChanged.connect(self._on_setting_changed)
        self.note_edit.textChanged.connect(self._on_setting_changed)
        self._nb_section_combo.currentIndexChanged.connect(self._refresh_notebook_section_detail)
        self._calc_section_combo.currentIndexChanged.connect(self._refresh_calc_section_detail)
        self._nb_preview_sections = []
        self._calc_preview_sections = []
        self._sync_added_preview_table_metrics()

    @staticmethod
    def _make_table(headers):
        t = QtWidgets.QTableWidget()
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch)
        t.setAlternatingRowColors(True)
        pal = t.palette()
        pal.setColor(QtGui.QPalette.ColorRole.Base,          QtGui.QColor("#ffffff"))
        pal.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#f5f6f7"))
        t.setPalette(pal)
        return t

    @staticmethod
    def _make_summary_table(headers):
        t = QtWidgets.QTableWidget()
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(True)
        header = t.horizontalHeader()
        for idx in range(len(headers)):
            mode = (
                QtWidgets.QHeaderView.ResizeMode.Stretch
                if idx == len(headers) - 1
                else QtWidgets.QHeaderView.ResizeMode.ResizeToContents
            )
            header.setSectionResizeMode(idx, mode)
        t.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        t.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        pal = t.palette()
        pal.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#ffffff"))
        pal.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor("#f5f6f7"))
        t.setPalette(pal)
        return t

    def _sync_added_preview_table_metrics(self):
        self._copy_row_metrics(self._nb_export_summary_table, self._nb_info_table)
        self._copy_row_metrics(self._nb_section_summary_table, self._nb_info_table)
        self._copy_row_metrics(self._calc_export_summary_table, self._calc_info_table)

    @staticmethod
    def _copy_row_metrics(target, source):
        target.verticalHeader().setDefaultSectionSize(
            source.verticalHeader().defaultSectionSize()
        )

    def _browse_output_dir(self):
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            self.tr("Select Output Folder"),
            self.output_dir_edit.text().strip(),
        )
        if selected:
            self.output_dir_edit.setText(selected)

    def _export_deliverables(self):
        if callable(self._export_callback):
            self._export_callback(self.settings())

    def _preview_label(self, text):
        return self.tr(text)

    # ---- Lifecycle ----

    def showEvent(self, event):
        super().showEvent(event)
        if not self._map_canvas_initialized:
            self._map_canvas_initialized = True
            QtCore.QTimer.singleShot(150, self._refresh_map_canvases)

    # ---- Map canvas preview ----

    def _on_setting_changed(self):
        self._refresh_map_canvases()
        self._refresh_table_previews()

    def _on_tab_changed(self, index):
        if _HAS_MAP_CANVAS and index in (0, 1):
            QtCore.QTimer.singleShot(50, self._refresh_map_canvases)

    def _active_canvas(self):
        if not _HAS_MAP_CANVAS:
            return None
        idx = self.preview_tabs.currentIndex()
        if idx == 0 and hasattr(self, '_map_canvas_plain'):
            return self._map_canvas_plain
        if idx == 1 and hasattr(self, '_map_canvas_bg'):
            return self._map_canvas_bg
        return None

    def keyPressEvent(self, event):
        canvas = self._active_canvas()
        key = event.key()
        arrow_keys = (
            QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right,
            QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down,
        )
        if canvas and key in arrow_keys:
            ext = canvas.extent()
            dx = ext.width() * 0.1
            dy = ext.height() * 0.1
            offsets = {
                QtCore.Qt.Key.Key_Left:  (-dx,   0),
                QtCore.Qt.Key.Key_Right: ( dx,   0),
                QtCore.Qt.Key.Key_Up:    (  0,  dy),
                QtCore.Qt.Key.Key_Down:  (  0, -dy),
            }
            ox, oy = offsets[key]
            canvas.setExtent(QgsRectangle(
                ext.xMinimum() + ox, ext.yMinimum() + oy,
                ext.xMaximum() + ox, ext.yMaximum() + oy))
            canvas.refresh()
        else:
            super().keyPressEvent(event)

    def _apply_suggested_scale(self):
        if self._suggested_scale:
            self.scale_combo.setCurrentText(f"1:{self._suggested_scale}")

    def _get_traverse_layers(self):
        layer_ids = self._pc.get("preview_layer_ids", [])
        layers = []
        for lid in layer_ids:
            layer = QgsProject.instance().mapLayer(lid)
            if layer is not None:
                layers.append(layer)
        return layers

    def _get_background_layer(self):
        name = self.background_layer_combo.currentText()
        if not name or name == self._no_background_text:
            return None
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name() == name:
                return layer
        return None

    def _traverse_extent(self, traverse_layers):
        """Combined extent of all traverse layers."""
        combined = QgsRectangle()
        for layer in traverse_layers:
            ext = layer.extent()
            if not ext.isEmpty():
                combined.combineExtentWith(ext)
        if combined.isEmpty():
            pts = self._pc.get("preview_points", [])
            if pts:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                combined = QgsRectangle(min(xs), min(ys), max(xs), max(ys))
        return combined

    def _map_extent_for_scale(self, traverse_extent):
        """Calculate map extent for the selected scale and paper size."""
        paper_key = self.paper_combo.currentText()
        scale_text = self.scale_combo.currentText().strip()
        try:
            if ":" in scale_text:
                scale_denom = int(scale_text.split(":")[1].replace(",", ""))
            else:
                scale_denom = int(scale_text.replace(",", ""))
        except (IndexError, ValueError):
            scale_denom = 1000

        w_mm, h_mm = PAPER_SIZES.get(paper_key, (297, 210))
        content_w_m = (w_mm - 2 * _MARGIN_MM) / 1000.0 * scale_denom
        content_h_m = (h_mm - 2 * _MARGIN_MM - _HEADER_MM) / 1000.0 * scale_denom

        if traverse_extent.isEmpty():
            return QgsRectangle(0, 0, content_w_m, content_h_m)

        cx = traverse_extent.center().x()
        cy = traverse_extent.center().y()
        return QgsRectangle(
            cx - content_w_m / 2, cy - content_h_m / 2,
            cx + content_w_m / 2, cy + content_h_m / 2)

    def _update_auto_scale_label(self, traverse_extent):
        if traverse_extent.isEmpty():
            self._auto_scale_label.setText("")
            self._suggested_scale = None
            self._apply_scale_btn.setEnabled(False)
            return
        paper_key = self.paper_combo.currentText()
        w_mm, h_mm = PAPER_SIZES.get(paper_key, (297, 210))
        content_w_mm = w_mm - 2 * _MARGIN_MM
        content_h_mm = h_mm - 2 * _MARGIN_MM - _HEADER_MM
        scale_x = traverse_extent.width() / max(1e-9, content_w_mm / 1000.0)
        scale_y = traverse_extent.height() / max(1e-9, content_h_mm / 1000.0)
        raw = max(scale_x, scale_y) * 1.15
        nice = [200, 500, 1000, 2500, 5000, 10000, 25000, 50000]
        suggested = next((s for s in nice if s >= raw), nice[-1])
        self._suggested_scale = suggested
        self._auto_scale_label.setText(f"推奨縮尺: 1:{suggested:,}")
        self._apply_scale_btn.setEnabled(True)
        # Auto-populate if the field is still empty
        if not self.scale_combo.currentText().strip():
            self.scale_combo.setCurrentText(f"1:{suggested}")

    def _refresh_map_canvases(self):
        if not _HAS_MAP_CANVAS:
            self._refresh_fallback_map()
            return

        paper_key = self.paper_combo.currentText()
        if hasattr(self, '_frame_plain'):
            self._frame_plain.set_paper_key(paper_key)
        if hasattr(self, '_frame_bg'):
            self._frame_bg.set_paper_key(paper_key)

        traverse_layers = self._get_traverse_layers()
        bg_layer = self._get_background_layer()
        traverse_extent = self._traverse_extent(traverse_layers)
        map_extent = self._map_extent_for_scale(traverse_extent)

        self._update_auto_scale_label(traverse_extent)

        crs = QgsProject.instance().crs()

        # Plain map — black/thin/no-fill clones (not added to QGIS project)
        plain_layers = _plain_styled_layers(traverse_layers)
        self._plain_render_layers = plain_layers  # keep reference alive
        self._map_canvas_plain.setDestinationCrs(crs)
        self._map_canvas_plain.setLayers(plain_layers)
        if not map_extent.isEmpty():
            self._map_canvas_plain.setExtent(map_extent)
        self._map_canvas_plain.refresh()

        # Map + background — traverse on top, background at bottom
        bg_layers = traverse_layers + ([bg_layer] if bg_layer else [])
        self._map_canvas_bg.setDestinationCrs(crs)
        self._map_canvas_bg.setLayers(bg_layers)
        if not map_extent.isEmpty():
            self._map_canvas_bg.setExtent(map_extent)
        self._map_canvas_bg.refresh()

    def _refresh_fallback_map(self):
        self.a4_preview.set_preview_state(
            paper_key=self.paper_combo.currentText(),
            project_name=self._pc.get("project_name", ""),
            work_name=self._pc.get("work_name", ""),
            scale_text=self.scale_combo.currentText(),
            note_text=self.note_edit.text().strip(),
            points=self._pc.get("preview_points", []),
        )

    # ---- Table previews ----

    def _refresh_table_previews(self):
        pc = self._pc
        observations = pc.get("observations", [])
        computation = pc.get("computation")
        block_entries = _normalized_block_entries(
            observations=observations,
            computation=computation,
            block_entries=pc.get("block_entries", []),
        )
        area_entries, _line_entries, _is_area_mode = _classify_block_entries(block_entries)
        if hasattr(self.preview_tabs, "setTabVisible"):
            self.preview_tabs.setTabVisible(self._calc_tab_index, bool(area_entries))
        self._refresh_nb_info_table(observations, computation, pc)
        self._refresh_notebook_table(observations, computation)
        self._refresh_calc_table_widgets(computation, observations, pc)

    def _refresh_nb_info_table(self, observations, computation, pc):
        t = self._nb_info_table
        t.setRowCount(0)
        _lbl_bg = QtGui.QBrush(QtGui.QColor("#e7eef2"))
        block_entries = _normalized_block_entries(
            observations=observations,
            computation=computation,
            block_entries=pc.get("block_entries", []),
        )
        area_entries, _line_entries, is_area_mode = _classify_block_entries(block_entries)
        exclude_lines = bool(pc.get("exclude_connecting_lines", False))
        sum_sd = sum(
            _entry_sum_sd(entry)
            for entry in block_entries
            if not _entry_is_excluded(entry, exclude_lines)
        )
        sum_hd = sum(
            _entry_sum_hd(entry)
            for entry in block_entries
            if not _entry_is_excluded(entry, exclude_lines)
        )
        area_total_m2 = sum(_entry_area_m2(entry) for entry in area_entries)
        right_label_1 = (
            self.tr("Area Total")
            if is_area_mode else self.tr("Length Total (Slope Distance)")
        )
        right_value_1 = (
            _floor_ha_str(area_total_m2) if is_area_mode and area_total_m2
            else (_fmt(sum_sd) + " m" if sum_sd else "")
        )
        right_label_2 = "" if is_area_mode else self.tr("Length Total (Horizontal Distance)")
        right_value_2 = "" if is_area_mode else (_fmt(sum_hd) + " m" if sum_hd else "")
        grid = [
            (self.tr("Project Name"), pc.get("project_name", ""), self.tr("Surveyor"), pc.get("surveyor", "")),
            (self.tr("Work Name"), pc.get("work_name", ""), self.tr("Measurement Date"), pc.get("measurement_date", "")),
            (self.tr("Fiscal Year"), pc.get("fiscal_year", ""), right_label_1, right_value_1),
            (self.tr("Operation Type"), pc.get("operation_type", ""), right_label_2, right_value_2),
        ]
        t.setRowCount(len(grid))
        for i, (k1, v1, k2, v2) in enumerate(grid):
            for j, text in enumerate((k1, v1, k2, v2)):
                item = QtWidgets.QTableWidgetItem(str(text))
                if j % 2 == 0 and text:
                    item.setBackground(_lbl_bg)
                if j % 2 == 1:
                    item.setTextAlignment(
                        QtCore.Qt.AlignmentFlag.AlignRight |
                        QtCore.Qt.AlignmentFlag.AlignVCenter)
                t.setItem(i, j, item)
        self._apply_table_default_row_height(t)
        t.updateGeometry()

    def _refresh_notebook_table(self, observations, computation):
        block_entries = _normalized_block_entries(
            observations=observations,
            computation=computation,
            block_entries=self._pc.get("block_entries", []),
        )
        exclude_lines = bool(self._pc.get("exclude_connecting_lines", False))
        self._nb_preview_sections = _build_notebook_preview_sections(
            block_entries,
            exclude_connecting_lines=exclude_lines,
            tr_label=self._preview_label,
        )
        self._fill_summary_table(
            self._nb_export_summary_table,
            _build_notebook_export_summary_rows(
                block_entries,
                exclude_connecting_lines=exclude_lines,
                tr_label=self._preview_label,
            ),
        )
        current_key = self._nb_section_combo.currentData()
        self._nb_section_combo.blockSignals(True)
        self._nb_section_combo.clear()
        for section in self._nb_preview_sections:
            self._nb_section_combo.addItem(section["label"], section["key"])
        self._nb_section_combo.blockSignals(False)
        if current_key:
            idx = self._nb_section_combo.findData(current_key)
            if idx >= 0:
                self._nb_section_combo.setCurrentIndex(idx)
        if self._nb_section_combo.count() and self._nb_section_combo.currentIndex() < 0:
            self._nb_section_combo.setCurrentIndex(0)
        self._refresh_notebook_section_detail()

    def _refresh_calc_table_widgets(self, computation, observations, pc):
        self._calc_info_table.setRowCount(0)
        self._calc_table.setRowCount(0)
        block_entries = _normalized_block_entries(
            observations=observations,
            computation=computation,
            block_entries=pc.get("block_entries", []),
        )
        area_entries, _line_entries, _is_area_mode = _classify_block_entries(block_entries)
        if not area_entries:
            self._calc_preview_sections = []
            self._fill_summary_table(self._calc_export_summary_table, [])
            self._calc_section_combo.blockSignals(True)
            self._calc_section_combo.clear()
            self._calc_section_combo.blockSignals(False)
            return
        self._calc_preview_sections = _build_calc_preview_sections(
            area_entries,
            project_name=pc.get("project_name", ""),
            work_name=pc.get("work_name", ""),
            note_text=self.note_edit.text().strip(),
            tr_label=self._preview_label,
        )
        self._fill_summary_table(
            self._calc_export_summary_table,
            _build_calc_export_summary_rows(area_entries, tr_label=self._preview_label),
        )
        current_key = self._calc_section_combo.currentData()
        self._calc_section_combo.blockSignals(True)
        self._calc_section_combo.clear()
        for section in self._calc_preview_sections:
            self._calc_section_combo.addItem(section["label"], section["key"])
        self._calc_section_combo.blockSignals(False)
        if current_key:
            idx = self._calc_section_combo.findData(current_key)
            if idx >= 0:
                self._calc_section_combo.setCurrentIndex(idx)
        if self._calc_section_combo.count() and self._calc_section_combo.currentIndex() < 0:
            self._calc_section_combo.setCurrentIndex(0)
        self._refresh_calc_section_detail()

    def _fill_summary_table(self, table, rows):
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                item = QtWidgets.QTableWidgetItem(str(value))
                if j == len(row) - 1:
                    item.setTextAlignment(
                        QtCore.Qt.AlignmentFlag.AlignRight |
                        QtCore.Qt.AlignmentFlag.AlignVCenter
                    )
                table.setItem(i, j, item)
        self._apply_table_default_row_height(table)

    @staticmethod
    def _apply_table_default_row_height(table):
        row_height = table.verticalHeader().defaultSectionSize()
        for row in range(table.rowCount()):
            table.setRowHeight(row, row_height)

    def _refresh_notebook_section_detail(self):
        t = self._notebook_table
        t.setRowCount(0)
        self._fill_summary_table(self._nb_section_summary_table, [])
        if not self._nb_preview_sections:
            return
        idx = self._nb_section_combo.currentIndex()
        if idx < 0:
            idx = 0
        section = self._nb_preview_sections[idx]
        self._fill_summary_table(
            self._nb_section_summary_table,
            [(label, value) for label, value in section.get("summary_rows", [])],
        )
        rows = section.get("rows", [])
        t.setRowCount(len(rows))
        for i, vals in enumerate(rows):
            for j, v in enumerate(vals):
                item = QtWidgets.QTableWidgetItem(str(v))
                if j >= 2 and j not in (9, 10, 11):
                    item.setTextAlignment(
                        QtCore.Qt.AlignmentFlag.AlignRight |
                        QtCore.Qt.AlignmentFlag.AlignVCenter
                    )
                t.setItem(i, j, item)
        self._apply_table_default_row_height(t)

    def _refresh_calc_section_detail(self):
        self._calc_info_table.setRowCount(0)
        self._calc_table.setRowCount(0)
        if not self._calc_preview_sections:
            return
        idx = self._calc_section_combo.currentIndex()
        if idx < 0:
            idx = 0
        section = self._calc_preview_sections[idx]
        info_grid = section["info_grid"]
        _lbl_bg = QtGui.QBrush(QtGui.QColor("#e7eef2"))
        self._calc_info_table.clearSpans()
        self._calc_info_table.setRowCount(len(info_grid))
        for i, row_data in enumerate(info_grid):
            for j, cell_text in enumerate(row_data):
                item = QtWidgets.QTableWidgetItem(str(cell_text))
                if j % 2 == 0 and not (i == 4 and j == 6):
                    item.setBackground(_lbl_bg)
                else:
                    item.setTextAlignment(
                        QtCore.Qt.AlignmentFlag.AlignRight |
                        QtCore.Qt.AlignmentFlag.AlignVCenter)
                self._calc_info_table.setItem(i, j, item)
        self._calc_info_table.setSpan(4, 1, 1, 3)
        self._calc_info_table.setSpan(4, 6, 1, 2)
        self._apply_table_default_row_height(self._calc_info_table)
        area_item = self._calc_info_table.item(4, 5)
        if area_item:
            font = area_item.font()
            font.setBold(True)
            area_item.setFont(font)

        preview_rows = section.get("rows", [])
        self._calc_table.setRowCount(len(preview_rows))
        for i, vals in enumerate(preview_rows):
            for j, v in enumerate(vals):
                item = QtWidgets.QTableWidgetItem(str(v))
                if j >= 2:
                    item.setTextAlignment(
                        QtCore.Qt.AlignmentFlag.AlignRight |
                        QtCore.Qt.AlignmentFlag.AlignVCenter)
                self._calc_table.setItem(i, j, item)
        self._apply_table_default_row_height(self._calc_table)

    # ---- Browser preview ----

    def _open_table_html_in_browser(self):
        import webbrowser
        pc = self._pc
        observations = pc.get("observations", [])
        computation = pc.get("computation")
        html_text = _build_table_html(
            project_name=pc.get("project_name", ""),
            work_name=pc.get("work_name", ""),
            scale_text=self.scale_combo.currentText(),
            paper_key="A4 縦",
            note_text=self.note_edit.text().strip(),
            observations=observations,
            computation=computation,
            fiscal_year=pc.get("fiscal_year", ""),
            surveyor=pc.get("surveyor", ""),
            measurement_date=pc.get("measurement_date", ""),
            operation_type=pc.get("operation_type", ""),
            block_entries=pc.get("block_entries", []),
            exclude_connecting_lines=bool(pc.get("exclude_connecting_lines", False)),
        )
        if self._tmp_html_path is None:
            fd, path = tempfile.mkstemp(suffix=".html", prefix="ct_table_")
            os.close(fd)
            self._tmp_html_path = path
        with open(self._tmp_html_path, "w", encoding="utf-8") as fp:
            fp.write(html_text)
        webbrowser.open(f"file:///{self._tmp_html_path.replace(os.sep, '/')}")

    def settings(self):
        background_layer_name = self.background_layer_combo.currentText()
        if background_layer_name == self._no_background_text:
            background_layer_name = ""
        return {
            "output_dir": self.output_dir_edit.text().strip(),
            "paper_size": self.paper_combo.currentText(),
            "scale": self.scale_combo.currentText(),
            "background_layer_name": background_layer_name,
            "bottom_right_note": self.note_edit.text().strip(),
            "drawing_number": self.drawing_number_edit.text().strip(),
            "label_interval": self.label_interval_spin.value(),
        }


# ---------------------------------------------------------------------------
# Layer style helpers
# ---------------------------------------------------------------------------

def _plain_styled_layers(traverse_layers):
    """Return clones of traverse layers styled for plain map (rule-based points)."""
    try:
        from qgis.core import (
            QgsMarkerSymbol, QgsLineSymbol, QgsFillSymbol,
            QgsSingleSymbolRenderer, QgsRuleBasedRenderer, QgsWkbTypes,
        )
    except ImportError:
        return list(traverse_layers or [])

    _PT_SIZE = "1.3"
    _PT_UNIT = "MM"
    _PT_OL   = "0.08"
    _PT_OL_U = "MM"

    def _pt_sym(color):
        return QgsMarkerSymbol.createSimple({
            "name": "circle",
            "size": _PT_SIZE, "size_unit": _PT_UNIT,
            "color": color,
            "outline_color": "#000000",
            "outline_width": _PT_OL, "outline_width_unit": _PT_OL_U,
        })

    result = []
    for layer in (traverse_layers or []):
        try:
            clone = layer.clone()
            gt = clone.geometryType()
            if gt == QgsWkbTypes.GeometryType.PointGeometry:
                root = QgsRuleBasedRenderer.Rule(None)

                # First station of each block (seq_index = 0): red fill
                r_first = QgsRuleBasedRenderer.Rule(_pt_sym("200,0,0,255"))
                r_first.setFilterExpression("\"seq_index\" = 0")
                root.appendChild(r_first)

                # Every 5th station within block: black fill
                r_n5 = QgsRuleBasedRenderer.Rule(_pt_sym("0,0,0,255"))
                r_n5.setFilterExpression("\"seq_index\" > 0 AND \"seq_index\" % 5 = 0")
                root.appendChild(r_n5)

                # Others: white fill
                r_else = QgsRuleBasedRenderer.Rule(_pt_sym("255,255,255,255"))
                r_else.setIsElse(True)
                root.appendChild(r_else)

                clone.setRenderer(QgsRuleBasedRenderer(root))
                clone.setLabelsEnabled(False)
            elif gt == QgsWkbTypes.GeometryType.LineGeometry:
                sym = QgsLineSymbol.createSimple({
                    "line_color": "#000000",
                    "line_width": "0.12",
                    "line_width_unit": "MM",
                })
                clone.setRenderer(QgsSingleSymbolRenderer(sym))
            elif gt == QgsWkbTypes.GeometryType.PolygonGeometry:
                sym = QgsFillSymbol.createSimple({
                    "color": "0,0,0,0",
                    "outline_color": "#000000",
                    "outline_width": "0.12",
                    "outline_width_unit": "MM",
                })
                clone.setRenderer(QgsSingleSymbolRenderer(sym))
            result.append(clone)
        except Exception:
            result.append(layer)
    return result


# ---------------------------------------------------------------------------
# PDF map export via QGIS renderer
# ---------------------------------------------------------------------------

def export_map_to_pdf(path, *, traverse_layer_ids, background_layer_name,
                      paper_key, scale_text, project_name, work_name,
                      drawing_number="", label_interval=5, preview_points=None,
                      note_text="", block_entries=None):
    """
    Render traverse layers (+ optional background) to PDF using QgsMapRendererCustomPainterJob.
    Returns (success: bool, message: str).
    """
    try:
        from qgis.PyQt.QtGui import (QPdfWriter, QPainter, QFont, QPen,
                                     QPageSize, QPageLayout)
        from qgis.PyQt.QtCore import QSizeF, QSize, QRect, QMarginsF, Qt
        from qgis.core import QgsMapSettings, QgsMapRendererCustomPainterJob
    except ImportError as e:
        return False, f"PDF出力に必要なモジュールがありません: {e}"

    # Collect layers
    traverse_layers = [
        QgsProject.instance().mapLayer(lid)
        for lid in (traverse_layer_ids or [])
        if QgsProject.instance().mapLayer(lid) is not None
    ]
    if not traverse_layers:
        return False, "トラバースレイヤーが見つかりません。先に「計算実行」を行ってください。"

    bg_layer = None
    if background_layer_name and background_layer_name != "(なし)":
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name() == background_layer_name:
                bg_layer = layer
                break

    # Compute map extent from traverse layers
    combined = QgsRectangle()
    for layer in traverse_layers:
        ext = layer.extent()
        if not ext.isEmpty():
            combined.combineExtentWith(ext)
    if combined.isEmpty():
        return False, "トラバースレイヤーの範囲が取得できません。"

    try:
        scale_denom = int(scale_text.split(":")[1])
    except (IndexError, ValueError):
        scale_denom = 1000

    w_mm, h_mm = PAPER_SIZES.get(paper_key, (297, 210))
    content_w_m = (w_mm - 2 * _MARGIN_MM) / 1000.0 * scale_denom
    content_h_m = (h_mm - 2 * _MARGIN_MM - _HEADER_MM) / 1000.0 * scale_denom
    cx, cy = combined.center().x(), combined.center().y()
    map_extent = QgsRectangle(
        cx - content_w_m / 2, cy - content_h_m / 2,
        cx + content_w_m / 2, cy + content_h_m / 2)

    # PDF writer setup
    writer = QPdfWriter(path)
    page_size = QPageSize(QSizeF(w_mm, h_mm), QPageSize.Unit.Millimeter)
    layout = QPageLayout(
        page_size, QPageLayout.Orientation.Portrait,
        QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
    writer.setPageLayout(layout)
    writer.setResolution(_PDF_DPI)

    painter = QPainter()
    if not painter.begin(writer):
        return False, "PDFライターの初期化に失敗しました。"

    mm_to_px = _PDF_DPI / 25.4
    margin_px = int(_MARGIN_MM * mm_to_px)
    header_px = int(_HEADER_MM * mm_to_px)

    vp = painter.viewport()
    page_w_px = vp.width()
    page_h_px = vp.height()
    map_rect = QRect(
        margin_px, margin_px + header_px,
        page_w_px - 2 * margin_px,
        page_h_px - 2 * margin_px - header_px)

    # Page 1: plain traverse — labels on, start point red
    plain_traverse = _plain_styled_layers(traverse_layers)
    _render_pdf_page(painter, plain_traverse, map_extent, map_rect,
                     margin_px, header_px, page_w_px, page_h_px,
                     project_name, work_name, drawing_number, scale_denom,
                     mm_to_px, preview_points=preview_points,
                     label_interval=label_interval,
                     show_labels=True, block_entries=block_entries)

    # Page 2: with background — labels off (background provides context)
    if bg_layer is not None:
        writer.newPage()
        bg_layers = traverse_layers + [bg_layer]
        _render_pdf_page(painter, bg_layers, map_extent, map_rect,
                         margin_px, header_px, page_w_px, page_h_px,
                         project_name, work_name, drawing_number, scale_denom,
                         mm_to_px, preview_points=preview_points,
                         label_interval=label_interval,
                         show_labels=False, block_entries=block_entries)

    painter.end()
    return True, path


def _pt_px(pt):
    """Convert point size to pixels at _PDF_DPI."""
    return max(8, int(pt * _PDF_DPI / 72.0))


def _draw_title_block(painter, left, top, width, height, mm_to_px,
                      project_name, work_name, drawing_number, scale_denom):
    """Draw 2-row × 2-column title block."""
    from qgis.PyQt.QtGui import QFont, QPen, QColor
    from qgis.PyQt.QtCore import QRect, Qt

    thin = QPen(QColor(0, 0, 0))
    thin.setWidth(1)
    painter.setPen(thin)

    row_h = height / 2.0
    mid_x = left + width / 2.0

    lbl_w = 10 * mm_to_px    # 名称/事業名 label column
    r_lbl_w = 14 * mm_to_px  # 図面番号/縮尺 label column

    # Grid lines
    painter.drawLine(int(left), int(top + row_h), int(left + width), int(top + row_h))
    painter.drawLine(int(mid_x), int(top), int(mid_x), int(top + height))
    painter.drawLine(int(left + lbl_w), int(top), int(left + lbl_w), int(top + height))
    painter.drawLine(int(mid_x + r_lbl_w), int(top), int(mid_x + r_lbl_w), int(top + height))

    lbl_font = QFont("Meiryo")
    lbl_font.setPixelSize(_pt_px(7))
    val_font = QFont("Meiryo")
    val_font.setPixelSize(_pt_px(8))

    def draw_lbl(x, y, w, h, text):
        painter.setFont(lbl_font)
        painter.setPen(QPen(QColor(80, 80, 80)))
        painter.drawText(QRect(int(x) + 3, int(y) + 2, int(w) - 4, int(h) - 4),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    def draw_val(x, y, w, h, text):
        painter.setFont(val_font)
        painter.setPen(QPen(QColor(0, 0, 0)))
        painter.drawText(QRect(int(x) + 4, int(y) + 2, int(w) - 6, int(h) - 4),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    # Row 0: 名称 | project_name | 図面番号 | drawing_number
    val1_w = mid_x - left - lbl_w
    val2_w = left + width - mid_x - r_lbl_w
    draw_lbl(left, top, lbl_w, row_h, "名称")
    draw_val(left + lbl_w, top, val1_w, row_h, project_name or "")
    draw_lbl(mid_x, top, r_lbl_w, row_h, "図面番号")
    draw_val(mid_x + r_lbl_w, top, val2_w, row_h, drawing_number or "")

    # Row 1: 事業名 | work_name | 縮尺 | scale
    draw_lbl(left, top + row_h, lbl_w, row_h, "事業名")
    draw_val(left + lbl_w, top + row_h, val1_w, row_h, work_name or "")
    draw_lbl(mid_x, top + row_h, r_lbl_w, row_h, "縮尺")
    draw_val(mid_x + r_lbl_w, top + row_h, val2_w, row_h,
             f"1:{scale_denom:,}" if scale_denom else "")


def _draw_north_arrow(painter, left, top, width, height, rotation_deg=0.0):
    """Draw a split north arrow (left=black, right=white) with 'N' label."""
    from qgis.PyQt.QtGui import QFont, QPen, QBrush, QColor, QPolygonF
    from qgis.PyQt.QtCore import QPointF, QRectF, Qt

    cx = left + width / 2.0
    cy = top + height / 2.0
    size = min(width, height) * 0.42

    painter.save()
    painter.setRenderHint(painter.RenderHint.Antialiasing)
    painter.translate(cx, cy)
    if rotation_deg != 0.0:
        painter.rotate(rotation_deg)

    hw = size * 0.26
    tip_y = -size * 0.58
    base_y = size * 0.50
    notch_y = size * 0.12

    left_tri = QPolygonF([QPointF(0, tip_y), QPointF(-hw, base_y), QPointF(0, notch_y)])
    right_tri = QPolygonF([QPointF(0, tip_y), QPointF(hw, base_y), QPointF(0, notch_y)])

    thin = QPen(QColor(0, 0, 0))
    thin.setWidthF(0.8)
    painter.setPen(thin)
    painter.setBrush(QBrush(QColor(0, 0, 0)))
    painter.drawPolygon(left_tri)
    painter.setBrush(QBrush(QColor(255, 255, 255)))
    painter.drawPolygon(right_tri)

    # "N" label above tip
    fsz = max(10, int(size * 0.32))
    f = QFont("Arial")
    f.setPixelSize(fsz)
    f.setBold(True)
    painter.setFont(f)
    painter.setPen(QPen(QColor(0, 0, 0)))
    painter.drawText(QRectF(-size * 0.3, tip_y - fsz - 2, size * 0.6, fsz + 2),
                     Qt.AlignmentFlag.AlignCenter, "N")
    painter.restore()


def _draw_scale_bar(painter, left, top, width, height, scale_denom, mm_to_px):
    """Draw alternating black/white scale bar with distance labels."""
    from qgis.PyQt.QtGui import QFont, QPen, QColor
    from qgis.PyQt.QtCore import QRect, Qt

    # Compute nice segment distance
    target_bar_mm = width / mm_to_px * 0.78
    target_total_m = target_bar_mm * scale_denom / 1000.0
    nice = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000]
    seg_m = min(nice, key=lambda v: abs(v - target_total_m / 4.0))
    n_segs = 4
    seg_px = int(seg_m * 1000.0 / scale_denom * mm_to_px)
    total_px = seg_px * n_segs

    bar_h = max(5, int(height * 0.24))
    bar_y = top + int(height * 0.18)
    bar_x = left + (width - total_px) // 2

    thin = QPen(QColor(0, 0, 0))
    thin.setWidth(1)

    for i in range(n_segs):
        sx = bar_x + i * seg_px
        sr = QRect(sx, bar_y, seg_px, bar_h)
        if i % 2 == 0:
            painter.fillRect(sr, QColor(0, 0, 0))
        else:
            painter.fillRect(sr, QColor(255, 255, 255))
        painter.setPen(thin)
        painter.setBrush(QtWidgets.QApplication.palette().base())
        painter.drawRect(sr)

    fsz = _pt_px(6)
    f = QFont("Arial")
    f.setPixelSize(fsz)
    painter.setFont(f)
    painter.setPen(QPen(QColor(0, 0, 0)))
    lbl_y = bar_y + bar_h + 2

    for i in range(n_segs + 1):
        lx = bar_x + i * seg_px
        txt = str(int(i * seg_m))
        painter.drawText(QRect(lx - 22, lbl_y, 44, fsz + 4),
                         Qt.AlignmentFlag.AlignCenter, txt)

    painter.drawText(QRect(bar_x + total_px + 4, lbl_y, 28, fsz + 4),
                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "m")


def _draw_station_labels(painter, preview_points, extent, map_rect,
                         label_interval, mm_to_px):
    """Draw BP, every N-th point, and EP text labels on the map (dots handled by QGIS renderer)."""
    from qgis.PyQt.QtGui import QFont, QPen, QBrush, QColor, QPainterPath
    from qgis.PyQt.QtCore import QRect, Qt

    if not preview_points:
        return
    if extent.isEmpty() or extent.width() <= 0 or extent.height() <= 0:
        return

    n = len(preview_points)

    # Support (x,y), (x,y,name), (x,y,name,seq), (x,y,name,seq,is_closing_target) tuples.
    def _pt(p):
        x, y = p[0], p[1]
        name = str(p[2]) if len(p) > 2 else ""
        seq = int(p[3]) if len(p) > 3 else -1
        closing = bool(p[4]) if len(p) > 4 else False
        return (x, y, name, seq, closing)

    pts = [_pt(p) for p in preview_points]

    # Detect closed (area) traverse: last point coincides with first
    # (suppress the duplicate label at the closure endpoint)
    is_area_traverse = False
    if n >= 2:
        dx = pts[-1][0] - pts[0][0]
        dy = pts[-1][1] - pts[0][1]
        is_area_traverse = math.sqrt(dx * dx + dy * dy) < 0.1

    fsz = _pt_px(7)
    f = QFont("Meiryo")
    f.setPixelSize(fsz)
    f.setBold(True)
    painter.setFont(f)

    def to_px(mx, my):
        rx = (mx - extent.xMinimum()) / extent.width()
        ry = 1.0 - (my - extent.yMinimum()) / extent.height()
        return (map_rect.left() + rx * map_rect.width(),
                map_rect.top() + ry * map_rect.height())

    painter.save()
    painter.setClipRect(map_rect)

    for i, (mx, my, station_name, seq_idx, is_closing_target) in enumerate(pts):
        # Suppress closure endpoint (same position as first, already labeled)
        if i == n - 1 and is_area_traverse:
            continue

        # Label only where dots are colored: seq_index=0 (red) or seq_index%N=0 (black)
        if seq_idx < 0:
            continue
        if not (seq_idx == 0 or (seq_idx > 0 and seq_idx % label_interval == 0)):
            continue

        # Suppress label at closure target stations (e.g. station 64, 84)
        if is_closing_target:
            continue

        px, py = to_px(mx, my)
        ipx, ipy = int(px), int(py)

        label = station_name

        # Label — outline buffer (white halo) + black fill (dots handled by QGIS renderer)
        lx, ly = ipx + 6, ipy - 2
        path = QPainterPath()
        path.addText(lx, ly, f, label)
        outline_pen = QPen(QColor(255, 255, 255))
        outline_pen.setWidth(max(2, int(fsz * 0.20)))
        outline_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        outline_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(outline_pen)
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawPath(path)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0)))
        painter.drawPath(path)

    painter.restore()


def _draw_block_name_labels(painter, block_entries, extent, map_rect):
    """Draw block name inside each area block using pointOnSurface + collision nudge."""
    from qgis.PyQt.QtGui import QFont, QPen, QBrush, QColor, QPainterPath, QFontMetrics
    from qgis.PyQt.QtCore import Qt
    try:
        from qgis.core import QgsGeometry, QgsPointXY
    except ImportError:
        QgsGeometry = None

    if not block_entries or extent.isEmpty() or extent.width() <= 0 or extent.height() <= 0:
        return

    def to_px(mx, my):
        rx = (mx - extent.xMinimum()) / extent.width()
        ry = 1.0 - (my - extent.yMinimum()) / extent.height()
        return (map_rect.left() + rx * map_rect.width(),
                map_rect.top() + ry * map_rect.height())

    fsz = _pt_px(8)
    f = QFont("Meiryo")
    f.setPixelSize(fsz)
    f.setBold(True)
    fm = QFontMetrics(f)

    # ① 各エリアブロックのラベル位置を pointOnSurface で求める
    candidates = []
    for entry in block_entries:
        kind = str(entry.get("block_kind", "") or "").strip().lower()
        comp = entry.get("computation")
        is_area = kind == "area" or (comp is not None and comp.latest_closure() is not None and kind != "branch")
        if not is_area or comp is None:
            continue
        block_name = str(entry.get("block_name") or entry.get("block_id") or "")
        if not block_name:
            continue

        coords = [comp.start_coordinate] + [
            (leg.corrected_target_coordinate or leg.target_coordinate)
            for leg in comp.leg_results
        ]
        if len(coords) < 3:
            continue

        cx, cy = None, None
        if QgsGeometry is not None:
            try:
                ring = [QgsPointXY(c.x, c.y) for c in coords]
                if ring[-1] != ring[0]:
                    ring.append(ring[0])
                geom = QgsGeometry.fromPolygonXY([ring])
                pt = geom.pointOnSurface().asPoint()
                cx, cy = pt.x(), pt.y()
            except Exception:  # nosec B110
                pass
        if cx is None:
            cx = sum(c.x for c in coords) / len(coords)
            cy = sum(c.y for c in coords) / len(coords)

        px, py = to_px(cx, cy)
        tw = fm.horizontalAdvance(block_name)
        th = fsz
        candidates.append({"name": block_name, "px": px, "py": py, "tw": tw, "th": th})

    # ② ラベル矩形の衝突を検出して y 方向にずらす
    placed = []  # [(left, top, right, bottom), ...]
    for item in candidates:
        lx = item["px"] - item["tw"] / 2
        ty = item["py"] - item["th"] / 2
        rx = lx + item["tw"]
        by = ty + item["th"]
        # 既存ラベルと重なる限りずらし続ける（最大5回）
        for _ in range(5):
            overlap = False
            for (ol, ot, or_, ob) in placed:
                if lx < or_ and rx > ol and ty < ob and by > ot:
                    shift = (ob - ty) + 4
                    ty += shift
                    by += shift
                    overlap = True
                    break
            if not overlap:
                break
        item["draw_px"] = lx
        item["draw_py"] = by  # addText の y は baseline
        placed.append((lx, ty, rx, by))

    # ③ 描画
    painter.save()
    painter.setClipRect(map_rect)
    painter.setFont(f)

    for item in candidates:
        path = QPainterPath()
        path.addText(int(item["draw_px"]), int(item["draw_py"]), f, item["name"])

        outline_pen = QPen(QColor(255, 255, 255))
        outline_pen.setWidth(max(3, int(fsz * 0.35)))
        outline_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        outline_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(outline_pen)
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawPath(path)

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 80, 180)))
        painter.drawPath(path)

    painter.restore()


def _draw_branch_lines(painter, block_entries, extent, map_rect):
    """Draw branch block lines in yellow/amber color over the map."""
    from qgis.PyQt.QtGui import QPen, QColor
    from qgis.PyQt.QtCore import Qt

    if not block_entries or extent.isEmpty() or extent.width() <= 0 or extent.height() <= 0:
        return

    has_branch = any(
        str(e.get("block_kind", "")).strip().lower() == "branch"
        for e in block_entries
    )
    if not has_branch:
        return

    def to_px(mx, my):
        rx = (mx - extent.xMinimum()) / extent.width()
        ry = 1.0 - (my - extent.yMinimum()) / extent.height()
        return (map_rect.left() + rx * map_rect.width(),
                map_rect.top() + ry * map_rect.height())

    pen = QPen(QColor(220, 160, 0))
    pen.setWidth(max(2, int(_PDF_DPI / 25.4 * 0.35)))
    pen.setStyle(Qt.PenStyle.DashLine)

    painter.save()
    painter.setClipRect(map_rect)
    painter.setPen(pen)

    for entry in block_entries:
        if str(entry.get("block_kind", "")).strip().lower() != "branch":
            continue
        comp = entry.get("computation")
        if comp is None:
            continue
        coords = [comp.start_coordinate] + [
            (leg.corrected_target_coordinate or leg.target_coordinate)
            for leg in comp.leg_results
        ]
        for i in range(len(coords) - 1):
            ax, ay = to_px(coords[i].x, coords[i].y)
            bx, by = to_px(coords[i + 1].x, coords[i + 1].y)
            painter.drawLine(int(ax), int(ay), int(bx), int(by))

    painter.restore()


def _draw_branch_legend(painter, block_entries, map_rect, mm_to_px):
    """Draw branch legend in the lower-left corner of the map area."""
    from qgis.PyQt.QtGui import QFont, QPen, QBrush, QColor
    from qgis.PyQt.QtCore import QRect, Qt

    if not block_entries:
        return
    has_branch = any(
        str(e.get("block_kind", "")).strip().lower() == "branch"
        for e in block_entries
    )
    if not has_branch:
        return

    fsz = _pt_px(7)
    f = QFont("Meiryo")
    f.setPixelSize(fsz)
    margin = int(4 * mm_to_px)
    box_size = fsz
    text = "接続線（Branch）として除外"

    fm = QtGui.QFontMetrics(f)
    text_w = fm.horizontalAdvance(text)
    total_w = box_size + int(2 * mm_to_px) + text_w + margin * 2
    total_h = box_size + margin * 2

    lx = map_rect.left() + margin
    ly = map_rect.bottom() - total_h - margin

    # Background
    painter.save()
    painter.setPen(QPen(QColor(180, 180, 180)))
    painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
    painter.drawRect(QRect(lx, ly, total_w, total_h))

    # Yellow square (■)
    sq_x = lx + margin
    sq_y = ly + margin
    painter.setPen(QPen(QColor(180, 130, 0)))
    painter.setBrush(QBrush(QColor(220, 160, 0)))
    painter.drawRect(QRect(sq_x, sq_y, box_size, box_size))

    # Text
    painter.setFont(f)
    painter.setPen(QPen(QColor(0, 0, 0)))
    tx = sq_x + box_size + int(2 * mm_to_px)
    ty = sq_y + box_size
    painter.drawText(tx, ty, text)

    painter.restore()


def _render_pdf_page(painter, layers, extent, map_rect,
                     margin_px, header_px, page_width_px, page_height_px,
                     project_name, work_name, drawing_number, scale_denom, mm_to_px,
                     preview_points=None, label_interval=5, map_rotation=0.0,
                     show_labels=True, block_entries=None):
    """Render one PDF page: structured header (title/north arrow/scale bar) + QGIS map."""
    from qgis.PyQt.QtGui import QPen, QColor
    from qgis.PyQt.QtCore import QRect, QSize, Qt
    from qgis.core import QgsMapSettings, QgsMapRendererCustomPainterJob

    content_left = margin_px
    content_top = margin_px
    content_w = page_width_px - 2 * margin_px
    content_h = page_height_px - 2 * margin_px
    content_rect = QRect(content_left, content_top, content_w, content_h)

    # ── QGIS map render ───────────────────────────────────────────────────────
    ms = QgsMapSettings()
    ms.setDestinationCrs(QgsProject.instance().crs())
    ms.setExtent(extent)
    ms.setOutputSize(QSize(map_rect.width(), map_rect.height()))
    ms.setOutputDpi(_PDF_DPI)
    ms.setBackgroundColor(QColor(255, 255, 255))
    ms.setLayers(layers)

    painter.save()
    painter.setClipRect(map_rect)
    painter.translate(map_rect.topLeft())
    job = QgsMapRendererCustomPainterJob(ms, painter)
    job.start()
    job.waitForFinished()
    painter.restore()

    # ── Station labels ────────────────────────────────────────────────────────
    if show_labels and preview_points:
        _draw_station_labels(painter, preview_points, extent, map_rect,
                             label_interval, mm_to_px)

    # ── Block overlays (branch lines, block name labels, legend) ─────────────
    if block_entries:
        _draw_branch_lines(painter, block_entries, extent, map_rect)
        if show_labels:
            _draw_block_name_labels(painter, block_entries, extent, map_rect)
        _draw_branch_legend(painter, block_entries, map_rect, mm_to_px)

    # ── Header zone ───────────────────────────────────────────────────────────
    # Layout: left 60% = title block | right 40%: left 65% = scale bar | right 35% = north arrow
    title_w = int(content_w * 0.60)
    right_w = content_w - title_w
    right_x = content_left + title_w
    scalebar_w = int(right_w * 0.65)
    north_x = right_x + scalebar_w
    north_w = right_w - scalebar_w

    _draw_title_block(painter,
                      left=float(content_left), top=float(content_top),
                      width=float(title_w), height=float(header_px),
                      mm_to_px=mm_to_px,
                      project_name=project_name, work_name=work_name,
                      drawing_number=drawing_number, scale_denom=scale_denom)

    _draw_scale_bar(painter,
                    left=right_x, top=content_top,
                    width=scalebar_w, height=header_px,
                    scale_denom=scale_denom, mm_to_px=mm_to_px)

    _draw_north_arrow(painter,
                      left=north_x, top=content_top,
                      width=north_w, height=header_px,
                      rotation_deg=map_rotation)

    # ── Borders and dividers ──────────────────────────────────────────────────
    outer_pen = QPen(QColor(0, 0, 0))
    outer_pen.setWidth(2)
    thin_pen = QPen(QColor(0, 0, 0))
    thin_pen.setWidth(1)

    painter.setBrush(Qt.BrushStyle.NoBrush)

    # Outer frame
    painter.setPen(outer_pen)
    painter.drawRect(content_rect)

    # Header / map separator
    painter.setPen(thin_pen)
    painter.drawLine(content_left, content_top + header_px,
                     content_left + content_w, content_top + header_px)

    # Title | right zone divider
    painter.drawLine(right_x, content_top, right_x, content_top + header_px)

    # Scale bar | north arrow divider
    painter.drawLine(north_x, content_top, north_x, content_top + header_px)


# ---------------------------------------------------------------------------
# HTML section builders (for 測量野帳 and 面積計算簿)
# ---------------------------------------------------------------------------


def _section_notebook_html(observations, paper_key, computation=None, *,
                           project_name="", work_name="", fiscal_year="",
                           surveyor="", measurement_date="", operation_type="",
                           block_entries=None, exclude_connecting_lines=False):
    he = html_module.escape
    block_entries = _normalized_block_entries(
        observations=observations,
        computation=computation,
        block_entries=block_entries,
    )
    if not block_entries:
        return "<p>観測データなし</p>"
    area_entries, _line_entries, is_area_mode = _classify_block_entries(block_entries)
    area_entries = sorted(area_entries, key=lambda e: str(e.get("block_name") or e.get("block_id") or ""))
    sum_sd = sum(
        _entry_sum_sd(entry)
        for entry in block_entries
        if not _entry_is_excluded(entry, exclude_connecting_lines)
    )
    sum_hd = sum(
        _entry_sum_hd(entry)
        for entry in block_entries
        if not _entry_is_excluded(entry, exclude_connecting_lines)
    )
    area_total_m2 = sum(_entry_area_m2(entry) for entry in area_entries)
    def _info_html(right_label_1, right_value_1, right_label_2="", right_value_2=""):
        return f"""<table style="margin-bottom:6px;font-size:11px;width:100%">
<tr>
  <th style="text-align:left;width:6em">事業名</th>
  <td colspan="3">{he(project_name)}</td>
  <th style="width:8em">測定者</th>
  <td colspan="3">{he(surveyor)}</td>
</tr>
<tr>
  <th style="text-align:left">測量名</th>
  <td colspan="3">{he(work_name)}</td>
  <th>測定日時</th>
  <td colspan="3">{he(measurement_date)}</td>
</tr>
<tr>
  <th style="text-align:left">事業年度</th>
  <td colspan="3">{he(fiscal_year)}</td>
  <th>{he(right_label_1)}</th>
  <td colspan="3" style="text-align:right">{he(right_value_1)}</td>
</tr>
<tr>
  <th style="text-align:left">事業種別</th>
  <td colspan="3">{he(operation_type)}</td>
  <th>{he(right_label_2)}</th>
  <td colspan="3" style="text-align:right">{he(right_value_2)}</td>
</tr>
</table>"""

    overview_html = _info_html(
        "面積合計" if is_area_mode else "延長合計（斜距離）",
        (
            _floor_ha_str(area_total_m2)
            if is_area_mode and area_total_m2
            else (_fmt(sum_sd) + " m" if sum_sd else "")
        ),
        "" if is_area_mode else "延長合計（水平距離）",
        "" if is_area_mode else (_fmt(sum_hd) + " m" if sum_hd else ""),
    )

    def _area_block_html(entry):
        block_name = entry.get("block_name") or entry.get("block_id") or ""
        area_m2 = _entry_area_m2(entry)
        slope_total = _entry_sum_sd(entry)
        return (
            f"<h4 style=\"margin:10px 0 6px\">{he(str(block_name))}</h4>"
            + _info_html(
                "面積合計",
                _floor_ha_str(area_m2) if area_m2 else "",
                "斜距離合計",
                _fmt_d(slope_total) + " m" if slope_total else "",
            )
            + table_open
            + _rows_html(entry, add_subtotal=False)
            + table_close
        )

    def _rows_html(entry, *, add_subtotal):
        block_name = entry.get("block_name") or entry.get("block_id") or ""
        leg_map = {}
        comp = entry.get("computation")
        if comp is not None:
            for leg in comp.leg_results:
                leg_map[(leg.from_station, leg.target_station)] = leg
        excluded = _entry_is_excluded(entry, exclude_connecting_lines)
        rows_html = ""
        for obs in entry.get("observations", []):
            dz = _height_diff_from_obs(obs)
            leg = leg_map.get((obs.from_station, obs.target_station))
            dx = leg.delta_x if leg else None
            dy = leg.delta_y if leg else None
            note_parts = [str(obs.note or "").strip()]
            if excluded:
                note_parts.append("計算から除外")
            rows_html += (
                f"<tr>"
                f"<td>{he(str(obs.from_station))}</td>"
                f"<td>{he(str(obs.target_station))}</td>"
                f"<td style='text-align:right'>{_fmt_d(obs.azimuth)}</td>"
                f"<td style='text-align:right'>{_fmt_d(obs.inclination)}</td>"
                f"<td style='text-align:right'>{_fmt_d(obs.slope_distance)}</td>"
                f"<td style='text-align:right'>{_fmt_d(obs.horizontal_distance)}</td>"
                f"<td style='text-align:right'>{_fmt_d(dz)}</td>"
                f"<td style='text-align:right'>{_fmt_d(dx)}</td>"
                f"<td style='text-align:right'>{_fmt_d(dy)}</td>"
                f"<td>{he(str(obs.connect_to or ''))}</td>"
                f"<td>{he(str(obs.close_to or ''))}</td>"
                f"<td>{he(' / '.join(p for p in note_parts if p))}</td>"
                f"</tr>\n"
            )
        if add_subtotal:
            subtotal_sd = "" if excluded else _fmt_d(_entry_sum_sd(entry))
            subtotal_hd = "" if excluded else _fmt_d(_entry_sum_hd(entry))
            subtotal_note = str(block_name)
            if excluded:
                subtotal_note = f"{subtotal_note} / 計算から除外"
            rows_html += (
                f"<tr style='font-weight:bold;background:#f0f4f8;'>"
                f"<td colspan='4'>延長計</td>"
                f"<td style='text-align:right'>{subtotal_sd}</td>"
                f"<td style='text-align:right'>{subtotal_hd}</td>"
                f"<td></td><td></td><td></td><td></td><td></td>"
                f"<td>{he(subtotal_note)}</td>"
                f"</tr>\n"
            )
        return rows_html

    table_open = """<table>
<thead>
<tr>
  <th rowspan="2">視準点</th><th rowspan="2">測定点</th>
  <th rowspan="2">方位角</th><th rowspan="2">高低角</th>
  <th>斜距離</th><th>水平距離</th><th>高低差</th>
  <th>△X</th><th>△Y</th>
  <th rowspan="2">接続先</th><th rowspan="2">閉合先</th><th rowspan="2">備考</th>
</tr>
<tr><th>m</th><th>m</th><th>m</th><th>m</th><th>m</th></tr>
</thead>
<tbody>"""
    table_close = "</tbody></table>"

    sections = []
    intro_parts = ["<h2>測量野帳</h2>", overview_html]
    if area_entries:
        intro_parts.append("<h3>面積測量野帳</h3>")
        intro_parts.append(_area_summary_html(area_entries, heading="面積概要"))
        intro_parts.append(_area_block_html(area_entries[0]))
        sections.append(f"<section class='notebook-intro'>{''.join(intro_parts)}</section>")
        for entry in area_entries[1:]:
            sections.append(
                f"<section class='notebook-block notebook-page-break'>{_area_block_html(entry)}</section>"
            )
    else:
        sections.append(f"<section class='notebook-intro'>{''.join(intro_parts)}</section>")
    line_target_entries = [entry for entry in block_entries if entry not in area_entries]
    if line_target_entries:
        sum_sd = sum(
            _entry_sum_sd(entry)
            for entry in line_target_entries
            if not _entry_is_excluded(entry, exclude_connecting_lines)
        )
        sum_hd = sum(
            _entry_sum_hd(entry)
            for entry in line_target_entries
            if not _entry_is_excluded(entry, exclude_connecting_lines)
        )
        info_html = _info_html(
            "延長合計（斜距離）",
            _fmt_d(sum_sd) + " m" if sum_sd else "",
            "延長合計（水平距離）",
            _fmt_d(sum_hd) + " m" if sum_hd else "",
        )

        rows_html = ""
        for entry in line_target_entries:
            block_name = entry.get("block_name") or entry.get("block_id") or ""
            rows_html += (
                f"<tr style='background:#eef3f8;font-weight:bold'>"
                f"<td colspan='12'>{he(str(block_name))}</td></tr>\n"
            )
            rows_html += _rows_html(entry, add_subtotal=True)
        sections.append(
            f"""<section class='notebook-block notebook-page-break'><h3>延長野帳</h3>{info_html}
{table_open}{rows_html}{table_close}</section>"""
        )

    return "".join(sections) if sections else "<p>観測データなし</p>"


def _section_area_calc_html(*, observations, computation, project_name,
                            work_name, note_text, paper_key, block_entries=None):
    he = html_module.escape
    block_entries = _normalized_block_entries(
        observations=observations,
        computation=computation,
        block_entries=block_entries,
    )
    area_entries, _line_entries, _is_area_mode = _classify_block_entries(block_entries)
    if not area_entries:
        return "<p>計算データなし（先に「計算実行」を行ってください）</p>"
    area_entries = sorted(area_entries, key=lambda e: str(e.get("block_name") or e.get("block_id") or ""))
    sections = []
    intro_parts = ["<h3>面積計算簿（倍横距法）</h3>", _area_summary_html(area_entries, heading="面積概要")]

    def _calc_block_html(entry):
        observations = entry.get("observations", [])
        computation = entry.get("computation")
        obs_map = {(o.from_station, o.target_station): o for o in (observations or [])}
        dmd_rows_list, totals = _dmd_rows(computation, obs_map)

        closure = computation.latest_closure()
        err_dist = closure.error_distance if closure else 0.0
        perimeter = computation.corrected_perimeter() or computation.total_horizontal_distance()
        ratio_val = computation.closure_ratio()
        ratio_str = (f"1/{int(round(ratio_val))}"
                     if ratio_val is not None and math.isfinite(ratio_val) else "")
        ratio_pct = f"{err_dist / perimeter * 100:.3f}%" if perimeter > 0 else ""
        area_m2 = computation.corrected_area() or 0.0
        area_ha = area_m2 / 10000.0

        all_coords = [computation.start_coordinate] + [
            (leg.corrected_target_coordinate or leg.target_coordinate)
            for leg in computation.leg_results]
        xs = [c.x for c in all_coords]
        ys = [c.y for c in all_coords]
        sum_dx = sum(leg.delta_x for leg in computation.leg_results)
        sum_dy = sum(leg.delta_y for leg in computation.leg_results)
        sum_hd = sum(leg.horizontal_distance for leg in computation.leg_results)
        block_name = entry.get("block_name") or entry.get("block_id") or ""

        header_html = f"""
<h4 style="margin:10px 0 6px">{he(str(block_name))}</h4>
<table style="margin-bottom:6px;font-size:11px;">
<tr>
  <th style="text-align:left;width:6em">事業名</th>
  <td colspan="3">{he(project_name)}</td>
  <th>X累計</th><td style="text-align:right">{_fmt_d(sum_dy)}</td>
  <th>測点数</th><td style="text-align:right">{len(computation.leg_results)} 箇所</td>
  <th>x最大値</th><td style="text-align:right">{_fmt_d(max(ys))}</td>
</tr>
<tr>
  <th style="text-align:left">測量名</th>
  <td colspan="3">{he(work_name)}</td>
  <th>Y累計</th><td style="text-align:right">{_fmt_d(sum_dx)}</td>
  <th>閉合差</th><td style="text-align:right">{_fmt_d(err_dist)}</td>
  <th>x最小値</th><td style="text-align:right">{_fmt_d(min(ys))}</td>
</tr>
<tr>
  <th style="text-align:left">測定者</th><td colspan="3"></td>
  <th>水距累計</th><td style="text-align:right">{_fmt_d(sum_hd)}</td>
  <th>精度(/)</th><td style="text-align:right">{he(ratio_str)}</td>
  <th>y最大値</th><td style="text-align:right">{_fmt_d(max(xs))}</td>
</tr>
<tr>
  <th style="text-align:left">測定日時</th><td colspan="3"></td>
  <th>高度累計</th><td style="text-align:right">{_fmt_d(totals.get("sum_dz",0))}</td>
  <th>精度(%)</th><td style="text-align:right">{he(ratio_pct)}</td>
  <th>y最小値</th><td style="text-align:right">{_fmt_d(min(xs))}</td>
</tr>
<tr>
  <th style="text-align:left">備考</th>
  <td colspan="3">{he(note_text)}</td>
  <td colspan="2"></td>
  <th>面積</th>
  <td style="text-align:right;font-weight:bold">{_floor_ha_str(area_m2)}</td>
  <td colspan="2"></td>
</tr>
</table>"""

        rows_html = ""
        for r in dmd_rows_list:
            rows_html += (
                f"<tr>"
                f"<td>{he(r['from'])}</td><td>{he(r['to'])}</td>"
                f"<td style='text-align:right'>{r['az']}</td>"
                f"<td style='text-align:right'>{r['inc']}</td>"
                f"<td style='text-align:right'>{r['sd']}</td>"
                f"<td style='text-align:right'>{r['hd']}</td>"
                f"<td style='text-align:right'>{r['dz']}</td>"
                f"<td style='text-align:right'>{r['y_coord']}</td>"
                f"<td style='text-align:right'>{r['x_coord']}</td>"
                f"<td style='text-align:right'>{r['z_coord']}</td>"
                f"<td style='text-align:right'>{r['dmd']}</td>"
                f"<td style='text-align:right'>{r['lat']}</td>"
                f"<td style='text-align:right'>{r['double_area']}</td>"
                f"</tr>\n"
            )
        rows_html += (
            f"<tr style='font-weight:bold;background:#f0f4f8;'>"
            f"<td colspan='2'>合計</td><td></td><td></td>"
            f"<td style='text-align:right'>{_fmt_d(totals.get('sum_sd'))}</td>"
            f"<td style='text-align:right'>{_fmt_d(totals.get('sum_hd'))}</td>"
            f"<td style='text-align:right'>{_fmt_d(totals.get('sum_dz'))}</td>"
            f"<td></td><td></td><td></td><td></td><td></td>"
            f"<td style='text-align:right'>{_fmt_d(totals.get('sum_double_area'))}</td>"
            f"</tr>\n"
            f"<tr style='font-weight:bold;background:#fff3cd;'>"
            f"<td colspan='12' style='text-align:right'>面積 = |Σ倍面積| / 2 =</td>"
            f"<td style='text-align:right'>{area_m2:.4f} m²</td>"
            f"</tr>\n"
        )

        table_html = f"""
<table>
<thead>
<tr>
  <th rowspan="2">視準点</th><th rowspan="2">測定点</th>
  <th rowspan="2">方位角</th><th rowspan="2">高低角</th>
  <th>斜距離</th><th>水平距離</th><th>高低差</th>
  <th>Y</th><th>X</th><th>Z</th>
  <th rowspan="2">倍横距</th><th rowspan="2">緯距</th><th rowspan="2">倍面積</th>
</tr>
<tr><th>m</th><th>m</th><th>m</th><th>m</th><th>m</th><th>m</th></tr>
</thead>
<tbody>{rows_html}</tbody>
</table>"""
        return header_html + table_html

    intro_parts.append(_calc_block_html(area_entries[0]))
    sections.append(f"<section class='notebook-intro'>{''.join(intro_parts)}</section>")
    for entry in area_entries[1:]:
        sections.append(
            f"<section class='notebook-block notebook-page-break'>{_calc_block_html(entry)}</section>"
        )

    return "".join(sections)


def _build_table_html(*, project_name, work_name, scale_text, paper_key,
                      note_text, observations, computation,
                      fiscal_year="", surveyor="", measurement_date="", operation_type="",
                      block_entries=None, exclude_connecting_lines=False):
    """Full HTML with 測量野帳 + 面積計算簿 tabs for browser preview/print."""
    he = html_module.escape
    page_css = _CSS_PAGE_SIZE.get(paper_key, "A4 landscape")
    block_entries = _normalized_block_entries(
        observations=observations,
        computation=computation,
        block_entries=block_entries,
    )
    area_entries, _line_entries, _is_area_mode = _classify_block_entries(block_entries)

    nb = _section_notebook_html(
        observations, paper_key, computation,
        project_name=project_name, work_name=work_name,
        fiscal_year=fiscal_year, surveyor=surveyor,
        measurement_date=measurement_date, operation_type=operation_type,
        block_entries=block_entries,
        exclude_connecting_lines=exclude_connecting_lines,
    )
    calc = ""
    if area_entries:
        calc = _section_area_calc_html(
            observations=observations, computation=computation,
            project_name=project_name, work_name=work_name,
            note_text=note_text, paper_key=paper_key,
            block_entries=block_entries,
        )

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<title>{he(project_name)} – 計算書類</title>
<style>
body{{font-family:'Yu Gothic UI','Meiryo',sans-serif;margin:0;background:#f3f6fa;}}
header{{padding:10px 16px;border-bottom:1px solid #d6dbe1;background:#fff;}}
.tabs{{display:flex;gap:6px;padding:8px 14px;background:#fff;border-bottom:1px solid #d6dbe1;}}
.tab-btn{{border:1px solid #d6dbe1;background:#fff;border-radius:6px;
          padding:6px 12px;cursor:pointer;font-size:13px;}}
.tab-btn.active{{border-color:#d8112a;color:#d8112a;font-weight:700;}}
.tab-panel{{display:none;padding:16px 20px;background:#fff;}}
.tab-panel.active{{display:block;}}
table{{width:100%;border-collapse:collapse;font-size:11px;margin-bottom:8px;}}
th,td{{border:1px solid #bbc;padding:4px 6px;}}
th{{background:#edf2f7;text-align:center;white-space:nowrap;}}
.notebook-block{{break-inside:avoid-page;page-break-inside:avoid;}}
h3,h4{{break-after:avoid-page;page-break-after:avoid;}}
@media print{{
  @page{{size:{page_css};margin:12mm;}}
  header,.tabs{{display:none;}}
  .tab-panel{{display:block!important;page-break-after:always;}}
  .tab-panel:last-child{{page-break-after:avoid;}}
  .notebook-block{{break-inside:avoid-page;page-break-inside:avoid;}}
  .notebook-page-break{{break-before:page;page-break-before:always;}}
  body{{background:#fff;}}
}}
</style></head><body>
<header>
  <strong>{he(project_name)} / {he(work_name)}</strong>
  <span style="margin-left:12px;color:#555;">縮尺 {he(scale_text)} / {he(paper_key)}</span>
</header>
<nav class="tabs">
  <button class="tab-btn" onclick="openTab('nb')">測量野帳</button>
  {('<button class="tab-btn" onclick="openTab(\'calc\')">面積計算簿</button>' if area_entries else '')}
</nav>
<div id="nb" class="tab-panel">{nb}</div>
{(f'<div id="calc" class="tab-panel">{calc}</div>' if area_entries else '')}
<script>
function openTab(id){{
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  var p=document.getElementById(id); if(p)p.classList.add('active');
  var b=document.querySelector('.tab-btn[onclick*="'+id+'"]'); if(b)b.classList.add('active');
}}
(function(){{document.querySelector('.tab-btn').click();}})();
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# DMD calculation
# ---------------------------------------------------------------------------

def _dmd_rows(computation, obs_map):
    rows = []
    totals = {"sum_sd": 0.0, "sum_hd": 0.0, "sum_dz": 0.0, "sum_double_area": 0.0}
    legs = computation.leg_results
    if not legs:
        return rows, totals

    deps = [leg.corrected_delta_x if leg.corrected_delta_x is not None
            else leg.delta_x for leg in legs]
    lats = [leg.corrected_delta_y if leg.corrected_delta_y is not None
            else leg.delta_y for leg in legs]

    dmds = []
    if deps:
        dmds.append(deps[0])
        for i in range(1, len(deps)):
            dmds.append(dmds[-1] + deps[i - 1] + deps[i])

    z_acc = 0.0
    for i, leg in enumerate(legs):
        obs = obs_map.get((leg.from_station, leg.target_station))
        dz = _height_diff_from_obs(obs)
        if dz is not None:
            z_acc += dz

        coord = leg.corrected_target_coordinate or leg.target_coordinate
        dmd_val = dmds[i] if i < len(dmds) else 0.0
        lat_val = lats[i] if i < len(lats) else 0.0
        double_area = dmd_val * lat_val

        sd = obs.slope_distance if obs else None
        az = obs.azimuth if obs else leg.azimuth_degrees
        inc = obs.inclination if obs else None

        totals["sum_sd"] = totals.get("sum_sd", 0.0) + (sd or 0.0)
        totals["sum_hd"] += leg.horizontal_distance
        totals["sum_dz"] += dz if dz is not None else 0.0
        totals["sum_double_area"] += double_area

        rows.append({
            "from": leg.from_station, "to": leg.target_station,
            "az": _fmt_d(az), "inc": _fmt_d(inc),
            "sd": _fmt_d(sd), "hd": _fmt_d(leg.horizontal_distance),
            "dz": _fmt_d(dz),
            "y_coord": _fmt_d(coord.x), "x_coord": _fmt_d(coord.y),
            "z_coord": _fmt_d(z_acc),
            "dmd": _fmt_d(dmd_val), "lat": _fmt_d(lat_val),
            "double_area": _fmt_d(double_area),
        })
    return rows, totals


def _height_diff_from_obs(obs):
    if obs is None:
        return None
    if obs.slope_distance is not None and obs.inclination is not None:
        return obs.slope_distance * math.sin(math.radians(obs.inclination))
    return None


# ---------------------------------------------------------------------------
# Main export bundle function
# ---------------------------------------------------------------------------

def generate_export_bundle(
    *,
    output_dir,
    project_name,
    work_name,
    scale_text,
    paper_key,
    background_layer_name,
    traverse_layer_ids,
    observations,
    computation,
    block_entries,
    summary_values,
    bottom_right_note,
    drawing_number="",
    label_interval=5,
    preview_points=None,
    fiscal_year="",
    surveyor="",
    measurement_date="",
    operation_type="",
    exclude_connecting_lines=False,
):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_base = _safe_name(project_name or "compass_traverse")

    output_files = []
    messages = []

    # 1: PDF for 平面図 (± 背景)
    pdf_path = os.path.join(output_dir, f"{safe_base}_{timestamp}_map.pdf")
    ok, msg = export_map_to_pdf(
        pdf_path,
        traverse_layer_ids=traverse_layer_ids,
        background_layer_name=background_layer_name,
        paper_key=paper_key,
        scale_text=scale_text,
        project_name=project_name,
        work_name=work_name,
        drawing_number=drawing_number,
        label_interval=label_interval,
        preview_points=preview_points,
        note_text=bottom_right_note,
        block_entries=block_entries,
    )
    if ok:
        output_files.append(pdf_path)
    else:
        messages.append(f"PDF出力スキップ: {msg}")

    # 2: HTML for 測量野帳 + 面積計算簿 — always A4縦
    html_path = os.path.join(output_dir, f"{safe_base}_{timestamp}_tables.html")
    html_text = _build_table_html(
        project_name=project_name,
        work_name=work_name,
        scale_text=scale_text,
        paper_key="A4 縦",
        note_text=bottom_right_note,
        observations=observations,
        computation=computation,
        block_entries=block_entries,
        fiscal_year=fiscal_year,
        surveyor=surveyor,
        measurement_date=measurement_date,
        operation_type=operation_type,
        exclude_connecting_lines=exclude_connecting_lines,
    )
    with open(html_path, "w", encoding="utf-8") as fp:
        fp.write(html_text)
    output_files.append(html_path)

    return output_files, messages


# ---------------------------------------------------------------------------
# Legacy helpers (kept for compatibility)
# ---------------------------------------------------------------------------

def map_canvas_to_data_uri(iface):
    if iface is None:
        return ""
    canvas = iface.mapCanvas()
    if canvas is None:
        return ""
    pixmap = canvas.grab()
    if pixmap.isNull():
        return ""
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    encoded = base64.b64encode(bytes(buf.data())).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def available_background_layers():
    """Return names of all raster layers in the current QGIS project."""
    layers = []
    for layer in QgsProject.instance().mapLayers().values():
        if layer is None:
            continue
        try:
            from qgis.core import Qgis
            is_raster = layer.type() == Qgis.LayerType.Raster
        except AttributeError:
            try:
                is_raster = layer.type() == QgsMapLayerType.RasterLayer
            except AttributeError:
                is_raster = False
        if is_raster:
            layers.append(layer.name())
    return sorted(set(layers))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt(v):
    if v is None or v == "":
        return ""
    try:
        return f"{float(v):.3f}"
    except Exception:
        return str(v)


def _fmt_d(v, decimals=3):
    if v is None or v == "":
        return ""
    try:
        return f"{float(v):.{decimals}f}"
    except Exception:
        return str(v)


def _safe_name(text):
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(text))
    return safe.strip("_") or "export"


def _normalized_block_entries(*, observations=None, computation=None, block_entries=None):
    entries = list(block_entries or [])
    if entries:
        return entries
    if observations and computation:
        return [{
            "block_id": observations[0].block_id or "blk_0",
            "block_name": observations[0].block_id or "blk_0",
            "block_kind": "area" if computation.latest_closure() is not None else "route",
            "observations": list(observations),
            "computation": computation,
        }]
    return []


def _classify_block_entries(block_entries):
    area_entries = []
    line_entries = []
    for entry in block_entries:
        kind = str(entry.get("block_kind", "") or "").strip().lower()
        comp = entry.get("computation")
        is_area = kind == "area" or (comp is not None and comp.latest_closure() is not None and kind != "branch")
        if is_area:
            area_entries.append(entry)
        else:
            line_entries.append(entry)
    return area_entries, line_entries, bool(area_entries and not line_entries)


def _entry_is_excluded(entry, exclude_connecting_lines):
    return bool(exclude_connecting_lines and str(entry.get("block_kind", "")).strip().lower() == "branch")


def _entry_sum_sd(entry):
    return sum(
        (obs.slope_distance or 0.0)
        for obs in entry.get("observations", [])
        if obs.slope_distance is not None
    )


def _entry_sum_hd(entry):
    comp = entry.get("computation")
    if comp is None:
        return 0.0
    return sum(leg.horizontal_distance for leg in comp.leg_results)


def _entry_area_m2(entry):
    comp = entry.get("computation")
    if comp is None:
        return 0.0
    return comp.corrected_area() or 0.0


def _area_summary_html(area_entries, *, heading="面積一覧"):
    if not area_entries:
        return ""
    rows = []
    total_m2 = 0.0
    sorted_entries = sorted(
        area_entries,
        key=lambda e: str(e.get("block_name") or e.get("block_id") or ""),
    )
    for entry in sorted_entries:
        area_m2 = _entry_area_m2(entry)
        total_m2 += area_m2
        rows.append(
            (
                str(entry.get("block_name") or entry.get("block_id") or ""),
                area_m2,
            )
        )

    html_rows = [
        "<table>"
        "<thead><tr><th>区画</th><th>面積(m²)</th><th>面積(ha)</th></tr></thead><tbody>"
    ]
    for block_name, area_m2 in rows:
        html_rows.append(
            "<tr>"
            f"<td>{html_module.escape(block_name)}</td>"
            f"<td style='text-align:right'>{_fmt_d(area_m2, 4)}</td>"
            f"<td style='text-align:right'>{_floor_ha_str(area_m2)}</td>"
            "</tr>"
        )
    html_rows.append(
        "<tr style='font-weight:bold;background:#f0f4f8;'>"
        "<td>合計</td>"
        f"<td style='text-align:right'>{_fmt_d(total_m2, 4)}</td>"
        f"<td style='text-align:right'>{_floor_ha_str(total_m2)}</td>"
        "</tr>"
    )
    html_rows.append("</tbody></table>")
    return f"<h4>{html_module.escape(heading)}</h4>{''.join(html_rows)}"


def _build_notebook_export_summary_rows(
    block_entries,
    *,
    exclude_connecting_lines=False,
    tr_label=None,
):
    tr_label = tr_label or (lambda text: text)
    area_entries, line_entries, _is_area_mode = _classify_block_entries(block_entries)
    rows = []
    area_total_m2 = 0.0
    sorted_area_entries = sorted(
        area_entries,
        key=lambda e: str(e.get("block_name") or e.get("block_id") or ""),
    )
    for entry in sorted_area_entries:
        area_m2 = _entry_area_m2(entry)
        area_total_m2 += area_m2
        rows.append((
            tr_label("Area"),
            str(entry.get("block_name") or entry.get("block_id") or ""),
            f"{_fmt_d(area_m2, 4)} m² / {_floor_ha_str(area_m2)}",
        ))
    if area_entries:
        rows.append((
            tr_label("Area"),
            tr_label("Total"),
            f"{_fmt_d(area_total_m2, 4)} m² / {_floor_ha_str(area_total_m2)}",
        ))

    line_total_sd = 0.0
    line_total_hd = 0.0
    sorted_line_entries = sorted(
        line_entries,
        key=lambda e: str(e.get("block_name") or e.get("block_id") or ""),
    )
    for entry in sorted_line_entries:
        if _entry_is_excluded(entry, exclude_connecting_lines):
            continue
        sd = _entry_sum_sd(entry)
        hd = _entry_sum_hd(entry)
        line_total_sd += sd
        line_total_hd += hd
        rows.append((
            tr_label("Length"),
            str(entry.get("block_name") or entry.get("block_id") or ""),
            f"{tr_label('Slope')} {_fmt_d(sd)} m / {tr_label('Horizontal')} {_fmt_d(hd)} m",
        ))
    if line_entries:
        rows.append((
            tr_label("Length"),
            tr_label("Total"),
            f"{tr_label('Slope')} {_fmt_d(line_total_sd)} m / {tr_label('Horizontal')} {_fmt_d(line_total_hd)} m",
        ))
    return rows


def _build_notebook_preview_sections(
    block_entries,
    *,
    exclude_connecting_lines=False,
    tr_label=None,
):
    tr_label = tr_label or (lambda text: text)
    sections = []
    sorted_entries = sorted(
        block_entries,
        key=lambda e: str(e.get("block_name") or e.get("block_id") or ""),
    )
    for entry in sorted_entries:
        obs_list = entry.get("observations", [])
        comp = entry.get("computation")
        leg_map = {}
        if comp:
            for leg in comp.leg_results:
                leg_map[(leg.from_station, leg.target_station)] = leg
        name = str(entry.get("block_name") or entry.get("block_id") or "")
        kind = str(entry.get("block_kind", "") or "").strip().lower()
        excluded = _entry_is_excluded(entry, exclude_connecting_lines)

        summary_rows = [(tr_label("Category"), name)]
        if kind == "branch" and excluded:
            summary_rows.append((tr_label("Handling"), tr_label("Excluded from calculation")))
        if kind == "area" or (comp is not None and comp.latest_closure() is not None and kind != "branch"):
            area_m2 = _entry_area_m2(entry)
            summary_rows.append((tr_label("Area"), f"{_fmt_d(area_m2, 4)} m²"))
            summary_rows.append((tr_label("Area (ha)"), _floor_ha_str(area_m2)))
        else:
            summary_rows.append((tr_label("Slope Distance Total"), "" if excluded else f"{_fmt_d(_entry_sum_sd(entry))} m"))
            summary_rows.append((tr_label("Horizontal Distance Total"), "" if excluded else f"{_fmt_d(_entry_sum_hd(entry))} m"))

        rows = []
        for obs in obs_list:
            leg = leg_map.get((obs.from_station, obs.target_station))
            dz = _height_diff_from_obs(obs)
            note_parts = [str(obs.note or "").strip()]
            if excluded:
                note_parts.append(tr_label("Excluded from calculation"))
            rows.append([
                obs.from_station, obs.target_station,
                _fmt(obs.azimuth), _fmt(obs.inclination),
                _fmt(obs.slope_distance), _fmt(obs.horizontal_distance),
                _fmt(dz),
                _fmt(leg.delta_x if leg else None),
                _fmt(leg.delta_y if leg else None),
                obs.connect_to or "", obs.close_to or "",
                " / ".join(part for part in note_parts if part),
            ])
        if kind not in ("area",) and not ((comp is not None and comp.latest_closure() is not None and kind != "branch")):
            subtotal_note = name
            if excluded:
                subtotal_note = f"{subtotal_note} / {tr_label('Excluded from calculation')}"
            rows.append([
                tr_label("Length Total"), "", "", "",
                "" if excluded else _fmt(_entry_sum_sd(entry)),
                "" if excluded else _fmt(_entry_sum_hd(entry)),
                "", "", "", "", "", subtotal_note,
            ])

        sections.append({
            "key": entry.get("block_id") or name,
            "label": name,
            "summary_rows": summary_rows,
            "rows": rows,
        })
    return sections


def _build_calc_export_summary_rows(area_entries, *, tr_label=None):
    tr_label = tr_label or (lambda text: text)
    rows = []
    total_m2 = 0.0
    sorted_entries = sorted(
        area_entries,
        key=lambda e: str(e.get("block_name") or e.get("block_id") or ""),
    )
    for entry in sorted_entries:
        area_m2 = _entry_area_m2(entry)
        total_m2 += area_m2
        rows.append((
            str(entry.get("block_name") or entry.get("block_id") or ""),
            _fmt_d(area_m2, 4),
            _floor_ha_str(area_m2),
        ))
    rows.append((tr_label("Total"), _fmt_d(total_m2, 4), _floor_ha_str(total_m2)))
    return rows


def _build_calc_preview_sections(
    area_entries,
    *,
    project_name="",
    work_name="",
    note_text="",
    tr_label=None,
):
    tr_label = tr_label or (lambda text: text)
    sections = []
    sorted_entries = sorted(
        area_entries,
        key=lambda e: str(e.get("block_name") or e.get("block_id") or ""),
    )
    for entry in sorted_entries:
        observations = entry.get("observations", [])
        computation = entry.get("computation")
        if computation is None:
            continue
        obs_map = {(o.from_station, o.target_station): o for o in (observations or [])}
        dmd_rows_list, totals = _dmd_rows(computation, obs_map)
        closure = computation.latest_closure()
        err_dist = closure.error_distance if closure else 0.0
        perimeter = computation.corrected_perimeter() or computation.total_horizontal_distance()
        ratio_val = computation.closure_ratio()
        ratio_str = (
            f"1/{int(round(ratio_val))}"
            if ratio_val is not None and math.isfinite(ratio_val) else ""
        )
        ratio_pct = f"{err_dist / perimeter * 100:.3f}%" if perimeter > 0 else ""
        area_m2 = _entry_area_m2(entry)
        legs = computation.leg_results
        all_coords = [computation.start_coordinate] + [
            (leg.corrected_target_coordinate or leg.target_coordinate) for leg in legs
        ]
        coord_xs = [c.x for c in all_coords]
        coord_ys = [c.y for c in all_coords]
        sum_dx = sum(leg.delta_x for leg in legs)
        sum_dy = sum(leg.delta_y for leg in legs)
        sum_hd = sum(leg.horizontal_distance for leg in legs)
        sum_dz = totals.get("sum_dz", 0.0)
        info_grid = [
            (tr_label("Project Name"), project_name,
             tr_label("X Total"), _fmt(sum_dy),
             tr_label("Stations"), f"{len(legs)} {tr_label('points')}",
             tr_label("X Max"), _fmt(max(coord_ys) if coord_ys else None)),
            (tr_label("Work Name"), work_name,
             tr_label("Y Total"), _fmt(sum_dx),
             tr_label("Closure Error"), _fmt(err_dist),
             tr_label("X Min"), _fmt(min(coord_ys) if coord_ys else None)),
            (tr_label("Surveyor"), "",
             tr_label("Horizontal Total"), _fmt(sum_hd),
             tr_label("Precision (/)"), ratio_str,
             tr_label("Y Max"), _fmt(max(coord_xs) if coord_xs else None)),
            (tr_label("Measurement Date"), "",
             tr_label("Elevation Total"), _fmt(sum_dz),
             tr_label("Precision (%)"), ratio_pct,
             tr_label("Y Min"), _fmt(min(coord_xs) if coord_xs else None)),
            (tr_label("Notes"), note_text,
             "", "",
             tr_label("Area"), _floor_ha_str(area_m2),
             "", ""),
        ]
        rows = []
        for r in dmd_rows_list:
            rows.append([
                r["from"], r["to"], r["az"], r["inc"],
                r["sd"], r["hd"], r["dz"],
                r["y_coord"], r["x_coord"], r["z_coord"],
                r["dmd"], r["lat"], r["double_area"],
            ])
        sections.append({
            "key": entry.get("block_id") or entry.get("block_name") or "",
            "label": str(entry.get("block_name") or entry.get("block_id") or ""),
            "info_grid": info_grid,
            "rows": rows,
        })
    return sections
