import itertools
import math
import time
from collections import OrderedDict, Iterable
from collections.abc import Iterable
from typing import Optional

from AnyQt.QtCore import (
    Qt, QAbstractItemModel, QByteArray, QBuffer, QIODevice,
    QSize)
from AnyQt.QtGui import QColor, QBrush, QIcon
from AnyQt.QtWidgets import QGraphicsScene, QTableView, QMessageBox

from orangewidget.io import PngFormat
from orangewidget.utils import getdeepattr

__all__ = ["Report",
           "bool_str", "colored_square",
           "plural", "plural_w",
           "clip_string", "clipped_list",
           "get_html_img", "get_html_section", "get_html_subsection",
           "list_legend",
           "render_items", "render_items_vert"]


def try_(func, default=None):
    """Try return the result of func, else return default."""
    try:
        return func()
    except Exception:
        return default


class Report:
    """
    A class that adds report-related methods to the widget.
    """
    report_html = ""
    name = ""

    # Report view. The canvas framework will override this when it needs to
    # route reports to a specific window.
    # `friend class WidgetsScheme`
    __report_view = None  # type: Optional[Callable[[], OWReport]]

    def _get_designated_report_view(self):
        # OWReport is a Report
        from orangewidget.report.owreport import OWReport
        if self.__report_view is not None:
            return self.__report_view()
        else:
            return OWReport.get_instance()

    def show_report(self):
        """
        Raise the report window.
        """
        self.create_report_html()
        from orangewidget.report.owreport import HAVE_REPORT

        report = self._get_designated_report_view()
        if not HAVE_REPORT and not report.have_report_warning_shown:
            QMessageBox.critical(
                None, "Missing Component",
                "Orange can not display reports, because your installation "
                "contains neither WebEngine nor WebKit.\n\n"
                "If you installed Orange with conda or pip, try using another "
                "PyQt distribution. "
                "If you installed Orange with a standard installer, please "
                "report this bug."
            )
            report.have_report_warning_shown = True

        # Should really have a signal `report_ready` or similar to decouple
        # the implementations.
        report.make_report(self)
        report.show()
        report.raise_()

    def get_widget_name_extension(self):
        """
        Return the text that is added to the section name in the report.

        For instance, the Distribution widget adds the name of the attribute
        whose distribution is shown.

        :return: str or None
        """
        return None

    def create_report_html(self):
        """ Start a new section in report and call :obj:`send_report` method
        to add content."""
        self.report_html = '<section class="section">'
        self.report_html += get_html_section(self.name)
        self.report_html += '<div class="content">\n'
        self.send_report()
        self.report_html += '</div></section>\n\n'

    @staticmethod
    def _fix_args(name, items):
        if items is None:
            return "", name
        else:
            return name, items

    def report_items(self, name, items=None):
        """
        Add a sequence of pairs or an `OrderedDict` as a HTML list to report.

        The first argument, `name` can be omitted.

        :param name: report section name (can be omitted)
        :type name: str or tuple or OrderedDict
        :param items: a sequence of items
        :type items: list or tuple or OrderedDict
        """
        name, items = self._fix_args(name, items)
        self.report_name(name)
        self.report_html += render_items(items)

    def report_name(self, name):
        """ Add a section name to the report"""
        if name != "":
            self.report_html += get_html_subsection(name)

    def report_plot(self, name=None, plot=None):
        """
        Add a plot to the report.

        Both arguments can be omitted.

        - `report_plot("graph name", self.plotView)` reports plot
            `self.plotView` with name `"graph name"`
        - `report_plot(self.plotView) reports plot without name
        - `report_plot()` reports plot stored in attribute whose name is
            taken from `self.graph_name`
        - `report_plot("graph name")` reports plot stored in attribute
            whose name is taken from `self.graph_name`

        :param name: report section name (can be omitted)
        :type name: str or tuple or OrderedDict
        :param plot: plot widget
        :type plot:
            QGraphicsScene or pyqtgraph.PlotItem or pyqtgraph.PlotWidget
            or pyqtgraph.GraphicsWidget. If omitted, the name of the
            attribute storing the graph is taken from `self.graph_name`
        """
        if not (isinstance(name, str) and plot is None):
            name, plot = self._fix_args(name, plot)

        from pyqtgraph import PlotWidget, PlotItem, GraphicsWidget, GraphicsView
        try:
            from orangewidget.utils.webview import WebviewWidget
        except ImportError:
            WebviewWidget = None

        self.report_name(name)
        if plot is None:
            plot = getdeepattr(self, self.graph_name)
        if isinstance(plot, (QGraphicsScene, PlotItem)):
            self.report_html += get_html_img(plot)
        elif isinstance(plot, PlotWidget):
            self.report_html += get_html_img(plot.plotItem)
        elif isinstance(plot, GraphicsWidget):
            self.report_html += get_html_img(plot.scene())
        elif isinstance(plot, GraphicsView):
            self.report_html += get_html_img(plot)
        elif WebviewWidget is not None and isinstance(plot, WebviewWidget):
            try:
                svg = plot.svg()
            except (IndexError, ValueError):
                svg = plot.html()
            self.report_html += svg

    # noinspection PyBroadException
    def report_table(self, name, table=None, header_rows=0, header_columns=0,
                     num_format=None):
        """
        Add content of a table to the report.

        The method accepts different kinds of two-dimensional data, including
        Qt's views and models.

        The first argument, `name` can be omitted if other arguments (except
        `table`) are passed as keyword arguments.

        :param name: name of the section
        :type name: str
        :param table: table to be reported
        :type table:
            QAbstractItemModel or QStandardItemModel or two-dimensional list or
            any object with method `model()` that returns one of the above
        :param header_rows: the number of rows that are marked as header rows
        :type header_rows: int
        :param header_columns:
            the number of columns that are marked as header columns
        :type header_columns: int
        :param num_format: numeric format, e.g. `{:.3}`
        """
        row_limit = 100
        name, table = self._fix_args(name, table)
        join = "".join

        def report_abstract_model(model, view=None):
            columns = [i for i in range(model.columnCount())
                       if not view or not view.isColumnHidden(i)]
            rows = [i for i in range(model.rowCount())
                    if not view or not view.isRowHidden(i)]

            has_horizontal_header = (try_(lambda: not view.horizontalHeader().isHidden()) or
                                     try_(lambda: not view.header().isHidden()))
            has_vertical_header = try_(lambda: not view.verticalHeader().isHidden())
            if view is not None:
                opts = view.viewOptions()
                decoration_size = QSize(opts.decorationSize)
            else:
                decoration_size = QSize(16, 16)

            def item_html(row, col):
                def data(role=Qt.DisplayRole,
                         orientation=Qt.Horizontal if row is None else Qt.Vertical):
                    if row is None or col is None:
                        return model.headerData(col if row is None else row,
                                                orientation, role)
                    data_ = model.data(model.index(row, col), role)
                    if isinstance(data_, QGraphicsScene):
                        data_ = get_html_img(
                            data_,
                            max_height=view.verticalHeader().defaultSectionSize()
                        )
                    elif isinstance(data_, QIcon):
                        data_ = get_icon_html(data_, size=decoration_size)
                    return data_

                selected = (view.selectionModel().isSelected(model.index(row, col))
                            if view and row is not None and col is not None else False)

                fgcolor = data(Qt.ForegroundRole)
                fgcolor = (QBrush(fgcolor).color().name()
                           if isinstance(fgcolor, (QBrush, QColor)) else 'black')

                bgcolor = data(Qt.BackgroundRole)
                bgcolor = (QBrush(bgcolor).color().name()
                           if isinstance(bgcolor, (QBrush, QColor)) else 'transparent')
                if bgcolor.lower() == '#ffffff':
                    bgcolor = 'transparent'

                font = data(Qt.FontRole)
                weight = 'font-weight: bold;' if font and font.bold() else ''

                alignment = data(Qt.TextAlignmentRole) or Qt.AlignLeft
                halign = ('left' if alignment & Qt.AlignLeft else
                          'right' if alignment & Qt.AlignRight else
                          'center')
                valign = ('top' if alignment & Qt.AlignTop else
                          'bottom' if alignment & Qt.AlignBottom else
                          'middle')
                return ('<{tag} style="'
                        'color:{fgcolor};'
                        'border:{border};'
                        'background:{bgcolor};'
                        '{weight}'
                        'text-align:{halign};'
                        'vertical-align:{valign};">{decoration}'
                        '{text}</{tag}>'.format(
                            tag='th' if row is None or col is None else 'td',
                            border='1px solid black' if selected else '0',
                            decoration=data(role=Qt.DecorationRole) or '',
                            text=data() or '', weight=weight, fgcolor=fgcolor,
                            bgcolor=bgcolor, halign=halign, valign=valign))

            stream = []

            if has_horizontal_header:
                stream.append('<tr>')
                if has_vertical_header:
                    stream.append('<th></th>')
                stream.extend(item_html(None, col) for col in columns)
                stream.append('</tr>')

            for row in rows[:row_limit]:
                stream.append('<tr>')
                if has_vertical_header:
                    stream.append(item_html(row, None))
                stream.extend(item_html(row, col) for col in columns)
                stream.append('</tr>')

            return ''.join(stream)

        if num_format:
            def fmtnum(s):
                try:
                    return num_format.format(float(s))
                except:
                    return s
        else:
            def fmtnum(s):
                return s

        def report_list(data,
                        header_rows=header_rows, header_columns=header_columns):
            cells = ["<td>{}</td>", "<th>{}</th>"]
            return join("  <tr>\n    {}</tr>\n".format(
                join(cells[rowi < header_rows or coli < header_columns]
                     .format(fmtnum(elm)) for coli, elm in enumerate(row))
            ) for rowi, row in zip(range(row_limit + header_rows), data))

        self.report_name(name)
        n_hidden_rows, n_cols = 0, 1
        if isinstance(table, QTableView):
            body = report_abstract_model(table.model(), table)
            n_hidden_rows = table.model().rowCount() - row_limit
            n_cols = table.model().columnCount()
        elif isinstance(table, QAbstractItemModel):
            body = report_abstract_model(table)
            n_hidden_rows = table.rowCount() - row_limit
            n_cols = table.columnCount()
        elif isinstance(table, Iterable):
            body = report_list(table, header_rows, header_columns)
            table = list(table)
            n_hidden_rows = len(table) - row_limit
            if len(table) and isinstance(table[0], Iterable):
                n_cols = len(table[0])
        else:
            body = None

        if n_hidden_rows > 0:
            body += """<tr><th></th><td colspan='{}'><b>+ {} more</b></td></tr>
            """.format(n_cols, n_hidden_rows)

        if body:
            self.report_html += "<table>\n" + body + "</table>"

    # noinspection PyBroadException
    def report_list(self, name, data=None, limit=1000):
        """
        Add a list to the report.

        The method accepts different kinds of one-dimensional data, including
        Qt's views and models.

        The first argument, `name` can be omitted.

        :param name: name of the section
        :type name: str
        :param data: table to be reported
        :type data:
            QAbstractItemModel or any object with method `model()` that
            returns QAbstractItemModel
        :param limit: the maximal number of reported items (default: 1000)
        :type limit: int
        """
        name, data = self._fix_args(name, data)

        def report_abstract_model(model):
            content = (model.data(model.index(row, 0))
                       for row in range(model.rowCount()))
            return clipped_list(content, limit, less_lookups=True)

        self.report_name(name)
        try:
            model = data.model()
        except:
            model = None
        if isinstance(model, QAbstractItemModel):
            txt = report_abstract_model(model)
        else:
            txt = ""
        self.report_html += txt

    def report_paragraph(self, name, text=None):
        """
        Add a paragraph to the report.

        The first argument, `name` can be omitted.

        :param name: name of the section
        :type name: str
        :param text: text of the paragraph
        :type text: str
        """
        name, text = self._fix_args(name, text)
        self.report_name(name)
        self.report_html += "<p>{}</p>".format(text)

    def report_caption(self, text):
        """
        Add caption to the report.
        """
        self.report_html += "<p class='caption'>{}</p>".format(text)

    def report_raw(self, name, html=None):
        """
        Add raw HTML to the report.
        """
        name, html = self._fix_args(name, html)
        self.report_name(name)
        self.report_html += html

    def combo_value(self, combo):
        """
        Add the value of a combo box to the report.

        The methods assumes that the combo box was created by
        :obj:`Orange.widget.gui.comboBox`. If the value of the combo equals
        `combo.emptyString`, this function returns None.
        """
        text = combo.currentText()
        if text != combo.emptyString:
            return text


def plural(s, number, suffix="s"):
    """
    Insert the number into the string, and make plural where marked, if needed.

    The string should use `{number}` to mark the place(s) where the number is
    inserted and `{s}` where an "s" needs to be added if the number is not 1.

    For instance, a string could be "I saw {number} dog{s} in the forest".

    Argument `suffix` can be used for some forms or irregular plural, like:

        plural("I saw {number} fox{s} in the forest", x, "es")
        plural("I say {number} child{s} in the forest", x, "ren")

    :param s: string
    :type s: str
    :param number: number
    :type number: int
    :param suffix: the suffix to use; default is "s"
    :type suffix: str
    :rtype: str
    """
    return s.format(number=number, s=suffix if number % 100 != 1 else "")


def plural_w(s, number, suffix="s", capitalize=False):
    """
    Insert the number into the string, and make plural where marked, if needed.

    If the number is smaller or equal to ten, a word is used instead of a
    numeric representation.

    The string should use `{number}` to mark the place(s) where the number is
    inserted and `{s}` where an "s" needs to be added if the number is not 1.

    For instance, a string could be "I saw {number} dog{s} in the forest".

    Argument `suffix` can be used for some forms or irregular plural, like:

        plural("I saw {number} fox{s} in the forest", x, "es")
        plural("I say {number} child{s} in the forest", x, "ren")

    :param s: string
    :type s: str
    :param number: number
    :type number: int
    :param suffix: the suffix to use; default is "s"
    :type suffix: str
    :rtype: str
    """
    numbers = ("zero", "one", "two", "three", "four", "five", "six", "seven",
               "nine", "ten")
    number_str = numbers[number] if number < len(numbers) else str(number)
    if capitalize:
        number_str = number_str.capitalize()
    return s.format(number=number_str, s=suffix if number % 100 != 1 else "")


def bool_str(v):
    """Convert a boolean to a string."""
    return "Yes" if v else "No"


def clip_string(s, limit=1000, sep=None):
    """
    Clip a string at a given character and add "..." if the string was clipped.

    If a separator is specified, the string is not clipped at the given limit
    but after the last occurence of the separator below the limit.

    :param s: string to clip
    :type s: str
    :param limit: number of characters to retain (including "...")
    :type limit: int
    :param sep: separator
    :type sep: str
    :rtype: str
    """
    if len(s) < limit:
        return s
    s = s[:limit - 3]
    if sep is None:
        return s
    sep_pos = s.rfind(sep)
    if sep_pos == -1:
        return s
    return s[:sep_pos + len(sep)] + "..."


def clipped_list(items, limit=1000, less_lookups=False, total_min=10, total=""):
    """
    Return a clipped comma-separated representation of the list.

    If `less_lookups` is `True`, clipping will use a generator across the first
    `(limit + 2) // 3` items only, which suffices even if each item is only a
    single character long. This is useful in case when retrieving items is
    expensive, while it is generally slower.

    If there are at least `total_lim` items, and argument `total` is present,
    the string `total.format(len(items))` is added to the end of string.
    Argument `total` can be, for instance `"(total: {} variables)"`.

    If `total` is given, `s` cannot be a generator.

    :param items: list
    :type items: list or another iterable object
    :param limit: number of characters to retain (including "...")
    :type limit: int
    :param total_min: the minimal number of items that triggers adding `total`
    :type total_min: int
    :param total: the string that is added if `len(items) >= total_min`
    :type total: str
    :param less_lookups: minimize the number of lookups
    :type less_lookups: bool
    :return:
    """
    if less_lookups:
        s = ", ".join(itertools.islice(items, (limit + 2) // 3))
    else:
        s = ", ".join(items)
    s = clip_string(s, limit, ", ")
    if total and len(items) >= total_min:
        s += " " + total.format(len(items))
    return s


def get_html_section(name):
    """
    Return a new section as HTML, with the given name and a time stamp.

    :param name: section name
    :type name: str
    :rtype: str
    """
    datetime = time.strftime("%a %b %d %y, %H:%M:%S")
    return "<h1>{} <span class='timestamp'>{}</h1>".format(name, datetime)


def get_html_subsection(name):
    """
    Return a subsection as HTML, with the given name

    :param name: subsection name
    :type name: str
    :rtype: str
    """
    return "<h2>{}</h2>".format(name)


def render_items(items):
    """
    Render a sequence of pairs or an `OrderedDict` as a HTML list.

    The function skips the items whose values are `None` or `False`.

    :param items: a sequence of items
    :type items: list or tuple or OrderedDict
    :return: rendered content
    :rtype: str
    """
    if isinstance(items, dict):
        items = items.items()
    return "<ul>" + "".join(
        "<b>{}:</b> {}</br>".format(key, value) for key, value in items
        if value is not None and value is not False) + "</ul>"


def render_items_vert(items):
    """
    Render a sequence of pairs or an `OrderedDict` as a comma-separated list.

    The function skips the items whose values are `None` or `False`.

    :param items: a sequence of items
    :type items: list or tuple or OrderedDict
    :return: rendered content
    :rtype: str
    """
    if isinstance(items, dict):
        items = items.items()
    return ", ".join("<b>{}</b>: {}".format(key, value) for key, value in items
                     if value is not None and value is not False)


def get_html_img(
        scene: QGraphicsScene, max_height: Optional[int] = None
) -> str:
    """
    Create HTML img element with base64-encoded image from the scene.
    If max_height is not none set the max height of the image in html.
    """
    byte_array = QByteArray()
    filename = QBuffer(byte_array)
    filename.open(QIODevice.WriteOnly)
    PngFormat.write(filename, scene)
    img_encoded = byte_array.toBase64().data().decode("utf-8")
    return '<img {} src="data:image/png;base64,{}"/>'.format(
        ("" if max_height is None
         else 'style="max-height: {}px"'.format(max_height)),
        img_encoded
    )


def get_icon_html(icon: QIcon, size: QSize) -> str:
    """
    Transform an icon to html <img> tag.
    """
    if not size.isValid():
        return ""
    if size.width() < 0 or size.height() < 0:
        size = QSize(16, 16)  # just in case
    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.WriteOnly)
    pixmap = icon.pixmap(size)
    if pixmap.isNull():
        return ""
    pixmap.save(buffer, "PNG")
    buffer.close()

    dpr = pixmap.devicePixelRatioF()
    if dpr != 1.0:
        size_ = pixmap.size() / dpr
        size_part = ' width="{}" height="{}"'.format(
            int(math.floor(size_.width())), int(math.floor(size_.height()))
        )
    else:
        size_part = ''
    img_encoded = byte_array.toBase64().data().decode("utf-8")
    return '<img src="data:image/png;base64,{}"{}/>'.format(img_encoded, size_part)


def colored_square(r, g, b):
    return '<span class="legend-square" ' \
           'style="background-color: rgb({}, {}, {})"></span>'.format(r, g, b)


def list_legend(model, selected=None):
    """
    Create HTML with a legend constructed from a Qt model or a view.

    This function can be used for reporting the legend for graph in widgets
    in which the colors representing different values are shown in a listbox
    with colored icons. The function returns a string with values from the
    listbox, preceded by squares of the corresponding colors.

    The model must return data for Qt.DecorationRole. If a view is passed as
    an argument, it has to have method `model()`.

    :param model: model or view, usually a list box
    :param selected: if given, only items with the specified indices are shown
    """
    if hasattr(model, "model"):
        model = model.model()
    legend = ""
    for row in range(model.rowCount()):
        if selected is not None and row not in selected:
            continue
        index = model.index(row, 0)
        icon = model.data(index, Qt.DecorationRole)
        r, g, b, a = QColor(
            icon.pixmap(12, 12).toImage().pixel(0, 0)).getRgb()
        text = model.data(index, Qt.DisplayRole)
        legend += colored_square(r, g, b) + \
                  '<span class="legend-item">{}</span>'.format(text)
    return legend
