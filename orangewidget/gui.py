"""
Wrappers for controls used in widgets
"""
import contextlib
import math
import re
import itertools
import sys
import warnings
import logging
from types import LambdaType
from collections import defaultdict

import pkg_resources

from AnyQt import QtWidgets, QtCore, QtGui
from AnyQt.QtCore import Qt, QEvent, QObject, QTimer, pyqtSignal as Signal
from AnyQt.QtGui import QCursor, QColor
from AnyQt.QtWidgets import (
    QApplication, QStyle, QSizePolicy, QWidget, QLabel, QGroupBox, QSlider,
    QTableWidgetItem, QStyledItemDelegate, QTableView, QHeaderView,
    QScrollArea, QFrame, QLineEdit, QCalendarWidget, QDateTimeEdit,
)

from orangewidget.utils import getdeepattr
from orangewidget.utils.buttons import VariableTextPushButton
from orangewidget.utils.combobox import (
    ComboBox as OrangeComboBox, ComboBoxSearch as OrangeComboBoxSearch
)
from orangewidget.utils.itemdelegates import text_color_for_state
from orangewidget.utils.itemmodels import PyListModel

__re_label = re.compile(r"(^|[^%])%\((?P<value>[a-zA-Z]\w*)\)")

log = logging.getLogger(__name__)

OrangeUserRole = itertools.count(Qt.UserRole)

LAMBDA_NAME = (f"_lambda_{i}" for i in itertools.count(1))


def is_macstyle():
    style = QApplication.style()
    style_name = style.metaObject().className()
    return style_name == 'QMacStyle'


class TableView(QTableView):
    """An auxilliary table view for use with PyTableModel in control areas"""
    def __init__(self, parent=None, **kwargs):
        kwargs = dict(
            dict(showGrid=False,
                 sortingEnabled=True,
                 cornerButtonEnabled=False,
                 alternatingRowColors=True,
                 selectionBehavior=self.SelectRows,
                 selectionMode=self.ExtendedSelection,
                 horizontalScrollMode=self.ScrollPerPixel,
                 verticalScrollMode=self.ScrollPerPixel,
                 editTriggers=self.DoubleClicked | self.EditKeyPressed),
            **kwargs)
        super().__init__(parent, **kwargs)
        h = self.horizontalHeader()
        h.setCascadingSectionResizes(True)
        h.setMinimumSectionSize(-1)
        h.setStretchLastSection(True)
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        v = self.verticalHeader()
        v.setVisible(False)
        v.setSectionResizeMode(QHeaderView.ResizeToContents)

    class BoldFontDelegate(QStyledItemDelegate):
        """Paints the text of associated cells in bold font.

        Can be used e.g. with QTableView.setItemDelegateForColumn() to make
        certain table columns bold, or if callback is provided, the item's
        model index is passed to it, and the item is made bold only if the
        callback returns true.

        Parameters
        ----------
        parent: QObject
            The parent QObject.
        callback: callable
            Accepts model index and returns True if the item is to be
            rendered in bold font.
        """
        def __init__(self, parent=None, callback=None):
            super().__init__(parent)
            self._callback = callback

        def paint(self, painter, option, index):
            """Paint item text in bold font"""
            if not callable(self._callback) or self._callback(index):
                option.font.setWeight(option.font.Bold)
            super().paint(painter, option, index)

        def sizeHint(self, option, index):
            """Ensure item size accounts for bold font width"""
            if not callable(self._callback) or self._callback(index):
                option.font.setWeight(option.font.Bold)
            return super().sizeHint(option, index)


def resource_filename(path):
    """
    Return a resource filename (package data) for path.
    """
    return pkg_resources.resource_filename(__name__, path)


class OWComponent:
    """
    Mixin for classes that contain settings and/or attributes that trigger
    callbacks when changed.

    The class initializes the settings handler, provides `__setattr__` that
    triggers callbacks, and provides `control` attribute for access to
    Qt widgets controling particular attributes.

    Callbacks are exploited by controls (e.g. check boxes, line edits,
    combo boxes...) that are synchronized with attribute values. Changing
    the value of the attribute triggers a call to a function that updates
    the Qt widget accordingly.

    The class is mixed into `widget.OWBaseWidget`, and must also be mixed into
    all widgets not derived from `widget.OWBaseWidget` that contain settings or
    Qt widgets inserted by function in `orangewidget.gui` module. See
    `OWScatterPlotGraph` for an example.
    """
    def __init__(self, widget=None):
        self.controlled_attributes = defaultdict(list)
        self.controls = ControlGetter(self)
        if widget is not None and widget.settingsHandler:
            widget.settingsHandler.initialize(self)

    def _reset_settings(self):
        """
        Copy default settings to instance's settings. This method can be
        called from OWWidget's reset_settings, but will mostly have to be
        followed by calling a method that updates the widget.
        """
        self.settingsHandler.reset_to_original(self)

    def connect_control(self, name, func):
        """
        Add `func` to the list of functions called when the value of the
        attribute `name` is set.

        If the name includes a dot, it is assumed that the part the before the
        first dot is a name of an attribute containing an instance of a
        component, and the call is transferred to its `conntect_control`. For
        instance, `calling `obj.connect_control("graph.attr_x", f)` is
        equivalent to `obj.graph.connect_control("attr_x", f)`.

        Args:
            name (str): attribute name
            func (callable): callback function
        """
        if "." in name:
            name, rest = name.split(".", 1)
            sub = getattr(self, name)
            sub.connect_control(rest, func)
        else:
            self.controlled_attributes[name].append(func)

    def __setattr__(self, name, value):
        """Set the attribute value and trigger any attached callbacks.

        For backward compatibility, the name can include dots, e.g.
        `graph.attr_x`. `obj.__setattr__('x.y', v)` is equivalent to
        `obj.x.__setattr__('x', v)`.

        Args:
            name (str): attribute name
            value (object): value to set to the member.
        """
        if "." in name:
            name, rest = name.split(".", 1)
            sub = getattr(self, name)
            setattr(sub, rest, value)
        else:
            super().__setattr__(name, value)
            # First check that the widget is not just being constructed
            if hasattr(self, "controlled_attributes"):
                for callback in self.controlled_attributes.get(name, ()):
                    callback(value)


def miscellanea(control, box, parent, *,
                addToLayout=True, stretch=0, sizePolicy=None,
                disabled=False, tooltip=None, disabledBy=None,
                addSpaceBefore=False, **kwargs):
    """
    Helper function that sets various properties of the widget using a common
    set of arguments.

    The function
    - sets the `control`'s attribute `box`, if `box` is given and `control.box`
    is not yet set,
    - attaches a tool tip to the `control` if specified,
    - disables the `control`, if `disabled` is set to `True`,
    - adds the `box` to the `parent`'s layout unless `addToLayout` is set to
    `False`; the stretch factor can be specified,
    - adds the control into the box's layout if the box is given (regardless
    of `addToLayout`!)
    - sets the size policy for the box or the control, if the policy is given,
    - adds space in the `parent`'s layout after the `box` if `addSpace` is set
    and `addToLayout` is not `False`.

    If `box` is the same as `parent` it is set to `None`; this is convenient
    because of the way complex controls are inserted.

    Unused keyword arguments are assumed to be properties; with this `gui`
    function mimic the behaviour of PyQt's constructors. For instance, if
    `gui.lineEdit` is called with keyword argument `sizePolicy=some_policy`,
    `miscallenea` will call `control.setSizePolicy(some_policy)`.

    :param control: the control, e.g. a `QCheckBox`
    :type control: QWidget
    :param box: the box into which the widget was inserted
    :type box: QWidget or None
    :param parent: the parent into whose layout the box or the control will be
        inserted
    :type parent: QWidget
    :param addSpaceBefore: the amount of space to add before the widget
    :type addSpaceBefore: bool or int
    :param disabled: If set to `True`, the widget is initially disabled
    :type disabled: bool
    :param addToLayout: If set to `False` the widget is not added to the layout
    :type addToLayout: bool
    :param stretch: the stretch factor for this widget, used when adding to
        the layout (default: 0)
    :type stretch: int
    :param tooltip: tooltip that is attached to the widget
    :type tooltip: str or None
    :param disabledBy: checkbox created with checkBox() function
    :type disabledBy: QCheckBox or None
    :param sizePolicy: the size policy for the box or the control
    :type sizePolicy: QSizePolicy
    """
    if 'addSpace' in kwargs:
        warnings.warn("'addSpace' has been deprecated. Use gui.separator instead.",
                      DeprecationWarning, stacklevel=3)
        kwargs.pop('addSpace')
    for prop, val in kwargs.items():
        method = getattr(control, "set" + prop[0].upper() + prop[1:])
        if isinstance(val, tuple):
            method(*val)
        else:
            method(val)
    if disabled:
        # if disabled==False, do nothing; it can be already disabled
        control.setDisabled(disabled)
    if tooltip is not None:
        control.setToolTip(tooltip)
    if box is parent:
        box = None
    elif box and box is not control and not hasattr(control, "box"):
        control.box = box
    if box and box.layout() is not None and \
            isinstance(control, QtWidgets.QWidget) and \
            box.layout().indexOf(control) == -1:
        box.layout().addWidget(control)
    if disabledBy is not None:
        disabledBy.disables.append(control)
        disabledBy.makeConsistent()
    if sizePolicy is not None:
        if isinstance(sizePolicy, tuple):
            sizePolicy = QSizePolicy(*sizePolicy)
        if box:
            box.setSizePolicy(sizePolicy)
        control.setSizePolicy(sizePolicy)
    if addToLayout and parent and parent.layout() is not None:
        _addSpace(parent, addSpaceBefore)
        parent.layout().addWidget(box or control, stretch)


def _is_horizontal(orientation):
    if isinstance(orientation, str):
        warnings.warn("string literals for orientation are deprecated",
                      DeprecationWarning)
    elif isinstance(orientation, bool):
        warnings.warn("boolean values for orientation are deprecated",
                      DeprecationWarning)
    return (orientation == Qt.Horizontal or
            orientation == 'horizontal' or
            not orientation)


def setLayout(widget, layout):
    """
    Set the layout of the widget.

    If `layout` is given as `Qt.Vertical` or `Qt.Horizontal`, the function
    sets the layout to :obj:`~QVBoxLayout` or :obj:`~QVBoxLayout`.

    :param widget: the widget for which the layout is being set
    :type widget: QWidget
    :param layout: layout
    :type layout: `Qt.Horizontal`, `Qt.Vertical` or instance of `QLayout`
    """
    if not isinstance(layout, QtWidgets.QLayout):
        if _is_horizontal(layout):
            layout = QtWidgets.QHBoxLayout()
        else:
            layout = QtWidgets.QVBoxLayout()
    widget.setLayout(layout)


def _addSpace(widget, space):
    """
    A helper function that adds space into the widget, if requested.
    The function is called by functions that have the `addSpace` argument.

    :param widget: Widget into which to insert the space
    :type widget: QWidget
    :param space: Amount of space to insert. If False, the function does
        nothing. If the argument is an `int`, the specified space is inserted.
        Otherwise, the default space is inserted by calling a :obj:`separator`.
    :type space: bool or int
    """
    if space:
        if type(space) == int:  # distinguish between int and bool!
            separator(widget, space, space)
        else:
            separator(widget)


def separator(widget, width=None, height=None):
    """
    Add a separator of the given size into the widget.

    :param widget: the widget into whose layout the separator is added
    :type widget: QWidget
    :param width: width of the separator
    :type width: int
    :param height: height of the separator
    :type height: int
    :return: separator
    :rtype: QWidget
    """
    sep = QtWidgets.QWidget(widget)
    if widget is not None and widget.layout() is not None:
        widget.layout().addWidget(sep)
    size = separator_size(width, height)
    sep.setFixedSize(*size)
    return sep


def separator_size(width=None, height=None):
    if is_macstyle():
        width = 2 if width is None else width
        height = 2 if height is None else height
    else:
        width = 4 if width is None else width
        height = 4 if height is None else height
    return width, height


def rubber(widget):
    """
    Insert a stretch 100 into the widget's layout
    """
    widget.layout().addStretch(100)


def widgetBox(widget, box=None, orientation=Qt.Vertical, margin=None, spacing=None,
              **misc):
    """
    Construct a box with vertical or horizontal layout, and optionally,
    a border with an optional label.

    If the widget has a frame, the space after the widget is added unless
    explicitly disabled.

    :param widget: the widget into which the box is inserted
    :type widget: QWidget or None
    :param box: tells whether the widget has a border, and its label
    :type box: int or str or None
    :param orientation: orientation of the box
    :type orientation: `Qt.Horizontal`, `Qt.Vertical` or instance of `QLayout`
    :param sizePolicy: The size policy for the widget (default: None)
    :type sizePolicy: :obj:`~QSizePolicy`
    :param margin: The margin for the layout. Default is 7 if the widget has
        a border, and 0 if not.
    :type margin: int
    :param spacing: Spacing within the layout (default: 4)
    :type spacing: int
    :return: Constructed box
    :rtype: QGroupBox or QWidget
    """
    if box:
        b = QtWidgets.QGroupBox(widget)
        if isinstance(box, str):
            b.setTitle(" " + box.strip() + " ")
            if is_macstyle() and widget and widget.layout() and \
                    isinstance(widget.layout(), QtWidgets.QVBoxLayout) and \
                    not widget.layout().isEmpty():
                misc.setdefault('addSpaceBefore', True)
        if margin is None:
            margin = 4
    else:
        b = QtWidgets.QWidget(widget)
        b.setContentsMargins(0, 0, 0, 0)
        if margin is None:
            margin = 0
    setLayout(b, orientation)
    if spacing is not None:
        b.layout().setSpacing(spacing)
    b.layout().setContentsMargins(margin, margin, margin, margin)
    miscellanea(b, None, widget, **misc)
    return b


def hBox(*args, **kwargs):
    return widgetBox(orientation=Qt.Horizontal, *args, **kwargs)


def vBox(*args, **kwargs):
    return widgetBox(orientation=Qt.Vertical, *args, **kwargs)


def indentedBox(widget, sep=20, orientation=Qt.Vertical, **misc):
    """
    Creates an indented box. The function can also be used "on the fly"::

        gui.checkBox(gui.indentedBox(box), self, "spam", "Enable spam")

    To align the control with a check box, use :obj:`checkButtonOffsetHint`::

        gui.hSlider(gui.indentedBox(self.interBox), self, "intervals")

    :param widget: the widget into which the box is inserted
    :type widget: QWidget
    :param sep: Indent size (default: 20)
    :type sep: int
    :param orientation: orientation of the inserted box
    :type orientation: `Qt.Vertical` (default), `Qt.Horizontal` or
            instance of `QLayout`
    :return: Constructed box
    :rtype: QGroupBox or QWidget
    """
    outer = hBox(widget, spacing=0)
    separator(outer, sep, 0)
    indented = widgetBox(outer, orientation=orientation)
    miscellanea(indented, outer, widget, **misc)
    indented.box = outer
    return indented


def widgetLabel(widget, label="", labelWidth=None, **misc):
    """
    Construct a simple, constant label.

    :param widget: the widget into which the box is inserted
    :type widget: QWidget or None
    :param label: The text of the label (default: None)
    :type label: str
    :param labelWidth: The width of the label (default: None)
    :type labelWidth: int
    :return: Constructed label
    :rtype: QLabel
    """
    lbl = QtWidgets.QLabel(label, widget)
    if labelWidth:
        lbl.setFixedSize(labelWidth, lbl.sizeHint().height())
    miscellanea(lbl, None, widget, **misc)
    return lbl


def label(widget, master, label, labelWidth=None, box=None,
          orientation=Qt.Vertical, **misc):
    """
    Construct a label that contains references to the master widget's
    attributes; when their values change, the label is updated.

    Argument :obj:`label` is a format string following Python's syntax
    (see the corresponding Python documentation): the label's content is
    rendered as `label % master.__dict__`. For instance, if the
    :obj:`label` is given as "There are %(mm)i monkeys", the value of
    `master.mm` (which must be an integer) will be inserted in place of
    `%(mm)i`.

    :param widget: the widget into which the box is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param label: The text of the label, including attribute names
    :type label: str
    :param labelWidth: The width of the label (default: None)
    :type labelWidth: int
    :param orientation: layout of the inserted box
    :type orientation: `Qt.Vertical` (default), `Qt.Horizontal` or
        instance of `QLayout`
    :return: label
    :rtype: QLabel
    """
    if box:
        b = hBox(widget, box, addToLayout=False)
    else:
        b = widget

    lbl = QtWidgets.QLabel("", b)
    reprint = CallFrontLabel(lbl, label, master)
    for mo in __re_label.finditer(label):
        master.connect_control(mo.group("value"), reprint)
    reprint()
    if labelWidth:
        lbl.setFixedSize(labelWidth, lbl.sizeHint().height())
    miscellanea(lbl, b, widget, **misc)
    return lbl


class SpinBoxMixin:
    """
    The class overloads :obj:`onChange` event handler to show the commit button,
    and :obj:`onEnter` to commit the change when enter is pressed.

    Also, click and drag to increase/decrease the spinbox's value,
    instead of scrolling.
    """

    valueCommitted = Signal(object)

    def __init__(self, minv, maxv, step, parent=None, verticalDrag=True):
        """
        Construct the object and set the range (`minv`, `maxv`) and the step.
        :param minv: Minimal value
        :type minv: int
        :param maxv: Maximal value
        :type maxv: int
        :param step: Step
        :type step: int
        :param parent: Parent widget
        :type parent: QWidget
        :param verticalDrag: Drag direction
        :type verticalDrag: bool
        """
        super().__init__(parent)
        self.setRange(minv, maxv)
        self.setSingleStep(step)

        self.equalityChecker = int.__eq__

        self.mouseHeld = False
        self.verticalDirection = verticalDrag
        self.mouseStartPos = QtCore.QPoint()
        self.preDragValue = 0

        self.textEditing = False
        self.preEditvalue = 0

        self.lineEdit().installEventFilter(self)
        self.installEventFilter(self)
        self.editingFinished.connect(self.__onEditingFinished)
        self.valueChanged.connect(self.__onValueChanged)

        # don't focus on scroll
        self.setFocusPolicy(Qt.StrongFocus)

        self.cback = None
        self.cfunc = None

    def __onEditingFinished(self):
        """
        After user input is finished, commit the new value.
        """
        if not self.mouseHeld and not self.textEditing:
            # value hasn't been altered
            return
        if self.mouseHeld:
            self.mouseHeld = False
            initialValue = self.preDragValue
        if self.textEditing:
            # mouse held can be triggered after editing, but not vice versa
            self.textEditing = False
            initialValue = self.preEditvalue
        value = self.value()
        if not self.equalityChecker(initialValue, value):
            # if value has changed, commit it
            self.__commitValue(value)

    def __onValueChanged(self, value):
        """
        When the value is changed outwith user input, commit it.
        """
        if not self.mouseHeld and not self.textEditing:
            self.__commitValue(value)

    def __commitValue(self, value):
        self.valueCommitted.emit(value)
        if self.cback:
            self.cback(value)
        if self.cfunc:
            self.cfunc()

    def eventFilter(self, obj, event):
        if not self.isEnabled() or \
                not (isinstance(obj, SpinBoxMixin) or isinstance(obj, QLineEdit)):
            return super().eventFilter(obj, event)

        cursor = Qt.SizeVerCursor if self.verticalDirection else Qt.SizeHorCursor

        if event.type() == QEvent.MouseButtonPress:
            # prepare click+drag
            self.mouseStartPos = event.globalPos()
            self.preDragValue = self.value()
            self.mouseHeld = True
        elif event.type() == QEvent.MouseMove and self.mouseHeld and isinstance(obj, QLineEdit):
            # do click+drag
            # override default cursor on drag
            if QApplication.overrideCursor() != cursor:
                QApplication.setOverrideCursor(cursor)

            stepSize = self.singleStep()

            pos = event.globalPos()
            posVal = pos.y() if self.verticalDirection else -pos.x()
            posValStart = self.mouseStartPos.y() if self.verticalDirection else -self.mouseStartPos.x()
            diff = posValStart - posVal

            # these magic params are pretty arbitrary, ensure that it's still
            # possible to easily highlight the text if moving mouse slightly
            # up/down, with the default stepsize
            normalizedDiff = abs(diff) / 30
            exponent = 1 + min(normalizedDiff / 10, 3)
            valueOffset = int(normalizedDiff ** exponent) * stepSize
            valueOffset = math.copysign(valueOffset, diff)

            self.setValue(self.preDragValue + valueOffset)

        elif event.type() == QEvent.MouseButtonRelease:
            # end click+drag
            # restore default cursor on release
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()

            self.__onEditingFinished()
        elif event.type() == QEvent.Wheel:
            # disable wheelEvents (scrolling to change value)
            event.ignore()
            return True
        elif event.type() in (QEvent.KeyPress, QEvent.KeyRelease):
            # handle committing keyboard entry only on editingFinished
            if self.mouseHeld:
                # if performing click+drag, ignore key events
                event.ignore()
                return True
            elif not self.textEditing:
                self.preEditvalue = self.value()
                self.textEditing = True
        return super().eventFilter(obj, event)

    def onEnter(self):
        warnings.warn(
            "Testing by calling a spinbox's 'onEnter' method is deprecated, "
            "a call to 'setValue' should be sufficient.",
            DeprecationWarning
        )


class SpinBox(SpinBoxMixin, QtWidgets.QSpinBox):
    """
    A class derived from QSpinBox, which postpones the synchronization
    of the control's value with the master's attribute until the control loses
    focus, and adds click-and-drag to change value functionality.
    """


class DoubleSpinBox(SpinBoxMixin, QtWidgets.QDoubleSpinBox):
    """
    Same as :obj:`SpinBoxWFocusOut`, except that it is derived from
    :obj:`~QDoubleSpinBox`"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setDecimals(math.ceil(-math.log10(self.singleStep())))
        self.equalityChecker = math.isclose


# deprecated
SpinBoxWFocusOut = SpinBox
DoubleSpinBoxWFocusOut = DoubleSpinBox


def spin(widget, master, value, minv, maxv, step=1, box=None, label=None,
         labelWidth=None, orientation=Qt.Horizontal, callback=None,
         controlWidth=None, callbackOnReturn=False, checked=None,
         checkCallback=None, posttext=None, disabled=False,
         alignment=Qt.AlignLeft, keyboardTracking=True,
         decimals=None, spinType=int, **misc):
    """
    A spinbox with lots of bells and whistles, such as a checkbox and various
    callbacks. It constructs a control of type :obj:`SpinBoxWFocusOut` or
    :obj:`DoubleSpinBoxWFocusOut`.

    :param widget: the widget into which the box is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param value: the master's attribute with which the value is synchronized
    :type value:  str
    :param minv: minimal value
    :type minv: int or float
    :param maxv: maximal value
    :type maxv: int or float
    :param step: step (default: 1)
    :type step: int or float
    :param box: tells whether the widget has a border, and its label
    :type box: int or str or None
    :param label: label that is put in above or to the left of the spin box
    :type label: str
    :param labelWidth: optional label width (default: None)
    :type labelWidth: int
    :param orientation: tells whether to put the label above or to the left
    :type orientation: `Qt.Horizontal` (default), `Qt.Vertical` or
        instance of `QLayout`
    :param callback: a function that is called when the value is entered;
        the function is called when the user finishes editing the value
    :type callback: function
    :param controlWidth: the width of the spin box
    :type controlWidth: int
    :param callbackOnReturn: (deprecated)
    :type callbackOnReturn: bool
    :param checked: if not None, a check box is put in front of the spin box;
        when unchecked, the spin box is disabled. Argument `checked` gives the
        name of the master's attribute given whose value is synchronized with
        the check box's state (default: None).
    :type checked: str
    :param checkCallback: a callback function that is called when the check
        box's state is changed
    :type checkCallback: function
    :param posttext: a text that is put to the right of the spin box
    :type posttext: str
    :param alignment: alignment of the spin box (e.g. `Qt.AlignLeft`)
    :type alignment: Qt.Alignment
    :param keyboardTracking: If `True`, the valueChanged signal is emitted
        when the user is typing (default: True)
    :type keyboardTracking: bool
    :param spinType: determines whether to use QSpinBox (int) or
        QDoubleSpinBox (float)
    :type spinType: type
    :param decimals: number of decimals (if `spinType` is `float`)
    :type decimals: int
    :return: Tuple `(spin box, check box) if `checked` is `True`, otherwise
        the spin box
    :rtype: tuple or gui.SpinBoxWFocusOut
    """

    if callbackOnReturn:
        warnings.warn(
            "'callbackOnReturn' is deprecated, all spinboxes callback "
            "only when the user is finished editing the value.",
            DeprecationWarning, stacklevel=2
        )
    # b is the outermost box or the widget if there are no boxes;
    #    b is the widget that is inserted into the layout
    # bi is the box that contains the control or the checkbox and the control;
    #    bi can be the widget itself, if there are no boxes
    # cbox is the checkbox (or None)
    # sbox is the spinbox itself
    if box or label and not checked:
        b = widgetBox(widget, box, orientation, addToLayout=False)
        hasHBox = _is_horizontal(orientation)
    else:
        b = widget
        hasHBox = False
    if not hasHBox and (checked or callback or posttext):
        bi = hBox(b, addToLayout=False)
    else:
        bi = b

    cbox = None
    if checked is not None:
        cbox = checkBox(bi, master, checked, label, labelWidth=labelWidth,
                        callback=checkCallback)
    elif label:
        b.label = widgetLabel(b, label, labelWidth)
    if posttext:
        widgetLabel(bi, posttext)

    isDouble = spinType == float
    sbox = bi.control = b.control = \
        (SpinBox, DoubleSpinBox)[isDouble](minv, maxv,
                                           step, bi)
    if bi is not widget:
        bi.setDisabled(disabled)
    else:
        sbox.setDisabled(disabled)

    if decimals is not None:
        sbox.setDecimals(decimals)
    sbox.setAlignment(alignment)
    sbox.setKeyboardTracking(keyboardTracking)
    if controlWidth:
        sbox.setFixedWidth(controlWidth)
    if value:
        sbox.setValue(getdeepattr(master, value))

    cfront, sbox.cback, sbox.cfunc = connectControl(
        master, value, callback,
        not (callback) and
        sbox.valueCommitted,
        (CallFrontSpin, CallFrontDoubleSpin)[isDouble](sbox))
    if checked:
        sbox.cbox = cbox
        cbox.disables = [sbox]
        cbox.makeConsistent()
    if callback:
        if hasattr(sbox, "upButton"):
            sbox.upButton().clicked.connect(
                lambda c=sbox.editor(): c.setFocus())
            sbox.downButton().clicked.connect(
                lambda c=sbox.editor(): c.setFocus())

    miscellanea(sbox, b if b is not widget else bi, widget, **misc)
    if checked:
        if isDouble and b == widget:
            # TODO Backward compatilibity; try to find and eliminate
            sbox.control = b.control
            return sbox
        return cbox, sbox
    else:
        return sbox



# noinspection PyTypeChecker
def doubleSpin(widget, master, value, minv, maxv, step=1, box=None, label=None,
               labelWidth=None, orientation=Qt.Horizontal, callback=None,
               controlWidth=None, callbackOnReturn=False, checked=None,
               checkCallback=None, posttext=None,
               alignment=Qt.AlignLeft, keyboardTracking=True,
               decimals=None, **misc):
    """
    Backward compatilibity function: calls :obj:`spin` with `spinType=float`.
    """
    return spin(widget, master, value, minv, maxv, step, box=box, label=label,
                labelWidth=labelWidth, orientation=orientation,
                callback=callback, controlWidth=controlWidth,
                callbackOnReturn=callbackOnReturn, checked=checked,
                checkCallback=checkCallback, posttext=posttext,
                alignment=alignment, keyboardTracking=keyboardTracking,
                decimals=decimals, spinType=float, **misc)


class CheckBoxWithDisabledState(QtWidgets.QCheckBox):
    def __init__(self, label, parent, disabledState):
        super().__init__(label, parent)
        self.disabledState = disabledState
        # self.trueState is always stored as Qt.Checked, Qt.PartiallyChecked
        # or Qt.Unchecked, even if the button is two-state, because in
        # setCheckState, which is used for setting it, "true" would result
        # in partially checked.
        self.trueState = self.checkState()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.EnabledChange:
            self._updateChecked()

    def setCheckState(self, state):
        self.trueState = state
        self._updateChecked()

    def setChecked(self, state):
        self._storeTrueState(state)
        self._updateChecked()

    def _updateChecked(self):
        if self.isEnabled():
            super().setCheckState(self.trueState)
        else:
            super().setCheckState(self.disabledState)

    def _storeTrueState(self, state):
        self.trueState = Qt.Checked if state else Qt.Unchecked


def checkBox(widget, master, value, label, box=None,
             callback=None, getwidget=False, id_=None, labelWidth=None,
             disables=None, stateWhenDisabled=None, **misc):
    """
    A simple checkbox.

    :param widget: the widget into which the box is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param value: the master's attribute with which the value is synchronized
    :type value:  str
    :param label: label
    :type label: str
    :param box: tells whether the widget has a border, and its label
    :type box: int or str or None
    :param callback: a function that is called when the check box state is
        changed
    :type callback: function
    :param getwidget: If set `True`, the callback function will get a keyword
        argument `widget` referencing the check box
    :type getwidget: bool
    :param id_: If present, the callback function will get a keyword argument
        `id` with this value
    :type id_: any
    :param labelWidth: the width of the label
    :type labelWidth: int
    :param disables: a list of widgets that are disabled if the check box is
        unchecked
    :type disables: list or QWidget or None
    :param stateWhenDisabled: the shown state of the checkbox when it is
        disabled (default: None, unaffected)
    :type stateWhenDisabled: bool or Qt.CheckState or None
    :return: constructed check box; if is is placed within a box, the box is
        return in the attribute `box`
    :rtype: QCheckBox
    """
    if box:
        b = hBox(widget, box, addToLayout=False)
    else:
        b = widget
    if stateWhenDisabled is not None:
        if isinstance(stateWhenDisabled, bool):
            stateWhenDisabled = Qt.Checked if stateWhenDisabled else Qt.Unchecked
        cbox = CheckBoxWithDisabledState(label, b, stateWhenDisabled)
        cbox.clicked.connect(cbox._storeTrueState)
    else:
        cbox = QtWidgets.QCheckBox(label, b)

    if labelWidth:
        cbox.setFixedSize(labelWidth, cbox.sizeHint().height())
    cbox.setChecked(getdeepattr(master, value))

    connectControl(master, value, None, cbox.toggled[bool],
                   CallFrontCheckBox(cbox),
                   cfunc=callback and FunctionCallback(
                       master, callback, widget=cbox, getwidget=getwidget,
                       id=id_))
    if isinstance(disables, QtWidgets.QWidget):
        disables = [disables]
    cbox.disables = disables or []
    cbox.makeConsistent = Disabler(cbox, master, value)
    cbox.toggled[bool].connect(cbox.makeConsistent)
    cbox.makeConsistent(value)
    miscellanea(cbox, b, widget, **misc)
    return cbox


class LineEditWFocusOut(QtWidgets.QLineEdit):
    """
    A class derived from QLineEdit, which postpones the synchronization
    of the control's value with the master's attribute until the user leaves
    the line edit or presses Tab when the value is changed.

    The class also allows specifying a callback function for focus-in event.

    .. attribute:: callback

        Callback that is called when the change is confirmed

    .. attribute:: focusInCallback

        Callback that is called on the focus-in event
    """

    def __init__(self, parent, callback, focusInCallback=None):
        super().__init__(parent)
        if parent is not None and parent.layout() is not None:
            parent.layout().addWidget(self)
        self.callback = callback
        self.focusInCallback = focusInCallback
        self.returnPressed.connect(self.returnPressedHandler)
        # did the text change between focus enter and leave
        self.__changed = False
        self.textEdited.connect(self.__textEdited)

    def __textEdited(self):
        self.__changed = True

    def returnPressedHandler(self):
        self.selectAll()
        self.__callback_if_changed()

    def __callback_if_changed(self):
        if self.__changed:
            self.__changed = False
            if hasattr(self, "cback") and self.cback:
                self.cback(self.text())
            if self.callback:
                self.callback()

    def setText(self, text):
        self.__changed = False
        super().setText(text)

    def focusOutEvent(self, *e):
        super().focusOutEvent(*e)
        self.__callback_if_changed()

    def focusInEvent(self, *e):
        self.__changed = False
        if self.focusInCallback:
            self.focusInCallback()
        return super().focusInEvent(*e)


def lineEdit(widget, master, value, label=None, labelWidth=None,
             orientation=Qt.Vertical, box=None, callback=None,
             valueType=None, validator=None, controlWidth=None,
             callbackOnType=False, focusInCallback=None, **misc):
    """
    Insert a line edit.

    :param widget: the widget into which the box is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param value: the master's attribute with which the value is synchronized
    :type value:  str
    :param label: label
    :type label: str
    :param labelWidth: the width of the label
    :type labelWidth: int
    :param orientation: tells whether to put the label above or to the left
    :type orientation: `Qt.Vertical` (default) or `Qt.Horizontal`
    :param box: tells whether the widget has a border, and its label
    :type box: int or str or None
    :param callback: a function that is called when the check box state is
        changed
    :type callback: function
    :param valueType: the type into which the entered string is converted
        when synchronizing to `value`. If omitted, the type of the current
        `value` is used. If `value` is `None`, the text is left as a string.
    :type valueType: type or None
    :param validator: the validator for the input
    :type validator: QValidator
    :param controlWidth: the width of the line edit
    :type controlWidth: int
    :param callbackOnType: if set to `True`, the callback is called at each
        key press (default: `False`)
    :type callbackOnType: bool
    :param focusInCallback: a function that is called when the line edit
        receives focus
    :type focusInCallback: function
    :rtype: QLineEdit or a box
    """
    if box or label:
        b = widgetBox(widget, box, orientation, addToLayout=False)
        if label is not None:
            widgetLabel(b, label, labelWidth)
    else:
        b = widget

    baseClass = misc.pop("baseClass", None)
    if baseClass:
        ledit = baseClass(b)
        if b is not widget:
            b.layout().addWidget(ledit)
    elif focusInCallback or callback and not callbackOnType:
        ledit = LineEditWFocusOut(b, callback, focusInCallback)
    else:
        ledit = QtWidgets.QLineEdit(b)
        if b is not widget:
            b.layout().addWidget(ledit)

    current_value = getdeepattr(master, value) if value else ""
    ledit.setText(str(current_value))
    if controlWidth:
        ledit.setFixedWidth(controlWidth)
    if validator:
        ledit.setValidator(validator)
    if value:
        ledit.cback = connectControl(
            master, value,
            callbackOnType and callback, ledit.textChanged[str],
            CallFrontLineEdit(ledit), fvcb=valueType or type(current_value))[1]

    miscellanea(ledit, b, widget, **misc)
    return ledit


def button(widget, master, label, callback=None, width=None, height=None,
           toggleButton=False, value="", default=False, autoDefault=True,
           buttonType=QtWidgets.QPushButton, **misc):
    """
    Insert a button (QPushButton, by default)

    :param widget: the widget into which the button is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param label: label
    :type label: str
    :param callback: a function that is called when the button is pressed
    :type callback: function
    :param width: the width of the button
    :type width: int
    :param height: the height of the button
    :type height: int
    :param toggleButton: if set to `True`, the button is checkable, but it is
        not synchronized with any attribute unless the `value` is given
    :type toggleButton: bool
    :param value: the master's attribute with which the value is synchronized
        (the argument is optional; if present, it makes the button "checkable",
        even if `toggleButton` is not set)
    :type value: str
    :param default: if `True` it makes the button the default button; this is
        the button that is activated when the user presses Enter unless some
        auto default button has current focus
    :type default: bool
    :param autoDefault: all buttons are auto default: they are activated if
        they have focus (or are the next in the focus chain) when the user
        presses enter. By setting `autoDefault` to `False`, the button is not
        activated on pressing Return.
    :type autoDefault: bool
    :param buttonType: the button type (default: `QPushButton`)
    :type buttonType: QPushButton
    :rtype: QPushButton
    """
    button = buttonType(widget)
    if is_macstyle():
        btnpaddingbox = vBox(widget, margin=0, spacing=0)
        separator(btnpaddingbox, 0, 4)  # lines up with a WA_LayoutUsesWidgetRect checkbox
        button.outer_box = btnpaddingbox
    else:
        button.outer_box = None
    if label:
        button.setText(label)
    if width:
        button.setFixedWidth(width)
    if height:
        button.setFixedHeight(height)
    if toggleButton or value:
        button.setCheckable(True)
    if buttonType == QtWidgets.QPushButton:
        button.setDefault(default)
        button.setAutoDefault(autoDefault)

    if value:
        button.setChecked(getdeepattr(master, value))
        connectControl(
            master, value, None, button.toggled[bool],
            CallFrontButton(button),
            cfunc=callback and FunctionCallback(master, callback,
                                                widget=button))
    elif callback:
        button.clicked.connect(callback)

    miscellanea(button, button.outer_box, widget, **misc)
    return button


def toolButton(widget, master, label="", callback=None,
               width=None, height=None, tooltip=None):
    """
    Insert a tool button. Calls :obj:`button`

    :param widget: the widget into which the button is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param label: label
    :type label: str
    :param callback: a function that is called when the button is pressed
    :type callback: function
    :param width: the width of the button
    :type width: int
    :param height: the height of the button
    :type height: int
    :rtype: QToolButton
    """
    return button(widget, master, label, callback, width, height,
                  buttonType=QtWidgets.QToolButton, tooltip=tooltip)


# btnLabels is a list of either char strings or pixmaps
def radioButtons(widget, master, value, btnLabels=(), tooltips=None,
                 box=None, label=None, orientation=Qt.Vertical,
                 callback=None, **misc):
    """
    Construct a button group and add radio buttons, if they are given.
    The value with which the buttons synchronize is the index of selected
    button.

    :param widget: the widget into which the box is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param value: the master's attribute with which the value is synchronized
    :type value:  str
    :param btnLabels: a list of labels or icons for radio buttons
    :type btnLabels: list of str or pixmaps
    :param tooltips: a list of tool tips of the same length as btnLabels
    :type tooltips: list of str
    :param box: tells whether the widget has a border, and its label
    :type box: int or str or None
    :param label: a label that is inserted into the box
    :type label: str
    :param callback: a function that is called when the selection is changed
    :type callback: function
    :param orientation: orientation of the box
    :type orientation: `Qt.Vertical` (default), `Qt.Horizontal` or an
        instance of `QLayout`
    :rtype: QButtonGroup
    """
    bg = widgetBox(widget, box, orientation,
                   addToLayout=misc.get('addToLayout', True))
    misc['addToLayout'] = False
    if label is not None:
        widgetLabel(bg, label)

    rb = QtWidgets.QButtonGroup(bg)
    if bg is not widget:
        bg.group = rb
    bg.buttons = []
    bg.ogValue = value
    bg.ogMaster = master
    for i, lab in enumerate(btnLabels):
        appendRadioButton(bg, lab, tooltip=tooltips and tooltips[i], id=i + 1)
    connectControl(master, value, callback, bg.group.buttonClicked[int],
                   CallFrontRadioButtons(bg), CallBackRadioButton(bg, master))
    miscellanea(bg.group, bg, widget, **misc)
    return bg


radioButtonsInBox = radioButtons

def appendRadioButton(group, label, insertInto=None,
                      disabled=False, tooltip=None, sizePolicy=None,
                      addToLayout=True, stretch=0, addSpace=None, id=None):
    """
    Construct a radio button and add it to the group. The group must be
    constructed with :obj:`radioButtons` since it adds additional
    attributes need for the call backs.

    The radio button is inserted into `insertInto` or, if omitted, into the
    button group. This is useful for more complex groups, like those that have
    radio buttons in several groups, divided by labels and inside indented
    boxes.

    :param group: the button group
    :type group: QButtonGroup
    :param label: string label or a pixmap for the button
    :type label: str or QPixmap
    :param insertInto: the widget into which the radio button is inserted
    :type insertInto: QWidget
    :rtype: QRadioButton
    """
    if addSpace is not None:
        warnings.warn("'addSpace' has been deprecated. Use gui.separator instead.",
                      DeprecationWarning, stacklevel=2)
    i = len(group.buttons)
    if isinstance(label, str):
        w = QtWidgets.QRadioButton(label)
    else:
        w = QtWidgets.QRadioButton(str(i))
        w.setIcon(QtGui.QIcon(label))
    if not hasattr(group, "buttons"):
        group.buttons = []
    group.buttons.append(w)
    if id is None:
        group.group.addButton(w)
    else:
        group.group.addButton(w, id)
    w.setChecked(getdeepattr(group.ogMaster, group.ogValue) == i)

    # miscellanea for this case is weird, so we do it here
    if disabled:
        w.setDisabled(disabled)
    if tooltip is not None:
        w.setToolTip(tooltip)
    if sizePolicy:
        if isinstance(sizePolicy, tuple):
            sizePolicy = QSizePolicy(*sizePolicy)
        w.setSizePolicy(sizePolicy)
    if addToLayout:
        dest = insertInto or group
        dest.layout().addWidget(w, stretch)
    return w


class DelayedNotification(QObject):
    """
    A proxy for successive calls/signals that emits a signal
    only when there are no calls for a given time.

    Also allows for mechanism that prevents successive equivalent calls:
    ff values are passed to the "changed" method, a signal is only emitted
    if the last passed values differ from the last passed values at the
    previous emission.
    """
    notification = Signal()

    def __init__(self, parent=None, timeout=500):
        super().__init__(parent=parent)
        self.timeout = timeout
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.notify_immediately)
        self._did_notify = False  # if anything was sent at all
        self._last_value = None  # last value passed to changed
        self._last_notified = None  # value at the last notification

    def changed(self, *args):
        self._last_value = args
        self._timer.start(self.timeout)

    def notify_immediately(self):
        self._timer.stop()
        if self._did_notify and self._last_notified == self._last_value:
            return
        self._last_notified = self._last_value
        self._did_notify = True
        self.notification.emit()


def hSlider(widget, master, value, box=None, minValue=0, maxValue=10, step=1,
            callback=None, callback_finished=None, label=None, labelFormat=" %d", ticks=False,
            divideFactor=1.0, vertical=False, createLabel=True, width=None,
            intOnly=True, **misc):
    """
    Construct a slider.

    :param widget: the widget into which the box is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param value: the master's attribute with which the value is synchronized
    :type value:  str
    :param box: tells whether the widget has a border, and its label
    :type box: int or str or None
    :param label: a label that is inserted into the box
    :type label: str
    :param callback: a function that is called when the value is changed
    :type callback: function
    :param callback_finished: a function that is called when the slider value
        stopped changing for at least 500 ms or when the slider is released
    :type callback_finished: function
    :param minValue: minimal value
    :type minValue: int or float
    :param maxValue: maximal value
    :type maxValue: int or float
    :param step: step size
    :type step: int or float
    :param labelFormat: the label format; default is `" %d"`
    :type labelFormat: str
    :param ticks: if set to `True`, ticks are added below the slider
    :type ticks: bool
    :param divideFactor: a factor with which the displayed value is divided
    :type divideFactor: float
    :param vertical: if set to `True`, the slider is vertical
    :type vertical: bool
    :param createLabel: unless set to `False`, labels for minimal, maximal
        and the current value are added to the widget
    :type createLabel: bool
    :param width: the width of the slider
    :type width: int
    :param intOnly: if `True`, the slider value is integer (the slider is
        of type :obj:`QSlider`) otherwise it is float
        (:obj:`FloatSlider`, derived in turn from :obj:`QSlider`).
    :type intOnly: bool
    :rtype: :obj:`QSlider` or :obj:`FloatSlider`
    """
    sliderBox = hBox(widget, box, addToLayout=False)
    if label:
        widgetLabel(sliderBox, label)
    sliderOrient = Qt.Vertical if vertical else Qt.Horizontal
    if intOnly:
        slider = Slider(sliderOrient, sliderBox)
        slider.setRange(minValue, maxValue)
        if step:
            slider.setSingleStep(step)
            slider.setPageStep(step)
            slider.setTickInterval(step)
        signal = slider.valueChanged[int]
    else:
        slider = FloatSlider(sliderOrient, minValue, maxValue, step)
        signal = slider.valueChangedFloat[float]
    sliderBox.layout().addWidget(slider)
    slider.setValue(getdeepattr(master, value))
    if width:
        slider.setFixedWidth(width)
    if ticks:
        slider.setTickPosition(QSlider.TicksBelow)
        slider.setTickInterval(ticks)

    if createLabel:
        label = QLabel(sliderBox)
        sliderBox.layout().addWidget(label)
        label.setText(labelFormat % minValue)
        width1 = label.sizeHint().width()
        label.setText(labelFormat % maxValue)
        width2 = label.sizeHint().width()
        label.setFixedSize(max(width1, width2), label.sizeHint().height())
        txt = labelFormat % (getdeepattr(master, value) / divideFactor)
        label.setText(txt)
        label.setLbl = lambda x: \
            label.setText(labelFormat % (x / divideFactor))
        signal.connect(label.setLbl)

    connectControl(master, value, callback, signal, CallFrontHSlider(slider))

    if callback_finished:
        dn = DelayedNotification(slider, timeout=500)
        dn.notification.connect(callback_finished)
        signal.connect(dn.changed)
        slider.sliderReleased.connect(dn.notify_immediately)

    miscellanea(slider, sliderBox, widget, **misc)
    return slider


def labeledSlider(widget, master, value, box=None,
                  label=None, labels=(), labelFormat=" %d", ticks=False,
                  callback=None, vertical=False, width=None, **misc):
    """
    Construct a slider with labels instead of numbers.

    :param widget: the widget into which the box is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param value: the master's attribute with which the value is synchronized
    :type value:  str
    :param box: tells whether the widget has a border, and its label
    :type box: int or str or None
    :param label: a label that is inserted into the box
    :type label: str
    :param labels: labels shown at different slider positions
    :type labels: tuple of str
    :param callback: a function that is called when the value is changed
    :type callback: function

    :param ticks: if set to `True`, ticks are added below the slider
    :type ticks: bool
    :param vertical: if set to `True`, the slider is vertical
    :type vertical: bool
    :param width: the width of the slider
    :type width: int
    :rtype: :obj:`QSlider`
    """
    sliderBox = hBox(widget, box, addToLayout=False)
    if label:
        widgetLabel(sliderBox, label)
    sliderOrient = Qt.Vertical if vertical else Qt.Horizontal
    slider = Slider(sliderOrient, sliderBox)
    slider.ogValue = value
    slider.setRange(0, len(labels) - 1)
    slider.setSingleStep(1)
    slider.setPageStep(1)
    slider.setTickInterval(1)
    sliderBox.layout().addWidget(slider)
    slider.setValue(labels.index(getdeepattr(master, value)))
    if width:
        slider.setFixedWidth(width)
    if ticks:
        slider.setTickPosition(QSlider.TicksBelow)
        slider.setTickInterval(ticks)

    max_label_size = 0
    slider.value_label = value_label = QLabel(sliderBox)
    value_label.setAlignment(Qt.AlignRight)
    sliderBox.layout().addWidget(value_label)
    for lb in labels:
        value_label.setText(labelFormat % lb)
        max_label_size = max(max_label_size, value_label.sizeHint().width())
    value_label.setFixedSize(max_label_size, value_label.sizeHint().height())
    value_label.setText(getdeepattr(master, value))
    if isinstance(labelFormat, str):
        value_label.set_label = lambda x: \
            value_label.setText(labelFormat % x)
    else:
        value_label.set_label = lambda x: value_label.setText(labelFormat(x))
    slider.valueChanged[int].connect(value_label.set_label)

    connectControl(master, value, callback, slider.valueChanged[int],
                   CallFrontLabeledSlider(slider, labels),
                   CallBackLabeledSlider(slider, master, labels))

    miscellanea(slider, sliderBox, widget, **misc)
    return slider


def valueSlider(widget, master, value, box=None, label=None,
                values=(), labelFormat=" %d", ticks=False,
                callback=None, vertical=False, width=None, **misc):
    """
    Construct a slider with different values.

    :param widget: the widget into which the box is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param value: the master's attribute with which the value is synchronized
    :type value:  str
    :param box: tells whether the widget has a border, and its label
    :type box: int or str or None
    :param label: a label that is inserted into the box
    :type label: str
    :param values: values at different slider positions
    :type values: list of int
    :param labelFormat: label format; default is `" %d"`; can also be a function
    :type labelFormat: str or func
    :param callback: a function that is called when the value is changed
    :type callback: function

    :param ticks: if set to `True`, ticks are added below the slider
    :type ticks: bool
    :param vertical: if set to `True`, the slider is vertical
    :type vertical: bool
    :param width: the width of the slider
    :type width: int
    :rtype: :obj:`QSlider`
    """
    if isinstance(labelFormat, str):
        labelFormat = lambda x, f=labelFormat: f % x

    sliderBox = hBox(widget, box, addToLayout=False)
    if label:
        widgetLabel(sliderBox, label)
    slider_orient = Qt.Vertical if vertical else Qt.Horizontal
    slider = Slider(slider_orient, sliderBox)
    slider.ogValue = value
    slider.setRange(0, len(values) - 1)
    slider.setSingleStep(1)
    slider.setPageStep(1)
    slider.setTickInterval(1)
    sliderBox.layout().addWidget(slider)
    slider.setValue(values.index(getdeepattr(master, value)))
    if width:
        slider.setFixedWidth(width)
    if ticks:
        slider.setTickPosition(QSlider.TicksBelow)
        slider.setTickInterval(ticks)

    max_label_size = 0
    slider.value_label = value_label = QLabel(sliderBox)
    value_label.setAlignment(Qt.AlignRight)
    sliderBox.layout().addWidget(value_label)
    for lb in values:
        value_label.setText(labelFormat(lb))
        max_label_size = max(max_label_size, value_label.sizeHint().width())
    value_label.setFixedSize(max_label_size, value_label.sizeHint().height())
    value_label.setText(labelFormat(getdeepattr(master, value)))
    value_label.set_label = lambda x: value_label.setText(labelFormat(values[x]))
    slider.valueChanged[int].connect(value_label.set_label)

    connectControl(master, value, callback, slider.valueChanged[int],
                   CallFrontLabeledSlider(slider, values),
                   CallBackLabeledSlider(slider, master, values))

    miscellanea(slider, sliderBox, widget, **misc)
    return slider


def comboBox(widget, master, value, box=None, label=None, labelWidth=None,
             orientation=Qt.Vertical, items=(), callback=None,
             sendSelectedValue=None, emptyString=None, editable=False,
             contentsLength=None, searchable=False, *, model=None, **misc):
    """
    Construct a combo box.

    The `value` attribute of the `master` contains the text or the
    index of the selected item.

    :param widget: the widget into which the box is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWWidget or OWComponent
    :param value: the master's attribute with which the value is synchronized
    :type value:  str
    :param box: tells whether the widget has a border, and its label
    :type box: int or str or None
    :param orientation: tells whether to put the label above or to the left
    :type orientation: `Qt.Horizontal` (default), `Qt.Vertical` or
        instance of `QLayout`
    :param label: a label that is inserted into the box
    :type label: str
    :param labelWidth: the width of the label
    :type labelWidth: int
    :param callback: a function that is called when the value is changed
    :type callback: function
    :param items: items (optionally with data) that are put into the box
    :type items: tuple of str or tuples
    :param sendSelectedValue: decides whether the `value` contains the text
        of the selected item (`True`) or its index (`False`). If omitted
        (or `None`), the type will match the current value type, or index,
        if the current value is `None`.
    :type sendSelectedValue: bool or `None`
    :param emptyString: the string value in the combo box that gets stored as
        an empty string in `value`
    :type emptyString: str
    :param editable: a flag telling whether the combo is editable. Editable is
        ignored when searchable=True.
    :type editable: bool
    :param int contentsLength: Contents character length to use as a
        fixed size hint. When not None, equivalent to::

            combo.setSizeAdjustPolicy(
                QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(contentsLength)
    :param searchable: decides whether combo box has search-filter option
    :type searchable: bool
    :rtype: QComboBox
    """
    widget_label = None
    if box or label:
        hb = widgetBox(widget, box, orientation,
                       addToLayout=misc.get('addToLayout', True))
        misc['addToLayout'] = False
        if label is not None:
            widget_label = widgetLabel(hb, label, labelWidth)
    else:
        hb = widget

    if searchable:
        combo = OrangeComboBoxSearch(hb)
        if editable:
            warnings.warn(
                "'editable' is ignored for searchable combo box."
            )
    else:
        combo = OrangeComboBox(hb, editable=editable)

    if contentsLength is not None:
        combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(contentsLength)

    combo.box = hb
    combo.label = widget_label
    for item in items:
        if isinstance(item, (tuple, list)):
            combo.addItem(*item)
        else:
            combo.addItem(str(item))

    if value:
        combo.setObjectName(value)
        cindex = getdeepattr(master, value)
        if model is not None:
            combo.setModel(model)
        if isinstance(model, PyListModel):
            callfront = CallFrontComboBoxModel(combo, model)
            callfront.action(cindex)
            connectControl(
                master, value, callback, combo.activated[int],
                callfront,
                ValueCallbackComboModel(master, value, model))
        else:
            if isinstance(cindex, str):
                if items and cindex in items:
                    cindex = items.index(cindex)
                else:
                    cindex = 0
            if cindex > combo.count() - 1:
                cindex = 0
            combo.setCurrentIndex(cindex)
            if sendSelectedValue:
                connectControl(
                    master, value, callback, combo.activated[str],
                    CallFrontComboBox(combo, emptyString),
                    ValueCallbackCombo(master, value, emptyString))
            else:
                connectControl(
                    master, value, callback, combo.activated[int],
                    CallFrontComboBox(combo, emptyString))

    if misc.pop("valueType", False):
        log.warning("comboBox no longer accepts argument 'valueType'")
    miscellanea(combo, hb, widget, **misc)
    combo.emptyString = emptyString
    return combo


# Decorator deferred allows for doing this:
#
# class MyWidget(OWBaseWidget):
#    def __init__(self):
#        ...
#        # construct some control, for instance a checkbox, which calls
#        # a deferred commit
#        cb = gui.checkbox(..., callback=commit.deferred)
#        ...
#        gui.auto_commit(..., commit=commit)
#
#     @deferred
#     def commit(self):
#         ...
#
#     def some_method(self):
#         ...
#         self.commit.now()
#
#     def another_method(self):
#         ...
#         self.commit.deferred()
#
# Calling self.commit() will raise an exception that one must choose to call
# either `now` or `deferred`.
#
# 1. `now` and `deferred` are created by `gui.auto_commit` and must be
#    stored to the instance.
#
#    Hence, auto_commit stores `now` and `then` into `master.__<name>_data.now`
#    and`master.<name>_data.deferred`, where <data> is the name of the
#    decorated method (usually "commit").
#
# 2. Calling a decorated self.commit() should raise an exception that one
#    must choose between `now` and `deferred` ... unless when calling
#    super().commit() from overriden commit.
#
#    Decorator would thus replace the method with a function (or a callable)
#    that raises an exception ... except for when calling super().
#
#    With everything else in place, the only simple way to allow calling super
#    that I see is to have a flag (..._data.commit_depth) that we're already
#    within commit.If we're in a commit, we allow calling the decorated commit,
#    otherwise raise an exception. `commit` is usually not called from multiple
#    threads, and even if is is, the only consequence would be that we will
#    (in rare, unfortunate cases with very exact timing) allow calling
#    self.commit instead of (essentially) self.commit.now instead of raising
#    a rather diagnostic exception. This is a non-issue.
#
# 3. `now` and `deferred` must have a reference to the widget instance
#    (that is, to what will be bound to `self` in the actual `commit`.
#
#    Therefore, we cannot simply have a `commit` to which we attach `now`
#    and `deferred` because `commit.now` would then be a function - one and the
#    same function for all widgets of that class, not a method bound to a
#    particular instance's commit.
#
#    To solve this problem, the decorated `commit` is not a function but a
#    property (of class `DeferrerProperty`) that returns a callable object
#    (of class `Deferred`). When the property is retrieved, we get a reference
#    to the instance which is kept in the closure. `Deferred` than provides
#    `now` and `deferred`.

# 4. Although `now` and `deferred` are constructed only in gui.auto_commit,
#    they (esp. `deferred`) must by available before that - not for
#    being called but for being stored as a callback for the checkbox.
#
#    To solve this problem, `Deferred` returns either a `..._data.now` and
#    `..._data.deferred` set by auto_commit; if they are not yet set, it returns
#    a lambda that calls them, essentially thunking a function that does not
#    yet exist.
#
# 5. `now` and `deferred` are set by `auto_commit` as well as by mocking in
#    unit tests. Because on the outside we only see commit.now and
#    commit.deffered (although they are stored elsewhere, not as attributes
#    of commit), they need to pretend to be attributes. Hence we patch
#    __setattr__, __getattribute__ and __delattr__.

# The type hint is somewhat wrong: the decorator returns a property, which is
# a function, but in practice it's the same. PyCharm correctly recognizes it.
if sys.version_info >= (3, 8):
    from typing import Protocol


    class DeferredFunc(Protocol):
        def deferred(self) -> None:
            ...

        def now(self) -> None:
            ...
else:
    from typing import Any
    DeferredFunc = Any


if sys.version_info >= (3, 7):
    from typing import Optional, Callable
    from dataclasses import dataclass

    @dataclass
    class DeferredData:
        # now and deferred are set by auto_commit
        now: Optional[Callable] = None
        deferred: Optional[Callable] = None
        # if True, data was changed while auto was disabled,
        # so enabling auto commit must call `func`
        dirty: bool = False
        # A flag (counter) telling that we're within commit and
        # super().commit() should not raise an exception
        commit_depth: int = 0
else:
    class DeferredData:
        def __init__(self):
            self.now = self.deferred = None
            self.dirty = False
            self.commit_depth = 0


def deferred(func) -> DeferredFunc:
    name = func.__name__

    # Deferred method is turned into a property that returns a class, with
    # __call__ that raises an exception about being deferred

    class DeferrerProperty:
        def __get__(self, instance, owner=None):
            if instance is None:
                # We come here is somebody retrieves, e.g. OWTable.commit
                data = None
            else:
                # `DeferredData` is created once per instance
                data = instance.__dict__.setdefault(f"__{name}_data",
                                                    DeferredData())

            class Deferred:
                # A property that represents commit. Its closure include
                # - func: the original commit method
                # - instance: a widget instance
                # and, for practicality
                # - data: a data class containing `now`, `deferred`, `dirty`
                #         and `commit_depth` for this `instance`
                # - name: name of the method being decorate (usually "commit")

                # Name of the function being decorated, copied to a standard
                # attribute; used, for instance, in auto_commit to check for
                # decorated overriden methods and in exception messages
                __name__ = name

                # A flag that tells an observer that the method is decorated
                # auto_commit uses it to check that a widget that overrides
                # a decorated method also decorates its method
                decorated = True

                @classmethod
                def __call__(cls):
                    # Semantically, decorated method is replaced by this one,
                    # which raises an exception except in super calls.

                    # If commit_depth > 0, we're calling super (assuming
                    # no threading on commit!)
                    if data.commit_depth:
                        cls.call()
                    else:
                        raise RuntimeError(
                            "This function is deferred; explicitly call "
                            f"{name}.deferred or {name}.now")

                @staticmethod
                def call():
                    data.commit_depth += 1
                    try:
                        acting_func = instance.__dict__.get(name, func)
                        acting_func(instance)
                    finally:
                        data.commit_depth -= 1

                def __setattr__(self, key, value):
                    if key in ("now", "deferred"):
                        setattr(data, key, value)
                    else:
                        super().__setattr__(key, value)

                def __getattribute__(self, key):
                    if key in ("now", "deferred"):
                        # If auto_commit already set a function, return it.
                        # If not, return that function that calls a function,
                        # which will later be set by auto_commit
                        value = getattr(data, key)
                        if value is not None:
                            return value
                        else:
                            return lambda: getattr(data, key)()
                    return super().__getattribute__(key)

                def __delattr__(self, key):
                    if key in ("now", "deferred"):
                        setattr(data, key, None)
                    else:
                        super().__delattr__(self, key)

                @property
                def dirty(_):
                    return data.dirty

                @dirty.setter
                def dirty(_, value):
                    data.dirty = value

            return Deferred()

        def __set__(self, instance, value):
            raise ValueError(
                f"decorated {name} can't be mocked; "
                f"mock '{name}.now' and/or '{name}.deferred'.")

    return DeferrerProperty()


def auto_commit(widget, master, value, label, auto_label=None, box=False,
                checkbox_label=None, orientation=None, commit=None,
                callback=None, **misc):
    """
    Add a commit button with auto-commit check box.

    When possible, use auto_apply or auto_send instead of auto_commit.

    The widget must have a commit method and a setting that stores whether
    auto-commit is on.

    The function replaces the commit method with a new commit method that
    checks whether auto-commit is on. If it is, it passes the call to the
    original commit, otherwise it sets the dirty flag.

    The checkbox controls the auto-commit. When auto-commit is switched on, the
    checkbox callback checks whether the dirty flag is on and calls the original
    commit.

    Important! Do not connect any signals to the commit before calling
    auto_commit.

    :param widget: the widget into which the box with the button is inserted
    :type widget: QWidget or None
    :param value: the master's attribute which stores whether the auto-commit
        is on
    :type value:  str
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param label: The button label
    :type label: str
    :param auto_label: The label used when auto-commit is on; default is
        `label + " Automatically"`
    :type auto_label: str
    :param commit: master's method to override ('commit' by default)
    :type commit: function
    :param callback: function to call whenever the checkbox's statechanged
    :type callback: function
    :param box: tells whether the widget has a border, and its label
    :type box: int or str or None
    :return: the box
    """
    commit = commit or getattr(master, 'commit')
    if isinstance(commit, LambdaType):
        commit_name = next(LAMBDA_NAME)
    else:
        commit_name = commit.__name__
    decorated = hasattr(commit, "deferred")

    def checkbox_toggled():
        if getattr(master, value):
            btn.setText(auto_label)
            btn.setEnabled(False)
            if is_dirty():
                do_commit()
        else:
            btn.setText(label)
            btn.setEnabled(True)
        if callback:
            callback()

    if decorated:
        def is_dirty():
            return commit.dirty

        def set_dirty(state):
            commit.dirty = state
    else:
        dirty = False

        def is_dirty():
            return dirty

        def set_dirty(state):
            nonlocal dirty
            dirty = state

    def conditional_commit():
        if getattr(master, value):
            do_commit()
        else:
            set_dirty(True)

    def do_commit():
        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        try:
            if decorated:
                commit.call()
            else:
                commit()
            set_dirty(False)
        finally:
            QApplication.restoreOverrideCursor()

    set_dirty(False)

    if not auto_label:
        if checkbox_label:
            auto_label = label
        else:
            auto_label = label.title() + " Automatically"
    if isinstance(box, QWidget):
        b = box
        addToLayout = False
    else:
        if orientation is None:
            orientation = Qt.Vertical if checkbox_label else Qt.Horizontal
        b = widgetBox(widget, box=box, orientation=orientation,
                      addToLayout=False, margin=0, spacing=0)
        addToLayout = misc.get('addToLayout', True)
        if addToLayout and widget and \
                not widget.layout().isEmpty() \
                and _is_horizontal(orientation) \
                and isinstance(widget.layout(), QtWidgets.QHBoxLayout):
            # put a separator before the checkbox
            separator(b, 16, 0)

    b.checkbox = cb = checkBox(b, master, value, checkbox_label,
                               callback=checkbox_toggled, tooltip=auto_label)
    if _is_horizontal(orientation):
        w = b.style().pixelMetric(QStyle.PM_CheckBoxLabelSpacing)
        separator(b, w, 0)
    cb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    b.button = btn = VariableTextPushButton(
        b, text=label, textChoiceList=[label, auto_label], clicked=do_commit)
    btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    if b.layout() is not None:
        if is_macstyle():
            btnpaddingbox = vBox(b, margin=0, spacing=0)
            separator(btnpaddingbox, 0, 4)
            btnpaddingbox.layout().addWidget(btn)
        else:
            b.layout().addWidget(btn)

    if not checkbox_label:
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    checkbox_toggled()

    if decorated:
        commit.now = do_commit
        commit.deferred = conditional_commit
    else:
        if not isinstance(commit, LambdaType):
            for supertype in type(master).mro()[1:]:
                inherited_commit = getattr(supertype, commit_name, None)
                if getattr(inherited_commit, "decorated", False):
                    raise RuntimeError(
                        f"{type(master).__name__}.{commit_name} must be"
                        "decorated with gui.deferred because it overrides"
                        f"a decorated {supertype.__name__}.")
            warnings.warn(
                f"decorate {type(master).__name__}.{commit_name} "
                "with @gui.deferred and then explicitly call "
                f"{commit_name}.now or {commit_name}.deferred.")

        # TODO: I suppose we don't need to to this for lambdas, do we?
        # Maybe we can change `else` to `elif not isinstance(commit, LambdaType)
        # and remove `if` that follows?
        setattr(master, 'unconditional_' + commit_name, commit)
        setattr(master, commit_name, conditional_commit)

    misc['addToLayout'] = addToLayout
    miscellanea(b, widget, widget, **misc)

    cb.setAttribute(Qt.WA_LayoutUsesWidgetRect)
    btn.setAttribute(Qt.WA_LayoutUsesWidgetRect)

    return b


def auto_send(widget, master, value="auto_send", **kwargs):
    """
    Convenience function that creates an auto_commit box,
    for widgets that send selected data (as opposed to applying changes).

    :param widget: the widget into which the box with the button is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param value: the master's attribute which stores whether the auto-commit (default 'auto_send')
    :type value:  str
    :return: the box
    """
    return auto_commit(widget, master, value, "Send Selection", "Send Automatically", **kwargs)


def auto_apply(widget, master, value="auto_apply", **kwargs):
    """
    Convenience function that creates an auto_commit box,
    for widgets that apply changes (as opposed to sending a selection).

    :param widget: the widget into which the box with the button is inserted
    :type widget: QWidget or None
    :param master: master widget
    :type master: OWBaseWidget or OWComponent
    :param value: the master's attribute which stores whether the auto-commit (default 'auto_apply')
    :type value:  str
    :return: the box
    """
    return auto_commit(widget, master, value, "Apply", "Apply Automatically", **kwargs)


def connectControl(master, value, f, signal,
                   cfront, cback=None, cfunc=None, fvcb=None):
    cback = cback or value and ValueCallback(master, value, fvcb)
    if cback:
        if signal:
            signal.connect(cback)
        cback.opposite = cfront
        if value and cfront:
            master.connect_control(value, cfront)
    cfunc = cfunc or f and FunctionCallback(master, f)
    if cfunc:
        if signal:
            signal.connect(cfunc)
        cfront.opposite = tuple(x for x in (cback, cfunc) if x)
    return cfront, cback, cfunc


@contextlib.contextmanager
def disable_opposite(obj):
    opposite = getattr(obj, "opposite", None)
    if opposite:
        opposite.disabled += 1
        try:
            yield
        finally:
            if opposite:
                opposite.disabled -= 1


class ControlledCallback:
    def __init__(self, widget, attribute, f=None):
        self.widget = widget
        self.attribute = attribute
        self.func = f
        self.disabled = 0
        if isinstance(widget, dict):
            return  # we can't assign attributes to dict
        if not hasattr(widget, "callbackDeposit"):
            widget.callbackDeposit = []
        widget.callbackDeposit.append(self)

    def acyclic_setattr(self, value):
        if self.disabled:
            return
        if self.func:
            if self.func in (int, float) and (
                    not value or isinstance(value, str) and value in "+-"):
                value = self.func(0)
            else:
                value = self.func(value)
        with disable_opposite(self):
            if isinstance(self.widget, dict):
                self.widget[self.attribute] = value
            else:
                setattr(self.widget, self.attribute, value)


class ValueCallback(ControlledCallback):
    # noinspection PyBroadException
    def __call__(self, value):
        if value is None:
            return
        self.acyclic_setattr(value)


class ValueCallbackCombo(ValueCallback):
    def __init__(self, widget, attribute, emptyString=""):
        super().__init__(widget, attribute)
        self.emptyString = emptyString

    def __call__(self, value):
        if value == self.emptyString:
            value = ""
        return super().__call__(value)


class ValueCallbackComboModel(ValueCallback):
    def __init__(self, widget, attribute, model):
        super().__init__(widget, attribute)
        self.model = model

    def __call__(self, index):
        # Can't use super here since, it doesn't set `None`'s?!
        return self.acyclic_setattr(self.model[index])


class ValueCallbackLineEdit(ControlledCallback):
    def __init__(self, control, widget, attribute, f=None):
        ControlledCallback.__init__(self, widget, attribute, f)
        self.control = control

    # noinspection PyBroadException
    def __call__(self, value):
        if value is None:
            return
        pos = self.control.cursorPosition()
        self.acyclic_setattr(value)
        self.control.setCursorPosition(pos)


class SetLabelCallback:
    def __init__(self, widget, label, format="%5.2f", f=None):
        self.widget = widget
        self.label = label
        self.format = format
        self.f = f
        if hasattr(widget, "callbackDeposit"):
            widget.callbackDeposit.append(self)
        self.disabled = 0

    def __call__(self, value):
        if not self.disabled and value is not None:
            if self.f:
                value = self.f(value)
            self.label.setText(self.format % value)


class FunctionCallback:
    def __init__(self, master, f, widget=None, id=None, getwidget=False):
        self.master = master
        self.widget = widget
        self.func = f
        self.id = id
        self.getwidget = getwidget
        if hasattr(master, "callbackDeposit"):
            master.callbackDeposit.append(self)
        self.disabled = 0

    def __call__(self, *value):
        if not self.disabled and value is not None:
            kwds = {}
            if self.id is not None:
                kwds['id'] = self.id
            if self.getwidget:
                kwds['widget'] = self.widget
            if isinstance(self.func, list):
                for func in self.func:
                    func(**kwds)
            else:
                self.func(**kwds)


class CallBackRadioButton:
    def __init__(self, control, widget):
        self.control = control
        self.widget = widget
        self.disabled = False

    def __call__(self, *_):  # triggered by toggled()
        if not self.disabled and self.control.ogValue is not None:
            arr = [butt.isChecked() for butt in self.control.buttons]
            self.widget.__setattr__(self.control.ogValue, arr.index(1))


class CallBackLabeledSlider:
    def __init__(self, control, widget, lookup):
        self.control = control
        self.widget = widget
        self.lookup = lookup
        self.disabled = False

    def __call__(self, *_):
        if not self.disabled and self.control.ogValue is not None:
            self.widget.__setattr__(self.control.ogValue,
                                    self.lookup[self.control.value()])


##############################################################################
# call fronts (change of the attribute value changes the related control)


class ControlledCallFront:
    def __init__(self, control):
        self.control = control
        self.disabled = 0

    def action(self, *_):
        pass

    def __call__(self, *args):
        if not self.disabled:
            opposite = getattr(self, "opposite", None)
            if opposite:
                try:
                    for op in opposite:
                        op.disabled += 1
                    self.action(*args)
                finally:
                    for op in opposite:
                        op.disabled -= 1
            else:
                self.action(*args)


class CallFrontSpin(ControlledCallFront):
    def action(self, value):
        if value is not None:
            self.control.setValue(value)


class CallFrontDoubleSpin(ControlledCallFront):
    def action(self, value):
        if value is not None:
            self.control.setValue(value)


class CallFrontCheckBox(ControlledCallFront):
    def action(self, value):
        if value is not None:
            values = [Qt.Unchecked, Qt.Checked, Qt.PartiallyChecked]
            self.control.setCheckState(values[value])


class CallFrontButton(ControlledCallFront):
    def action(self, value):
        if value is not None:
            self.control.setChecked(bool(value))


class CallFrontComboBox(ControlledCallFront):
    def __init__(self, control, emptyString=""):
        super().__init__(control)
        self.emptyString = emptyString

    def action(self, value):
        def action_str():
            items = [combo.itemText(i) for i in range(combo.count())]
            try:
                index = items.index(value or self.emptyString)
            except ValueError:
                if items:
                    msg = f"Combo '{combo.objectName()}' has no item '{value}'; " \
                          f"current items are {', '.join(map(repr, items))}."
                else:
                    msg = f"combo '{combo.objectName()}' is empty."
                warnings.warn(msg, stacklevel=5)
            else:
                self.control.setCurrentIndex(index)

        def action_int():
            if value < combo.count():
                combo.setCurrentIndex(value)
            else:
                if combo.count():
                    msg = f"index {value} is out of range " \
                          f"for combo box '{combo.objectName()}' " \
                          f"with {combo.count()} item(s)."
                else:
                    msg = f"combo box '{combo.objectName()}' is empty."
                warnings.warn(msg, stacklevel=5)

        combo = self.control
        if isinstance(value, int):
            action_int()
        else:
            action_str()


class CallFrontComboBoxModel(ControlledCallFront):
    def __init__(self, control, model):
        super().__init__(control)
        self.model = model

    def action(self, value):
        if value == "":  # the latter accomodates PyListModel
            value = None
        if value is None and None not in self.model:
            return  # e.g. values in half-initialized widgets
        if value in self.model:
            self.control.setCurrentIndex(self.model.indexOf(value))
            return
        if isinstance(value, str):
            for i, val in enumerate(self.model):
                if value == str(val):
                    self.control.setCurrentIndex(i)
                    return
        raise ValueError("Combo box does not contain item " + repr(value))


class CallFrontHSlider(ControlledCallFront):
    def action(self, value):
        if value is not None:
            self.control.setValue(value)


class CallFrontLabeledSlider(ControlledCallFront):
    def __init__(self, control, lookup):
        super().__init__(control)
        self.lookup = lookup

    def action(self, value):
        if value is not None:
            self.control.setValue(self.lookup.index(value))


class CallFrontLogSlider(ControlledCallFront):
    def action(self, value):
        if value is not None:
            if value < 1e-30:
                print("unable to set %s to %s (value too small)" %
                      (self.control, value))
            else:
                self.control.setValue(math.log10(value))


class CallFrontLineEdit(ControlledCallFront):
    def action(self, value):
        self.control.setText(str(value))


class CallFrontRadioButtons(ControlledCallFront):
    def action(self, value):
        if value < 0 or value >= len(self.control.buttons):
            value = 0
        self.control.buttons[value].setChecked(1)


class CallFrontLabel:
    def __init__(self, control, label, master):
        self.control = control
        self.label = label
        self.master = master

    def __call__(self, *_):
        self.control.setText(self.label % self.master.__dict__)

##############################################################################
## Disabler is a call-back class for check box that can disable/enable other
## widgets according to state (checked/unchecked, enabled/disable) of the
## given check box
##
## Tricky: if self.propagateState is True (default), then if check box is
## disabled the related widgets will be disabled (even if the checkbox is
## checked). If self.propagateState is False, the related widgets will be
## disabled/enabled if check box is checked/clear, disregarding whether the
## check box itself is enabled or not. (If you don't understand, see the
## code :-)
DISABLER = 1
HIDER = 2


# noinspection PyShadowingBuiltins
class Disabler:
    def __init__(self, widget, master, valueName, propagateState=True,
                 type=DISABLER):
        self.widget = widget
        self.master = master
        self.valueName = valueName
        self.propagateState = propagateState
        self.type = type

    def __call__(self, *value):
        currState = self.widget.isEnabled()
        if currState or not self.propagateState:
            if len(value):
                disabled = not value[0]
            else:
                disabled = not getdeepattr(self.master, self.valueName)
        else:
            disabled = True
        for w in self.widget.disables:
            if isinstance(w, tuple):
                if isinstance(w[0], int):
                    i = 1
                    if w[0] == -1:
                        disabled = not disabled
                else:
                    i = 0
                if self.type == DISABLER:
                    w[i].setDisabled(disabled)
                elif self.type == HIDER:
                    if disabled:
                        w[i].hide()
                    else:
                        w[i].show()
                if hasattr(w[i], "makeConsistent"):
                    w[i].makeConsistent()
            else:
                if self.type == DISABLER:
                    w.setDisabled(disabled)
                elif self.type == HIDER:
                    if disabled:
                        w.hide()
                    else:
                        w.show()

##############################################################################
# some table related widgets


# noinspection PyShadowingBuiltins
class tableItem(QTableWidgetItem):
    def __init__(self, table, x, y, text, editType=None, backColor=None,
                 icon=None, type=QTableWidgetItem.Type):
        super().__init__(type)
        if icon:
            self.setIcon(QtGui.QIcon(icon))
        if editType is not None:
            self.setFlags(editType)
        else:
            self.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable |
                          Qt.ItemIsSelectable)
        if backColor is not None:
            self.setBackground(QtGui.QBrush(backColor))
        # we add it this way so that text can also be int and sorting will be
        # done properly (as integers and not as text)
        self.setData(Qt.DisplayRole, text)
        table.setItem(x, y, self)


BarRatioRole = next(OrangeUserRole)  # Ratio for drawing distribution bars
BarBrushRole = next(OrangeUserRole)  # Brush for distribution bar

SortOrderRole = next(OrangeUserRole)  # Used for sorting


class BarItemDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, parent, brush=QtGui.QBrush(QtGui.QColor(255, 170, 127)),
                 scale=(0.0, 1.0)):
        super().__init__(parent)
        self.brush = brush
        self.scale = scale

    def paint(self, painter, option, index):
        if option.widget is not None:
            style = option.widget.style()
        else:
            style = QApplication.style()

        style.drawPrimitive(
            QStyle.PE_PanelItemViewRow, option, painter,
            option.widget)
        style.drawPrimitive(
            QStyle.PE_PanelItemViewItem, option, painter,
            option.widget)

        rect = option.rect
        val = index.data(Qt.DisplayRole)
        if isinstance(val, float):
            minv, maxv = self.scale
            val = (val - minv) / (maxv - minv)
            painter.save()
            if option.state & QStyle.State_Selected:
                painter.setOpacity(0.75)
            painter.setBrush(self.brush)
            painter.drawRect(
                rect.adjusted(1, 1, - rect.width() * (1.0 - val) - 2, -2))
            painter.restore()


class IndicatorItemDelegate(QtWidgets.QStyledItemDelegate):
    IndicatorRole = next(OrangeUserRole)

    def __init__(self, parent, role=IndicatorRole, indicatorSize=2):
        super().__init__(parent)
        self.role = role
        self.indicatorSize = indicatorSize

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        rect = option.rect
        indicator = index.data(self.role)

        if indicator:
            brush = index.data(Qt.ForegroundRole)
            if brush is None:
                brush = QtGui.QBrush(Qt.black)
            painter.save()
            painter.setRenderHints(QtGui.QPainter.Antialiasing)
            painter.setBrush(brush)
            painter.setPen(QtGui.QPen(brush, 1))
            painter.drawEllipse(rect.center(),
                                self.indicatorSize, self.indicatorSize)
            painter.restore()


class LinkStyledItemDelegate(QStyledItemDelegate):
    LinkRole = next(OrangeUserRole)

    def __init__(self, parent):
        super().__init__(parent)
        self.mousePressState = QtCore.QModelIndex(), QtCore.QPoint()
        parent.entered.connect(self.onEntered)

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QtCore.QSize(size.width(), max(size.height(), 20))

    def linkRect(self, option, index):
        if option.widget is not None:
            style = option.widget.style()
        else:
            style = QApplication.style()

        text = self.displayText(index.data(Qt.DisplayRole),
                                QtCore.QLocale.system())
        self.initStyleOption(option, index)
        textRect = style.subElementRect(
            QStyle.SE_ItemViewItemText, option, option.widget)

        if not textRect.isValid():
            textRect = option.rect
        margin = style.pixelMetric(
            QStyle.PM_FocusFrameHMargin, option, option.widget) + 1
        textRect = textRect.adjusted(margin, 0, -margin, 0)
        font = index.data(Qt.FontRole)
        if not isinstance(font, QtGui.QFont):
            font = option.font

        metrics = QtGui.QFontMetrics(font)
        elideText = metrics.elidedText(text, option.textElideMode,
                                       textRect.width())
        return metrics.boundingRect(textRect, option.displayAlignment,
                                    elideText)

    def editorEvent(self, event, model, option, index):
        if event.type() == QtCore.QEvent.MouseButtonPress and \
                self.linkRect(option, index).contains(event.pos()):
            self.mousePressState = (QtCore.QPersistentModelIndex(index),
                                    QtCore.QPoint(event.pos()))

        elif event.type() == QtCore.QEvent.MouseButtonRelease:
            link = index.data(LinkRole)
            if not isinstance(link, str):
                link = None

            pressedIndex, pressPos = self.mousePressState
            if pressedIndex == index and \
                    (pressPos - event.pos()).manhattanLength() < 5 and \
                    link is not None:
                import webbrowser
                webbrowser.open(link)
            self.mousePressState = QtCore.QModelIndex(), event.pos()

        elif event.type() == QtCore.QEvent.MouseMove:
            link = index.data(LinkRole)
            if not isinstance(link, str):
                link = None

            if link is not None and \
                    self.linkRect(option, index).contains(event.pos()):
                self.parent().viewport().setCursor(Qt.PointingHandCursor)
            else:
                self.parent().viewport().setCursor(Qt.ArrowCursor)

        return super().editorEvent(event, model, option, index)

    def onEntered(self, index):
        link = index.data(LinkRole)
        if not isinstance(link, str):
            link = None
        if link is None:
            self.parent().viewport().setCursor(Qt.ArrowCursor)

    def paint(self, painter, option, index):
        link = index.data(LinkRole)
        if not isinstance(link, str):
            link = None

        if link is not None:
            if option.widget is not None:
                style = option.widget.style()
            else:
                style = QApplication.style()
            style.drawPrimitive(
                QStyle.PE_PanelItemViewRow, option, painter,
                option.widget)
            style.drawPrimitive(
                QStyle.PE_PanelItemViewItem, option, painter,
                option.widget)

            text = self.displayText(index.data(Qt.DisplayRole),
                                    QtCore.QLocale.system())
            textRect = style.subElementRect(
                QStyle.SE_ItemViewItemText, option, option.widget)
            if not textRect.isValid():
                textRect = option.rect
            margin = style.pixelMetric(
                QStyle.PM_FocusFrameHMargin, option, option.widget) + 1
            textRect = textRect.adjusted(margin, 0, -margin, 0)
            elideText = QtGui.QFontMetrics(option.font).elidedText(
                text, option.textElideMode, textRect.width())
            painter.save()
            font = index.data(Qt.FontRole)
            if not isinstance(font, QtGui.QFont):
                font = option.font
            painter.setFont(font)
            if option.state & QStyle.State_Selected:
                color = option.palette.highlightedText().color()
            else:
                color = option.palette.link().color()
            painter.setPen(QtGui.QPen(color))
            painter.drawText(textRect, option.displayAlignment, elideText)
            painter.restore()
        else:
            super().paint(painter, option, index)


LinkRole = LinkStyledItemDelegate.LinkRole


class ColoredBarItemDelegate(QtWidgets.QStyledItemDelegate):
    """ Item delegate that can also draws a distribution bar
    """
    def __init__(self, parent=None, decimals=3, color=Qt.red):
        super().__init__(parent)
        self.decimals = decimals
        self.float_fmt = "%%.%if" % decimals
        self.color = QtGui.QColor(color)

    def displayText(self, value, locale=QtCore.QLocale()):
        if value is None or isinstance(value, float) and math.isnan(value):
            return "NA"
        if isinstance(value, float):
            return self.float_fmt % value
        return str(value)

    def sizeHint(self, option, index):
        font = self.get_font(option, index)
        metrics = QtGui.QFontMetrics(font)
        height = metrics.lineSpacing() + 8  # 4 pixel margin
        width = metrics.horizontalAdvance(
            self.displayText(index.data(Qt.DisplayRole), QtCore.QLocale())) + 8
        return QtCore.QSize(width, height)

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        text = self.displayText(index.data(Qt.DisplayRole))
        ratio, have_ratio = self.get_bar_ratio(option, index)

        rect = option.rect
        if have_ratio:
            # The text is raised 3 pixels above the bar.
            # TODO: Style dependent margins?
            text_rect = rect.adjusted(4, 1, -4, -4)
        else:
            text_rect = rect.adjusted(4, 4, -4, -4)

        painter.save()
        font = self.get_font(option, index)
        painter.setFont(font)

        if option.widget is not None:
            style = option.widget.style()
        else:
            style = QApplication.style()

        style.drawPrimitive(
            QStyle.PE_PanelItemViewRow, option, painter,
            option.widget)
        style.drawPrimitive(
            QStyle.PE_PanelItemViewItem, option, painter,
            option.widget)

        # TODO: Check ForegroundRole.
        painter.setPen(
            QtGui.QPen(text_color_for_state(option.palette, option.state)))

        align = self.get_text_align(option, index)

        metrics = QtGui.QFontMetrics(font)
        elide_text = metrics.elidedText(
            text, option.textElideMode, text_rect.width())
        painter.drawText(text_rect, align, elide_text)

        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if have_ratio:
            brush = self.get_bar_brush(option, index)

            painter.setBrush(brush)
            painter.setPen(QtGui.QPen(brush, 1))
            bar_rect = QtCore.QRect(text_rect)
            bar_rect.setTop(bar_rect.bottom() - 1)
            bar_rect.setBottom(bar_rect.bottom() + 1)
            w = text_rect.width()
            bar_rect.setWidth(max(0, min(w * ratio, w)))
            painter.drawRoundedRect(bar_rect, 2, 2)
        painter.restore()

    def get_font(self, option, index):
        font = index.data(Qt.FontRole)
        if not isinstance(font, QtGui.QFont):
            font = option.font
        return font

    def get_text_align(self, _, index):
        align = index.data(Qt.TextAlignmentRole)
        if not isinstance(align, int):
            align = Qt.AlignLeft | Qt.AlignVCenter

        return align

    def get_bar_ratio(self, _, index):
        ratio = index.data(BarRatioRole)
        return ratio, isinstance(ratio, float)

    def get_bar_brush(self, _, index):
        bar_brush = index.data(BarBrushRole)
        if not isinstance(bar_brush, (QtGui.QColor, QtGui.QBrush)):
            bar_brush = self.color
        return QtGui.QBrush(bar_brush)


class HorizontalGridDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        painter.setPen(QColor(212, 212, 212))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()
        QStyledItemDelegate.paint(self, painter, option, index)


class VerticalLabel(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)
        self.setMaximumWidth(self.sizeHint().width() + 2)
        self.setMargin(4)

    def sizeHint(self):
        metrics = QtGui.QFontMetrics(self.font())
        rect = metrics.boundingRect(self.text())
        size = QtCore.QSize(rect.height() + self.margin(),
                            rect.width() + self.margin())
        return size

    def setGeometry(self, rect):
        super().setGeometry(rect)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        rect = self.geometry()
        text_rect = QtCore.QRect(0, 0, rect.width(), rect.height())

        painter.translate(text_rect.bottomLeft())
        painter.rotate(-90)
        painter.drawText(
            QtCore.QRect(QtCore.QPoint(0, 0),
                         QtCore.QSize(rect.height(), rect.width())),
            Qt.AlignCenter, self.text())
        painter.end()


class VerticalItemDelegate(QStyledItemDelegate):
    # Extra text top/bottom margin.
    Margin = 6

    def __init__(self, extend=False):
        super().__init__()
        self._extend = extend  # extend text over cell borders

    def sizeHint(self, option, index):
        sh = super().sizeHint(option, index)
        return QtCore.QSize(sh.height() + self.Margin * 2, sh.width())

    def paint(self, painter, option, index):
        option = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(option, index)

        if not option.text:
            return

        if option.widget is not None:
            style = option.widget.style()
        else:
            style = QApplication.style()
        style.drawPrimitive(
            QStyle.PE_PanelItemViewRow, option, painter,
            option.widget)
        cell_rect = option.rect
        itemrect = QtCore.QRect(0, 0, cell_rect.height(), cell_rect.width())
        opt = QtWidgets.QStyleOptionViewItem(option)
        opt.rect = itemrect
        textrect = style.subElementRect(
            QStyle.SE_ItemViewItemText, opt, opt.widget)

        painter.save()
        painter.setFont(option.font)

        if option.displayAlignment & (Qt.AlignTop | Qt.AlignBottom):
            brect = painter.boundingRect(
                textrect, option.displayAlignment, option.text)
            diff = textrect.height() - brect.height()
            offset = max(min(diff / 2, self.Margin), 0)
            if option.displayAlignment & Qt.AlignBottom:
                offset = -offset

            textrect.translate(0, offset)
            if self._extend and brect.width() > itemrect.width():
                textrect.setWidth(brect.width())

        painter.translate(option.rect.x(), option.rect.bottom())
        painter.rotate(-90)
        painter.drawText(textrect, option.displayAlignment, option.text)
        painter.restore()

##############################################################################
# progress bar management


class ProgressBar:
    def __init__(self, widget, iterations):
        self.iter = iterations
        self.widget = widget
        self.count = 0
        self.widget.progressBarInit()
        self.finished = False

    def __del__(self):
        if not self.finished:
            self.widget.progressBarFinished(processEvents=False)

    def advance(self, count=1):
        self.count += count
        self.widget.progressBarSet(int(self.count * 100 / max(1, self.iter)))

    def finish(self):
        self.finished = True
        self.widget.progressBarFinished()


##############################################################################

def tabWidget(widget):
    w = QtWidgets.QTabWidget(widget)
    if widget.layout() is not None:
        widget.layout().addWidget(w)
    return w


def createTabPage(tab_widget, name, widgetToAdd=None, canScroll=False,
                  orientation=Qt.Vertical):
    if widgetToAdd is None:
        widgetToAdd = widgetBox(tab_widget, orientation=orientation,
                                addToLayout=0, margin=4)
    if canScroll:
        scrollArea = QtWidgets.QScrollArea()
        tab_widget.addTab(scrollArea, name)
        scrollArea.setWidget(widgetToAdd)
        scrollArea.setWidgetResizable(1)
        scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scrollArea.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    else:
        tab_widget.addTab(widgetToAdd, name)
    return widgetToAdd


def table(widget, rows=0, columns=0, selectionMode=-1, addToLayout=True):
    w = QtWidgets.QTableWidget(rows, columns, widget)
    if widget and addToLayout and widget.layout() is not None:
        widget.layout().addWidget(w)
    if selectionMode != -1:
        w.setSelectionMode(selectionMode)
    w.setHorizontalScrollMode(QtWidgets.QTableWidget.ScrollPerPixel)
    w.horizontalHeader().setSectionsMovable(True)
    return w


class VisibleHeaderSectionContextEventFilter(QtCore.QObject):
    def __init__(self, parent, itemView=None):
        super().__init__(parent)
        self.itemView = itemView

    def eventFilter(self, view, event):
        if not isinstance(event, QtGui.QContextMenuEvent):
            return False

        model = view.model()
        headers = [(view.isSectionHidden(i),
                    model.headerData(i, view.orientation(), Qt.DisplayRole))
                   for i in range(view.count())]
        menu = QtWidgets.QMenu("Visible headers", view)

        for i, (checked, name) in enumerate(headers):
            action = QtWidgets.QAction(name, menu)
            action.setCheckable(True)
            action.setChecked(not checked)
            menu.addAction(action)

            def toogleHidden(visible, section=i):
                view.setSectionHidden(section, not visible)
                if not visible:
                    return
                if self.itemView:
                    self.itemView.resizeColumnToContents(section)
                else:
                    view.resizeSection(section,
                                       max(view.sectionSizeHint(section), 10))

            action.toggled.connect(toogleHidden)
        menu.exec(event.globalPos())
        return True


def checkButtonOffsetHint(button, style=None):
    option = QtWidgets.QStyleOptionButton()
    option.initFrom(button)
    if style is None:
        style = button.style()
    if isinstance(button, QtWidgets.QCheckBox):
        pm_spacing = QStyle.PM_CheckBoxLabelSpacing
        pm_indicator_width = QStyle.PM_IndicatorWidth
    else:
        pm_spacing = QStyle.PM_RadioButtonLabelSpacing
        pm_indicator_width = QStyle.PM_ExclusiveIndicatorWidth
    space = style.pixelMetric(pm_spacing, option, button)
    width = style.pixelMetric(pm_indicator_width, option, button)
    # TODO: add other styles (Maybe load corrections from .cfg file?)
    style_correction = {"macintosh (aqua)": -2, "macintosh(aqua)": -2,
                        "plastique": 1, "cde": 1, "motif": 1}
    return space + width + \
        style_correction.get(QApplication.style().objectName().lower(), 0)


def toolButtonSizeHint(button=None, style=None):
    if button is None and style is None:
        style = QApplication.style()
    elif style is None:
        style = button.style()

    button_size = \
        style.pixelMetric(QStyle.PM_SmallIconSize) + \
        style.pixelMetric(QStyle.PM_ButtonMargin)
    return button_size


class Slider(QSlider):
    """
    Slider that disables wheel events.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()


class FloatSlider(Slider):
    """
    Slider for continuous values.

    The slider is derived from `QtGui.QSlider`, but maps from its discrete
    numbers to the desired continuous interval.
    """
    valueChangedFloat = Signal(float)

    def __init__(self, orientation, min_value, max_value, step, parent=None):
        super().__init__(orientation, parent)
        self.setScale(min_value, max_value, step)
        self.valueChanged[int].connect(self._send_value)

    def _update(self):
        self.setSingleStep(1)
        if self.min_value != self.max_value:
            self.setEnabled(True)
            self.setMinimum(int(round(self.min_value / self.step)))
            self.setMaximum(int(round(self.max_value / self.step)))
        else:
            self.setEnabled(False)

    def _send_value(self, slider_value):
        value = min(max(slider_value * self.step, self.min_value),
                    self.max_value)
        self.valueChangedFloat.emit(value)

    def setValue(self, value):
        """
        Set current value. The value is divided by `step`

        Args:
            value: new value
        """
        super().setValue(int(round(value / self.step)))

    def setScale(self, minValue, maxValue, step=0):
        """
        Set slider's ranges (compatibility with qwtSlider).

        Args:
            minValue (float): minimal value
            maxValue (float): maximal value
            step (float): step
        """
        if minValue >= maxValue:
            ## It would be more logical to disable the slider in this case
            ## (self.setEnabled(False))
            ## However, we do nothing to keep consistency with Qwt
            # TODO If it's related to Qwt, remove it
            return
        if step <= 0 or step > (maxValue - minValue):
            if isinstance(maxValue, int) and isinstance(minValue, int):
                step = 1
            else:
                step = float(minValue - maxValue) / 100.0
        self.min_value = float(minValue)
        self.max_value = float(maxValue)
        self.step = step
        self._update()

    def setRange(self, minValue, maxValue, step=1.0):
        """
        Set slider's ranges (compatibility with qwtSlider).

        Args:
            minValue (float): minimal value
            maxValue (float): maximal value
            step (float): step
        """
        # For compatibility with qwtSlider
        # TODO If it's related to Qwt, remove it
        self.setScale(minValue, maxValue, step)


class ControlGetter:
    """
    Provide access to GUI elements based on their corresponding attributes
    in widget.

    Every widget has an attribute `controls` that is an instance of this
    class, which uses the `controlled_attributes` dictionary to retrieve the
    control (e.g. `QCheckBox`, `QComboBox`...) corresponding to the attribute.
    For `OWComponents`, it returns its controls so that subsequent
    `__getattr__` will retrieve the control.
    """
    def __init__(self, widget):
        self.widget = widget

    def __getattr__(self, name):
        widget = self.widget
        callfronts = widget.controlled_attributes.get(name, None)
        if callfronts is None:
            # This must be an OWComponent
            try:
                return getattr(widget, name).controls
            except AttributeError:
                raise AttributeError(
                    "'{}' is not an attribute related to a gui element or "
                    "component".format(name))
        else:
            return callfronts[0].control


class VerticalScrollArea(QScrollArea):
    """
    A QScrollArea that can only scroll vertically because it never
    needs to scroll horizontally: it adapts its width to the contents.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.horizontalScrollBar().setEnabled(False)
        self.verticalScrollBar().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self.verticalScrollBar() and event.type() == QEvent.StyleChange:
            self.updateGeometry()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        sb = self.verticalScrollBar()
        isTransient = sb.style().styleHint(QStyle.SH_ScrollBar_Transient, widget=sb)

        if isTransient or sb.minimum() == sb.maximum():
            self.setViewportMargins(0, 0, 0, 0)
        else:
            self.setViewportMargins(0, 0, 5, 0)

        super().resizeEvent(event)
        self.updateGeometry()
        self.parent().updateGeometry()

    def sizeHint(self):
        if not self.widget():
            return super().sizeHint()

        width = self.widget().sizeHint().width()
        sb = self.verticalScrollBar()
        isTransient = sb.style().styleHint(QStyle.SH_ScrollBar_Transient, widget=sb)
        if not isTransient and sb.maximum() != sb.minimum():
            width += sb.style().pixelMetric(QStyle.PM_ScrollBarExtent, widget=sb)
            width += 5

        sh = self.widget().sizeHint()
        sh.setWidth(width)
        return sh


class CalendarWidgetWithTime(QCalendarWidget):
    def __init__(self, parent=None, time=None, format="hh:mm:ss"):
        super().__init__(parent)
        if time is None:
            time = QtCore.QTime.currentTime()
        self.timeedit = QDateTimeEdit(displayFormat=format)
        self.timeedit.setTime(time)

        self._time_layout = sublay = QtWidgets.QHBoxLayout()
        sublay.setContentsMargins(6, 6, 6, 6)
        sublay.addStretch(1)
        sublay.addWidget(QLabel("Time: "))
        sublay.addWidget(self.timeedit)
        sublay.addStretch(1)
        self.layout().addLayout(sublay)

    def minimumSize(self):
        return self.sizeHint()

    def sizeHint(self):
        size = super().sizeHint()
        size.setHeight(
            size.height()
            + self._time_layout.sizeHint().height()
            + self.layout().spacing())
        return size


class DateTimeEditWCalendarTime(QDateTimeEdit):
    def __init__(self, parent, format="yyyy-MM-dd hh:mm:ss"):
        QDateTimeEdit.__init__(self, parent)
        self.setDisplayFormat(format)
        self.setCalendarPopup(True)
        self.calendarWidget = CalendarWidgetWithTime(self)
        self.calendarWidget.timeedit.timeChanged.connect(self.set_datetime)
        self.setCalendarWidget(self.calendarWidget)

    def set_datetime(self, date_time=None):
        if date_time is None:
            date_time = QtCore.QDateTime.currentDateTime()
        if isinstance(date_time, QtCore.QTime):
            self.setDateTime(
                QtCore.QDateTime(self.date(), date_time))
        else:
            self.setDateTime(date_time)
