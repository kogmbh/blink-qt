__all__ = ['DocumentsWindow', 'DocumentState']

import os

from zope.interface import implements

from application.notification import IObserver, NotificationCenter, NotificationData
from application.python import Null
from application.python.types import MarkerType

from PyQt4 import uic
from PyQt4.QtCore import Qt, QEasingCurve, QEvent, QPointF, QPropertyAnimation, QSize, QRect, QSettings, QTimer, pyqtSignal
from PyQt4.QtCore import QAbstractListModel, QModelIndex
from PyQt4.QtGui import QAction, QBrush, QColor, QPixmap, QIcon, QPainter, QPalette, QPen, QPolygonF
from PyQt4.QtGui import QWidget, QVBoxLayout, QLabel, QLinearGradient, QListView, QMenu, QMessageBox
from PyQt4.QtGui import QStyle, QStyledItemDelegate, QStyleOption
from PyQt4.QtWebKit import QWebSettings

from sipsimple.configuration.settings import SIPSimpleSettings

from blink.documentsharing.localbackend import BlinkWebODFLocalSessionBackend
from blink.documentsharing.streambackend import BlinkWebODFStreamSessionBackend
from blink.documentsharing.webodfwidget import WebODFWidget
from blink.configuration.settings import BlinkSettings
from blink.sessions import SessionManager, StreamDescription
from blink.resources import Resources
from blink.util import run_in_gui_thread
from blink.widgets.color import ColorHelperMixin
from blink.widgets.util import ContextMenuActions, QtDynamicProperty
from blink.widgets.graph import Graph


# Document sessions
#

class StateColor(QColor):
    @property
    def stroke(self):
        return self.darker(200)

# TODO: which states should there be?
class StateColorMapping(dict):
    def __missing__(self, key):
        if key == 'offline':
            return self.setdefault(key, StateColor('#d0d0d0'))
        elif key == 'available':
            return self.setdefault(key, StateColor('#00ff00'))
        elif key == 'inactive':
            return self.setdefault(key, StateColor('#ffff00'))
        elif key == 'active':
            return self.setdefault(key, StateColor('#ff0000'))
        else:
            return StateColor(Qt.transparent) #StateColor('#d0d0d0')


class DocumentState(QLabel, ColorHelperMixin):
    state = QtDynamicProperty('state', unicode)

    def __init__(self, parent=None):
        super(DocumentState, self).__init__(parent)
        self.state_colors = StateColorMapping()
        self.state = None

    def event(self, event):
        if event.type() == QEvent.DynamicPropertyChange and event.propertyName() == 'state':
            self.update()
        return super(DocumentState, self).event(event)

    def paintEvent(self, event):
        color = self.state_colors[self.state]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0.0, Qt.transparent)
        gradient.setColorAt(1.0, color)
        painter.setBrush(QBrush(gradient))
        gradient.setColorAt(1.0, color.stroke)
        painter.setPen(QPen(QBrush(gradient), 1))
        painter.drawRoundedRect(-4, 0, self.width()+4, self.height(), 3.7, 3.7)



class Container(object): pass
class Palettes(Container): pass
class PixmapContainer(Container): pass


class DocumentSharingSessionIconLabel(QLabel):
    icon = QtDynamicProperty('icon', type=QIcon)
    selectedCompositionColor = QtDynamicProperty('selectedCompositionColor', type=QColor)

    def __init__(self, parent=None):
        super(DocumentSharingSessionIconLabel, self).__init__(parent)
        self.pixmaps = PixmapContainer()
        self.icon = None
        self.icon_size = 12
        self.selectedCompositionColor = Qt.transparent

    def event(self, event):
        if event.type() == QEvent.DynamicPropertyChange and event.propertyName() in ('icon', 'selectedCompositionColor') and self.icon is not None:
            self.pixmaps.standard = self.icon.pixmap(self.icon_size)
            self.pixmaps.selected = QPixmap(self.pixmaps.standard)
            painter = QPainter(self.pixmaps.selected)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setCompositionMode(QPainter.CompositionMode_SourceAtop)
            painter.fillRect(self.pixmaps.selected.rect(), self.selectedCompositionColor)
            painter.end()
        return super(DocumentSharingSessionIconLabel, self).event(event)

    def paintEvent(self, event):
        if self.icon is None or self.icon.isNull():
            return
        session_widget = self.parent().parent()
        style = self.style()
        painter = QPainter(self)
        margin = self.margin()
        rect = self.contentsRect().adjusted(margin, margin, -margin, -margin)
        if not self.isEnabled():
            option = QStyleOption()
            option.initFrom(self)
            pixmap = style.generatedIconPixmap(QIcon.Disabled, self.pixmaps.standard, option)
        elif session_widget.display_mode is session_widget.SelectedDisplayMode:
            pixmap = self.pixmaps.selected
        else:
            pixmap = self.pixmaps.standard
        align = style.visualAlignment(self.layoutDirection(), self.alignment())
        style.drawItemPixmap(painter, rect, align, pixmap)


ui_class, base_class = uic.loadUiType(Resources.get('document_session.ui'))

class DocumentSharingWidget(base_class, ui_class):
    class StandardDisplayMode:  __metaclass__ = MarkerType
    class AlternateDisplayMode: __metaclass__ = MarkerType
    class SelectedDisplayMode:  __metaclass__ = MarkerType

    def __init__(self, parent=None):
        super(DocumentSharingWidget, self).__init__(parent)
        with Resources.directory:
            self.setupUi(self)
        self.palettes = Palettes()
        self.palettes.standard = self.palette()
        self.palettes.alternate = self.palette()
        self.palettes.selected = self.palette()
        self.palettes.standard.setColor(QPalette.Window,  self.palettes.standard.color(QPalette.Base))          # We modify the palettes because only the Oxygen theme honors the BackgroundRole if set
        self.palettes.alternate.setColor(QPalette.Window, self.palettes.standard.color(QPalette.AlternateBase)) # AlternateBase set to #f0f4ff or #e0e9ff by designer
        self.palettes.selected.setColor(QPalette.Window,  self.palettes.standard.color(QPalette.Highlight))     # #0066cc #0066d5 #0066dd #0066aa (0, 102, 170) '#256182' (37, 97, 130), #2960a8 (41, 96, 168), '#2d6bbc' (45, 107, 188), '#245897' (36, 88, 151) #0044aa #0055d4
        self.setBackgroundRole(QPalette.Window)
        self.display_mode = self.StandardDisplayMode
        self.locally_modified_icon.installEventFilter(self)
        self.widget_layout.invalidate()
        self.widget_layout.activate()
        #self.setAttribute(103) # Qt.WA_DontShowOnScreen == 103 and is missing from pyqt, but is present in qt and pyside -Dan
        #self.show()

    def _get_display_mode(self):
        return self.__dict__['display_mode']

    def _set_display_mode(self, value):
        if value not in (self.StandardDisplayMode, self.AlternateDisplayMode, self.SelectedDisplayMode):
            raise ValueError("invalid display_mode: %r" % value)
        old_mode = self.__dict__.get('display_mode', None)
        new_mode = self.__dict__['display_mode'] = value
        if new_mode == old_mode:
            return
        if new_mode is self.StandardDisplayMode:
            self.setPalette(self.palettes.standard)
            self.setForegroundRole(QPalette.WindowText)
            self.name_label.setForegroundRole(QPalette.WindowText)
            self.info_label.setForegroundRole(QPalette.Dark)
        elif new_mode is self.AlternateDisplayMode:
            self.setPalette(self.palettes.alternate)
            self.setForegroundRole(QPalette.WindowText)
            self.name_label.setForegroundRole(QPalette.WindowText)
            self.info_label.setForegroundRole(QPalette.Dark)
        elif new_mode is self.SelectedDisplayMode:
            self.setPalette(self.palettes.selected)
            self.setForegroundRole(QPalette.HighlightedText)
            self.name_label.setForegroundRole(QPalette.HighlightedText)
            self.info_label.setForegroundRole(QPalette.HighlightedText)

    display_mode = property(_get_display_mode, _set_display_mode)
    del _get_display_mode, _set_display_mode

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.ShowToParent, QEvent.HideToParent):
            self.widget_layout.invalidate()
            self.widget_layout.activate()
        return False

    def paintEvent(self, event):
        super(DocumentSharingWidget, self).paintEvent(event)
        if self.display_mode == self.SelectedDisplayMode and self.state_label.state is not None:
            rect = self.state_label.geometry()
            rect.setWidth(self.width() - rect.x())
            gradient = QLinearGradient(0, 0, 1, 0)
            gradient.setCoordinateMode(QLinearGradient.ObjectBoundingMode)
            gradient.setColorAt(0.0, Qt.transparent)
            gradient.setColorAt(1.0, Qt.white)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.fillRect(rect, QBrush(gradient))
            painter.end()

    def update_content(self, sessionItem):
        self.name_label.setText(sessionItem.name)
        self.info_label.setText(sessionItem.info)
        self.icon_label.setPixmap(sessionItem.pixmap)
        self.state_label.state = sessionItem.state
        self.locally_modified_icon.setVisible(sessionItem.locallyModified)

del ui_class, base_class


class DocumentSharingSessionItem(object):
    implements(IObserver)

    size_hint = QSize(200, 36)

    def __init__(self, stream):
        self.stream = stream
        self.blink_session = stream.blink_session
        self.blink_session.items.documentsharing = self
        self.session_uuid = stream.session_uuid
        self.document_title = stream.document_title
        self.isHost = (stream.mode == 'host')

        notification_center = NotificationCenter()
        notification_center.add_observer(self, sender=stream)

        if self.isHost:
            self.backend = BlinkWebODFLocalSessionBackend(stream)
        else:
            self.backend = BlinkWebODFStreamSessionBackend(stream)
        self._statusText = 'Connecting...'
        self._blink_fail_reason = None
        self._leaving = False

        #self.participants_model = DocumentSharingParticipantModel(document_sharing)
        self.widget = DocumentSharingWidget(None)
        self.widget.update_content(self)

        notification_center.add_observer(self, sender=self.widget)

    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, self.stream)

    @property
    def name(self):
        return self.document_title if self.document_title is not None else "Document"

    @property
    def info(self):
        return self._statusText

    @property
    def state(self):
        return "offline" #TODO: 

    @property
    def locallyModified(self):
        return False # TODO:

    @property
    def icon(self):
        return QIcon(Resources.get('icons/file-transfer.png')) # TODO

    @property
    def pixmap(self):
        return QIcon(Resources.get('icons/file-transfer.png')).pixmap(32) # TODO

    def handleReconnection(self, stream):
        notification_center = NotificationCenter()
        # TODO: check if still needed
        notification_center.remove_observer(self, sender=self.stream)

        self.stream = stream
        notification_center.add_observer(self, sender=stream)
        self.backend.handleReconnection(stream)

    def leaveSession(self):
        if self._leaving:
            return

        self._leaving = True
        self.document_widget.leaveSession() # TODO: check for unsynced changes etc

    def end(self, delete=False):
        if self.backend is not None and self.backend.hasUnsavedChanges():
            if self.isHost:
                title, message = u"Unsaved changes", u"There are changes to the document not yet saved.\nDo you want to discard those changes?"
                if QMessageBox.question(None, title, message, QMessageBox.Ok|QMessageBox.Cancel) == QMessageBox.Cancel:
                    return
            else:
                title, message = u"Unsynchronized changes", u"There are local changes to the document not yet synchronized to the host.\nDo you want to discard those changes?"
                if QMessageBox.question(None, title, message, QMessageBox.Ok|QMessageBox.Cancel) == QMessageBox.Cancel:
                    return
        self.blink_session.end(delete=delete)

    def delete(self):
        notification_center = NotificationCenter()
        notification_center.remove_observer(self, sender=self.stream)
        self.participants_model = None
        self.stream = None
        self.backend = None
        self.widget = None

    def _notify_changes(self):
        self.widget.update_content(self)
        notification_center = NotificationCenter()
        notification_center.post_notification('DocumentSharingSessionItemDidChange', sender=self)

    def handle_notification(self, notification):
        handler = getattr(self, '_NH_%s' % notification.name, Null)
        handler(notification)


    def _NH_MediaStreamDidStart(self, notification):
        self._statusText = 'Connected'
        self._notify_changes()

    def _NH_MediaStreamDidEnd(self, notification):
        if self._blink_fail_reason:
            self._statusText = 'Disconnected: %s' % self._blink_fail_reason
        else:
            self._statusText = 'Disconnected'
        self._notify_changes()

    def _NH_MediaStreamDidFail(self, notification):
        self._blink_fail_reason = notification.data.reason

    def _NH_WebODFWidgetSessionLeft(self, notification):
        if not self._leaving:
            print ""
            return

        self.end()


class DocumentSharingDelegate(QStyledItemDelegate, ColorHelperMixin):
    def __init__(self, parent=None):
        super(DocumentSharingDelegate, self).__init__(parent)

    def editorEvent(self, event, model, option, index):
        if event.type()==QEvent.MouseButtonRelease and event.button()==Qt.LeftButton and event.modifiers()==Qt.NoModifier:
            arrow_rect = option.rect.adjusted(option.rect.width()-14, option.rect.height()/2, 0, 0)  # bottom half of the rightmost 14 pixels
            cross_rect = option.rect.adjusted(option.rect.width()-14, 0, 0, -option.rect.height()/2) # top half of the rightmost 14 pixels
            if arrow_rect.contains(event.pos()):
                documentsharing_list = self.parent()
                documentsharing_list.animation.setDirection(QPropertyAnimation.Backward)
                documentsharing_list.animation.start()
                return True
            elif cross_rect.contains(event.pos()):
                sessionItem = index.data(Qt.UserRole)
                sessionItem.end(delete=True)
                return True
        return super(DocumentSharingDelegate, self).editorEvent(event, model, option, index)

    def paint(self, painter, option, index):
        sessionItem = index.data(Qt.UserRole)
        if option.state & QStyle.State_Selected:
            sessionItem.widget.display_mode = sessionItem.widget.SelectedDisplayMode
        elif index.row() % 2 == 0:
            sessionItem.widget.display_mode = sessionItem.widget.StandardDisplayMode
        else:
            sessionItem.widget.display_mode = sessionItem.widget.AlternateDisplayMode
        sessionItem.widget.setFixedSize(option.rect.size())

        painter.save()
        painter.drawPixmap(option.rect, QPixmap.grabWidget(sessionItem.widget))
        if option.state & QStyle.State_MouseOver:
            self.drawSessionIndicators(option, painter, sessionItem.widget)
        if 0 and (option.state & QStyle.State_MouseOver):
            painter.setRenderHint(QPainter.Antialiasing, True)
            if option.state & QStyle.State_Selected:
                painter.fillRect(option.rect, QColor(240, 244, 255, 40))
            else:
                painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
                painter.fillRect(option.rect, QColor(240, 244, 255, 230))
        painter.restore()

    def drawSessionIndicators(self, option, painter, widget):
        pen_thickness = 1.6

        if widget.state_label.state is not None:
            foreground_color = option.palette.color(QPalette.Normal, QPalette.WindowText)
            background_color = widget.state_label.state_colors[widget.state_label.state]
            base_contrast_color = self.calc_light_color(background_color)
            gradient = QLinearGradient(0, 0, 1, 0)
            gradient.setCoordinateMode(QLinearGradient.ObjectBoundingMode)
            gradient.setColorAt(0.0, self.color_with_alpha(base_contrast_color, 0.3*255))
            gradient.setColorAt(1.0, self.color_with_alpha(base_contrast_color, 0.8*255))
            contrast_color = QBrush(gradient)
        else:
            #foreground_color = option.palette.color(QPalette.Normal, QPalette.WindowText)
            #background_color = option.palette.color(QPalette.Window)
            foreground_color = widget.palette().color(QPalette.Normal, widget.foregroundRole())
            background_color = widget.palette().color(widget.backgroundRole())
            contrast_color = self.calc_light_color(background_color)
        line_color = self.deco_color(background_color, foreground_color)

        pen = QPen(line_color, pen_thickness, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        contrast_pen = QPen(contrast_color, pen_thickness, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

        # draw the expansion indicator at the bottom (works best with a state_label of width 14)
        arrow_rect = QRect(0, 0, 14, 14)
        arrow_rect.moveBottomRight(widget.state_label.geometry().bottomRight())
        arrow_rect.translate(option.rect.topLeft())

        arrow = QPolygonF([QPointF(3, 1.5), QPointF(-0.5, -2.5), QPointF(-4, 1.5)])
        arrow.translate(2, 1)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.translate(arrow_rect.center())
        painter.translate(0, +1)
        painter.setPen(contrast_pen)
        painter.drawPolyline(arrow)
        painter.translate(0, -1)
        painter.setPen(pen)
        painter.drawPolyline(arrow)
        painter.restore()

        # draw the close indicator at the top (works best with a state_label of width 14)
        cross_rect = QRect(0, 0, 14, 14)
        cross_rect.moveTopRight(widget.state_label.geometry().topRight())
        cross_rect.translate(option.rect.topLeft())

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.translate(cross_rect.center())
        painter.translate(+1.5, +1)
        painter.translate(0, +1)
        painter.setPen(contrast_pen)
        painter.drawLine(-3.5, -3.5, 3.5, 3.5)
        painter.drawLine(-3.5, 3.5, 3.5, -3.5)
        painter.translate(0, -1)
        painter.setPen(pen)
        painter.drawLine(-3.5, -3.5, 3.5, 3.5)
        painter.drawLine(-3.5, 3.5, 3.5, -3.5)
        painter.restore()

    def sizeHint(self, option, index):
        return index.data(Qt.SizeHintRole)


class DocumentSharingSessionModel(QAbstractListModel):
    implements(IObserver)

    sessionAboutToBeAdded = pyqtSignal(DocumentSharingSessionItem)
    sessionAboutToBeRemoved = pyqtSignal(DocumentSharingSessionItem)
    sessionAdded = pyqtSignal(DocumentSharingSessionItem)
    sessionRemoved = pyqtSignal(DocumentSharingSessionItem)

    def __init__(self, parent=None):
        super(DocumentSharingSessionModel, self).__init__(parent)
        self.sessionItems = []

        notification_center = NotificationCenter()
        notification_center.add_observer(self, name='BlinkSessionWasDeleted')
        notification_center.add_observer(self, name='DocumentSharingSessionItemDidChange')

        notification_center.add_observer(self, name='MediaStreamDidInitialize') # to catch new documentsharing streams
        notification_center.add_observer(self, name='MediaStreamDidStart')
        notification_center.add_observer(self, name='MediaStreamDidFail')
        notification_center.add_observer(self, name='MediaStreamDidEnd')

    def rowCount(self, parent=QModelIndex()):
        return len(self.sessionItems)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self.sessionItems[index.row()]
        if role == Qt.UserRole:
            return item
        elif role == Qt.SizeHintRole:
            return item.size_hint
        elif role == Qt.DisplayRole:
            return unicode(item)
        return None

    @run_in_gui_thread
    def handle_notification(self, notification):
        handler = getattr(self, '_NH_%s' % notification.name, Null)
        handler(notification)

    def _NH_MediaStreamDidInitialize(self, notification):
        stream = notification.sender
        if stream.type != 'document-sharing':
            return

        print "DocumentSharingSessionModel MediaStreamDidInitializeHandler:", stream.session_uuid

        if not stream.continue_session:
            self.addSessionItem(DocumentSharingSessionItem(stream))
        else:
            # find session and reconnect if possible otherwise cancel the stream
            sessionItem = next((sessionItem for sessionItem in self.sessionItems if sessionItem.session_uuid == stream.session_uuid), None)
            print "found matching sessionItem:", sessionItem, stream.mode
            if sessionItem is None:
                if stream.mode != 'host': # TODO: only works if we are guest for now
                    self.addSessionItem(DocumentSharingSessionItem(stream))
                else:
                    stream.sendMessage('error', {'error': 'NoSuchHostSession'})
                    stream.blink_session.end()
                    QMessageBox.warning(None, 'Document Sharing Host Session Not Existing', 'The requested document sharing session is longer hosted.')
            else:
                sessionItem.handleReconnection(stream)

    def _NH_BlinkSessionWasDeleted(self, notification):
        self.removeSessionItem(notification.sender.items.documentsharing)

    def _NH_DocumentSharingSessionItemDidChange(self, notification):
        sessionItem = notification.sender
        if sessionItem not in self.sessionItems:
            # TODO: find out why that can happen if last is removed
            return
        index = self.index(self.sessionItems.index(notification.sender))
        self.dataChanged.emit(index, index)

    def _find_insertion_point(self, sessionItem):
        for position, item in enumerate(self.sessionItems):
            if item.name > sessionItem.name:
                break
        else:
            position = len(self.sessionItems)
        return position

    def _add_session_item(self, sessionItem):
        position = self._find_insertion_point(sessionItem)
        self.beginInsertRows(QModelIndex(), position, position)
        self.sessionItems.insert(position, sessionItem)
        self.endInsertRows()

    def _pop_session_item(self, sessionItem):
        position = self.sessionItems.index(sessionItem)
        self.beginRemoveRows(QModelIndex(), position, position)
        del self.sessionItems[position]
        self.endRemoveRows()
        return sessionItem

    def addSessionItem(self, sessionItem):
        if sessionItem in self.sessionItems:
            return
        self.sessionAboutToBeAdded.emit(sessionItem)
        self._add_session_item(sessionItem)
        self.sessionAdded.emit(sessionItem)

    def removeSessionItem(self, sessionItem):
        if sessionItem not in self.sessionItems:
            return
        self.sessionAboutToBeRemoved.emit(sessionItem)
        self._pop_session_item(sessionItem).delete()
        self.sessionRemoved.emit(sessionItem)


class DocumentSharingListView(QListView):
    implements(IObserver)

    def __init__(self, documents_window):
        super(DocumentSharingListView, self).__init__(documents_window.session_panel)
        self.documents_window = documents_window
        self.setItemDelegate(DocumentSharingDelegate(self))

        self.setMouseTracking(True)
        self.setAlternatingRowColors(True)
        self.setAutoFillBackground(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        #self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded) # default
        self.setSelectionMode(QListView.SingleSelection) # default

        self.setStyleSheet("""QListView { border: 1px inset palette(dark); border-radius: 3px; }""")
        self.animation = QPropertyAnimation(self, 'geometry')
        self.animation.setDuration(250)
        self.animation.setEasingCurve(QEasingCurve.Linear)
        self.animation.finished.connect(self._SH_AnimationFinished)
        self.context_menu = QMenu(self)
        self.actions = ContextMenuActions()
        self.ignore_selection_changes = False
        self.doubleClicked.connect(self._SH_DoubleClicked) # activated is emitted on single click
        documents_window.session_panel.installEventFilter(self)

        notification_center = NotificationCenter()
        notification_center.add_observer(self, name='BlinkActiveSessionDidChange')

    def selectionChanged(self, selected, deselected):
        super(DocumentSharingListView, self).selectionChanged(selected, deselected)
        selection_model = self.selectionModel()
        selection = selection_model.selection()
        if selection_model.currentIndex() not in selection:
            index = selection.indexes()[0] if not selection.isEmpty() else self.model().index(-1)
            selection_model.setCurrentIndex(index, selection_model.Select)
        self.context_menu.hide()
        if self.ignore_selection_changes:
            return

    def selectionCommand(self, index, event=None):
        # in case we implement DnD later, we might consider selecting the item on mouse press if there is nothing else selected (except maybe if mouse press was on the buttons area?)
        # this would allow the dragged item to be selected before DnD starts, in case it is needed for the session to be active when dragged -Dan
        selection_model = self.selectionModel()
        if self.selectionMode() == self.NoSelection:
            return selection_model.NoUpdate
        elif not index.isValid() or event is None:
            return selection_model.NoUpdate
        elif event.type() in (QEvent.MouseButtonPress, QEvent.MouseMove):
            return selection_model.NoUpdate
        elif event.type() == QEvent.MouseButtonRelease:
            index_rect = self.visualRect(index)
            cross_rect = index_rect.adjusted(index_rect.width()-14, 0, 0, -index_rect.height()/2) # the top half of the rightmost 14 pixels
            if cross_rect.contains(event.pos()):
                return selection_model.NoUpdate
            else:
                return selection_model.ClearAndSelect
        else:
            return super(DocumentSharingListView, self).selectionCommand(index, event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            new_size = event.size()
            geometry = self.animation.endValue()
            if geometry is not None:
                old_size = geometry.size()
                geometry.setSize(new_size)
                self.animation.setEndValue(geometry)
                geometry = self.animation.startValue()
                geometry.setWidth(geometry.width() + new_size.width() - old_size.width())
                self.animation.setStartValue(geometry)
            self.resize(new_size)
        return False

    def contextMenuEvent(self, event):
        pass

    def hideEvent(self, event):
        self.context_menu.hide()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.selectionModel().selection():
            self.animation.setDirection(QPropertyAnimation.Backward)
            self.animation.start()
        else:
            super(DocumentSharingListView, self).keyPressEvent(event)

    def _SH_AnimationFinished(self):
        if self.animation.direction() == QPropertyAnimation.Forward:
            try:
                self.scrollTo(self.selectedIndexes()[0], self.EnsureVisible)
            except IndexError:
                pass
            self.setFocus(Qt.OtherFocusReason)
        else:
            self.hide()
            current_tab = self.documents_window.tab_widget.currentWidget()
            current_tab.setFocus(Qt.OtherFocusReason)

    def _SH_DoubleClicked(self, index):
        self.animation.setDirection(QPropertyAnimation.Backward)
        self.animation.start()

    def handle_notification(self, notification):
        handler = getattr(self, '_NH_%s' % notification.name, Null)
        handler(notification)

    def _NH_BlinkActiveSessionDidChange(self, notification):
        self.ignore_selection_changes = True
        selection_model = self.selectionModel()
        if notification.data.active_session is None:
            selection = selection_model.selection()
            # check the code in this if branch if it's needed -Dan (if not also remove previous_active_session maybe)
            #selected_blink_session = selection[0].topLeft().data(Qt.UserRole).blink_session if selection else None
            #if notification.data.previous_active_session is selected_blink_session:
            #    print "-- chat session list updating selection to None None"
            #    selection_model.clearSelection()
        else:
            if notification.data.active_session.items.documentsharing is not None:
                model = self.model()
                position = model.sessionItems.index(notification.data.active_session.items.documentsharing)
                selection_model.select(model.index(position), selection_model.ClearAndSelect)
        self.ignore_selection_changes = False


class DocumentWidget(QWidget):
    implements(IObserver)
    def __init__(self, documentsharing_item, parent = None):
        super(DocumentWidget, self).__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setMargin(0)
        self.webODFWidget = WebODFWidget(documentsharing_item.backend, self)
        self.layout.addWidget(self.webODFWidget)
        self.documentsharing_item = documentsharing_item

        self.notification_center = NotificationCenter()
        self.notification_center.add_observer(self, sender=documentsharing_item)
        self.notification_center.add_observer(self, sender=documentsharing_item.stream)
        if documentsharing_item.stream.mode == 'host':
            self.webODFWidget.joinSession()

        print "+++++++ instance DocumentWidget created"

    def leaveSession(self):
        self.webODFWidget.leaveSession() # TODO: check for unsynced changes etc

    @run_in_gui_thread
    def handle_notification(self, notification):
        handler = getattr(self, '_NH_%s' % notification.name, None)
        if handler is not None:
            handler(notification)

    def _NH_DocumentSharingStreamGenesisDocument(self, notification):
        self.webODFWidget.joinSession()

    def _NH_DocumentSharingStreamError(self, notification):
        if(notification.sender not in self.documentsharing_item.blink_session.streams):
            return

        QMessageBox.warning(self, 'An Error Occurred', 'There was an error during your collaboration session: %s' % notification.data.content)


class NoSessionsLabel(QLabel):
    def __init__(self, documents_window):
        super(NoSessionsLabel, self).__init__(documents_window.session_panel)
        self.documents_window = documents_window
        font = self.font()
        font.setFamily("Sans Serif")
        font.setPointSize(20)
        self.setFont(font)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("""QLabel { border: 1px inset palette(dark); border-radius: 3px; background-color: white; color: #545454; }""")
        self.setText("No Sessions")
        documents_window.session_panel.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            self.resize(event.size())
        return False



ui_class, base_class = uic.loadUiType(Resources.get('documents_window.ui'))

class DocumentsWindow(base_class, ui_class, ColorHelperMixin):
    implements(IObserver)

    sliding_panels = True

    def __init__(self, parent=None):
        super(DocumentsWindow, self).__init__(parent)
        with Resources.directory:
            self.setupUi()

        self.selected_item = None
        self.documentsharingsession_model = DocumentSharingSessionModel(self)
        self.documentsharing_list.setModel(self.documentsharingsession_model)
        self.session_widget.installEventFilter(self)
        self.state_label.installEventFilter(self)

        self.control_button.clicked.connect(self._SH_ControlButtonClicked)
        self.documentsharingsession_model.sessionAdded.connect(self._SH_SessionModelSessionAdded)
        self.documentsharingsession_model.sessionRemoved.connect(self._SH_SessionModelSessionRemoved)
        self.documentsharingsession_model.sessionAboutToBeRemoved.connect(self._SH_SessionModelSessionAboutToBeRemoved)
        self.documentsharing_list.selectionModel().selectionChanged.connect(self._SH_SessionListSelectionChanged)

        geometry = QSettings().value("documents_window/geometry")
        if geometry:
            self.restoreGeometry(geometry)

        notification_center = NotificationCenter()
        notification_center.add_observer(self, name='SIPApplicationDidStart')
        notification_center.add_observer(self, name='BlinkSessionNewIncoming')
        notification_center.add_observer(self, name='BlinkSessionNewOutgoing')
        notification_center.add_observer(self, name='BlinkSessionDidReinitializeForIncoming')
        notification_center.add_observer(self, name='BlinkSessionDidReinitializeForOutgoing')
        
        #notification_center.add_observer(self, name='DocumentSharingStreamGotChanges') emit conditionally some typing sounds or update

    def setupUi(self):
        super(DocumentsWindow, self).setupUi(self)

        self.control_icon = QIcon(Resources.get('icons/cog.svg'))
        self.cancel_icon = QIcon(Resources.get('icons/cancel.png'))

        self.control_button.setIcon(self.control_icon)

        self.control_menu = QMenu(self.control_button)
        self.control_button.setMenu(self.control_menu)
        self.control_button.actions = ContextMenuActions()
        self.control_button.actions.reconnect = QAction("Reconnect", self, triggered=self._AH_Reconnect)
        self.control_button.actions.disconnect = QAction("Disconnect", self, triggered=self._AH_Disconnect)
        self.control_button.actions.main_window = QAction("Main Window", self, triggered=self._AH_MainWindow, shortcut='Ctrl+B', shortcutContext=Qt.ApplicationShortcut)

        self.addAction(self.control_button.actions.main_window) # make this active even when it's not in the contol_button's menu

        self.documentsharing_list = DocumentSharingListView(self)
        self.documentsharing_list.setObjectName('documentsharing_list')

        self.no_sessions_label = NoSessionsLabel(self)
        self.no_sessions_label.setObjectName('no_sessions_label')

        self.slide_direction = self.session_details.RightToLeft # decide if we slide from one direction only -Dan
        self.slide_direction = self.session_details.Automatic
        self.session_details.animationDuration = 300
        self.session_details.animationEasingCurve = QEasingCurve.OutCirc

        self.tab_widget.clear() # remove the tab(s) added in designer
        self.tab_widget.tabBar().hide()
        self.dummy_tab = QWidget(self.tab_widget)
        #self.dummy_tab = WebODFWidget(None, self.tab_widget)
        self.tab_widget.addTab(self.dummy_tab, "Dummy")
        self.tab_widget.setCurrentWidget(self.dummy_tab)

        self.documentsharing_list.hide()

        #self.participants_panel_info_button.hide()
        #self.participants_panel_files_button.hide()

        self.control_button.setEnabled(False)

        self.info_label.setForegroundRole(QPalette.Dark)

        # prepare self.session_widget so we can take over some of its painting and behaviour
        self.session_widget.setAttribute(Qt.WA_Hover, True)
        self.session_widget.hovered = False

    def _get_selected_session_item(self):
        return self.__dict__['selected_session_item']

    def _set_selected_session_item(self, sessionItem):
        old_session_item = self.__dict__.get('selected_session_item', None)
        new_session_item = self.__dict__['selected_session_item'] = sessionItem
        if new_session_item != old_session_item:
            notification_center = NotificationCenter()
            if old_session_item is not None:
                notification_center.remove_observer(self, sender=old_session_item)
                notification_center.remove_observer(self, sender=old_session_item.blink_session)
            if new_session_item is not None:
                notification_center.add_observer(self, sender=new_session_item)
                notification_center.add_observer(self, sender=new_session_item.blink_session)
                self._update_widgets_for_session_item() # clean this up -Dan (too many functions called in 3 different places: on selection changed, here and on notifications handlers)
                self._update_control_menu()

    selected_session_item = property(_get_selected_session_item, _set_selected_session_item)
    del _get_selected_session_item, _set_selected_session_item

    def _update_widgets_for_session_item(self):
        sessionItem = self.selected_session_item
        if sessionItem is None:
            return

        widget = sessionItem.widget
        # session widget
        self.name_label.setText(widget.name_label.text())
        self.info_label.setText(widget.info_label.text())
        self.icon_label.setPixmap(widget.icon_label.pixmap())
        self.state_label.state = widget.state_label.state or 'offline'
        self.locally_modified_icon.setVisible(widget.locally_modified_icon.isVisibleTo(widget))

    def _update_control_menu(self):
        menu = self.control_menu
        menu.hide()
        blink_session = self.selected_session_item.blink_session
        state = blink_session.state
        if state=='connecting/*' and blink_session.direction=='outgoing' or state=='connected/sent_proposal':
            self.control_button.setMenu(None)
            self.control_button.setIcon(self.cancel_icon)
        elif state == 'connected/received_proposal':
            self.control_button.setEnabled(False)
        else:
            self.control_button.setEnabled(True)
            self.control_button.setIcon(self.control_icon)
            menu.clear()
            if state not in ('connecting/*', 'connected/*'):
                menu.addAction(self.control_button.actions.reconnect)
            else:
                menu.addAction(self.control_button.actions.disconnect)
                #if state == 'connected':
                    #menu.addAction(self.control_button.actions.add_audio if 'audio' not in blink_session.streams else self.control_button.actions.remove_audio)
            ##menu.addAction(self.control_button.actions.dump_session) # remove this later -Dan
            self.control_button.setMenu(menu)

    def show(self):
        super(DocumentsWindow, self).show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        QSettings().setValue("documents_window/geometry", self.saveGeometry())
        super(DocumentsWindow, self).closeEvent(event)

    def eventFilter(self, watched, event):
        event_type = event.type()
        if watched is self.session_widget:
            if event_type == QEvent.HoverEnter:
                watched.hovered = True
            elif event_type == QEvent.HoverLeave:
                watched.hovered = False
            elif event_type == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                self._EH_ShowSessions()
        elif watched is self.state_label:
            if event_type == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and event.modifiers() == Qt.NoModifier:
                upper_half = QRect(0, 0, self.state_label.width(), self.state_label.height()/2)
                if upper_half.contains(event.pos()):
                    self._EH_CloseSession()
                else:
                    self._EH_ShowSessions()
            elif event_type == QEvent.Paint: # and self.session_widget.hovered:
                watched.event(event)
                self.drawSessionWidgetIndicators()
                return True
        return False

    def drawSessionWidgetIndicators(self):
        painter = QPainter(self.state_label)
        palette = self.state_label.palette()
        rect = self.state_label.rect()

        pen_thickness = 1.6

        if self.state_label.state is not None:
            background_color = self.state_label.state_colors[self.state_label.state]
            base_contrast_color = self.calc_light_color(background_color)
            gradient = QLinearGradient(0, 0, 1, 0)
            gradient.setCoordinateMode(QLinearGradient.ObjectBoundingMode)
            gradient.setColorAt(0.0, self.color_with_alpha(base_contrast_color, 0.3*255))
            gradient.setColorAt(1.0, self.color_with_alpha(base_contrast_color, 0.8*255))
            contrast_color = QBrush(gradient)
        else:
            background_color = palette.color(QPalette.Window)
            contrast_color = self.calc_light_color(background_color)
        foreground_color = palette.color(QPalette.Normal, QPalette.WindowText)
        line_color = self.deco_color(background_color, foreground_color)

        pen = QPen(line_color, pen_thickness, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        contrast_pen = QPen(contrast_color, pen_thickness, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

        # draw the expansion indicator at the bottom (works best with a state_label of width 14)
        arrow_rect = QRect(0, 0, 14, 14)
        arrow_rect.moveBottomRight(rect.bottomRight())

        arrow = QPolygonF([QPointF(-3, -1.5), QPointF(0.5, 2.5), QPointF(4, -1.5)])
        arrow.translate(1, 1)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.translate(arrow_rect.center())
        painter.translate(0, +1)
        painter.setPen(contrast_pen)
        painter.drawPolyline(arrow)
        painter.translate(0, -1)
        painter.setPen(pen)
        painter.drawPolyline(arrow)
        painter.restore()

        # draw the close indicator at the top (works best with a state_label of width 14)
        cross_rect = QRect(0, 0, 14, 14)
        cross_rect.moveTopRight(rect.topRight())

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.translate(cross_rect.center())
        painter.translate(+1.5, +1)
        painter.translate(0, +1)
        painter.setPen(contrast_pen)
        painter.drawLine(-3.5, -3.5, 3.5, 3.5)
        painter.drawLine(-3.5, 3.5, 3.5, -3.5)
        painter.translate(0, -1)
        painter.setPen(pen)
        painter.drawLine(-3.5, -3.5, 3.5, 3.5)
        painter.drawLine(-3.5, 3.5, 3.5, -3.5)
        painter.restore()

    @run_in_gui_thread
    def handle_notification(self, notification):
        handler = getattr(self, '_NH_%s' % notification.name, Null)
        handler(notification)

    def _NH_SIPApplicationDidStart(self, notification):
        notification.center.add_observer(self, name='CFGSettingsObjectDidChange')

        blink_settings = BlinkSettings()
        if blink_settings.documents_window.session_info.alternate_style:
            title_role = 'alt-title'
            value_role = 'alt-value'
        else:
            title_role = 'title'
            value_role = 'value'
        for label in (attr for name, attr in vars(self).iteritems() if name.endswith('_title_label') and attr.property('role') is not None):
            label.setProperty('role', title_role)
        for label in (attr for name, attr in vars(self).iteritems() if name.endswith('_value_label') or name.endswith('_value_widget') and attr.property('role') is not None):
            label.setProperty('role', value_role)

    def _NH_CFGSettingsObjectDidChange(self, notification):
        settings = SIPSimpleSettings()
        blink_settings = BlinkSettings()
        if notification.sender is blink_settings:
            if 'presence.icon' in notification.data.modified:
                QWebSettings.clearMemoryCaches()
            if 'documents_window.session_info.alternate_style' in notification.data.modified:
                if blink_settings.documents_window.session_info.alternate_style:
                    title_role = 'alt-title'
                    value_role = 'alt-value'
                else:
                    title_role = 'title'
                    value_role = 'value'
                for label in (attr for name, attr in vars(self).iteritems() if name.endswith('_title_label') and attr.property('role') is not None):
                    label.setProperty('role', title_role)
                for label in (attr for name, attr in vars(self).iteritems() if name.endswith('_value_label') or name.endswith('_value_widget') and attr.property('role') is not None):
                    label.setProperty('role', value_role)

    def _NH_BlinkSessionNewIncoming(self, notification):
        if 'document-sharing' in notification.sender.streams.types:
            self.show()

    def _NH_BlinkSessionNewOutgoing(self, notification):
        if notification.sender.stream_descriptions != None:
            if 'document-sharing' in notification.sender.stream_descriptions:
                self.show()
        else:
            if 'document-sharing' in notification.sender.streams:
                self.show()

    def _NH_BlinkSessionDidReinitializeForIncoming(self, notification):
        if 'document-sharing' in notification.sender.streams.types:
            self.show()

    def _NH_BlinkSessionDidReinitializeForOutgoing(self, notification):
        if 'document-sharing' in notification.sender.stream_descriptions.types:
            self.show()

    # use BlinkSessionNewIncoming/Outgoing to show the chat window if there is a chat stream available (like with reinitialize) instead of using the sessionAdded signal from the model -Dan
    # or maybe not. sessionAdded means it was added to the model, while during NewIncoming/Outgoing we do not know that yet. but then we have a problem with the DidReinitialize since
    # they do not check if the session is in the model. maybe the right approach is to always have BlinkSessions in the model and if we need any other kind of sessions we create a
    # different class for them that posts different notifications. in that case we can do in in NewIncoming/Outgoing -Dan

    def _NH_BlinkSessionWillAddStream(self, notification):
        if notification.data.stream.type == 'document-sharing':
            self.show()

    def _NH_BlinkSessionDidRemoveStream(self, notification):
        self._update_control_menu()

    def _NH_BlinkSessionDidChangeState(self, notification):
        # even if we use this, we also need to listen for BlinkSessionDidRemoveStream as that transition doesn't change the state at all -Dan
        self._update_control_menu()

    def _NH_BlinkSessionDidEnd(self, notification):
        return # TODO: hide WebODF participant list and instead enable and fill native participants_list
        if self.selected_session_item.active_panel is not self.participants_list:
            if self.sliding_panels:
                self.session_details.slideInWidget(self.participants_list, direction=self.slide_direction)
            else:
                self.session_details.setCurrentWidget(self.participants_list)
            self.selected_session_item.active_panel = self.participants_list

    def _NH_BlinkSessionWillAddParticipant(self, notification):
        if len(notification.sender.server_conference.participants) == 1 and self.selected_session_item.active_panel is not self.participants_panel:
            if self.sliding_panels:
                self.session_details.slideInWidget(self.participants_panel, direction=self.slide_direction)
            else:
                self.session_details.setCurrentWidget(self.participants_panel)
            self.selected_session_item.active_panel = self.participants_panel

    def _NH_DocumentSharingSessionItemDidChange(self, notification):
        self._update_widgets_for_session_item()

    def _NH_DocumentSharingStreamGotChanges(self, notification):
        blink_session = notification.sender.blink_session
        sessionItem = blink_session.items.documentsharing
        if sessionItem is None:
            return

        settings = SIPSimpleSettings()
        #if settings.sounds.play_document_alerts and self.selected_session_item is session:
            #player = WavePlayer(SIPApplication.alert_audio_bridge.mixer, Resources.get('sounds/document_changed.wav'), volume=20)
            #SIPApplication.alert_audio_bridge.add(player)
            #player.start()

    # signal handlers
    #
    def _SH_ParticipantsButtonClicked(self, checked):
        if self.sliding_panels:
            self.session_details.slideInWidget(self.participants_panel, direction=self.slide_direction)
        else:
            self.session_details.setCurrentWidget(self.participants_panel)
        self.selected_session_item.active_panel = self.participants_panel

    def _SH_ControlButtonClicked(self, checked):
        # this is only called if the control button doesn't have a menu attached
        if self.selected_session_item.blink_session.state == 'connected/sent_proposal':
            self.selected_session_item.blink_session.sip_session.cancel_proposal()
        else:
            self.selected_session_item.end()

    def _SH_SessionModelSessionAdded(self, sessionItem):
        model = self.documentsharingsession_model
        position = model.sessionItems.index(sessionItem)
        sessionItem.document_widget = DocumentWidget(sessionItem, self.tab_widget)
        sessionItem.active_panel = self.participants_panel
        self.tab_widget.insertTab(position, sessionItem.document_widget, sessionItem.name)
        self.no_sessions_label.hide()
        selection_model = self.documentsharing_list.selectionModel()
        selection_model.select(model.index(position), selection_model.ClearAndSelect)
        self.documentsharing_list.scrollTo(model.index(position), QListView.EnsureVisible) # or PositionAtCenter
        sessionItem.document_widget.setFocus(Qt.OtherFocusReason)

    def _SH_SessionModelSessionRemoved(self, sessionItem):
        self.tab_widget.removeTab(self.tab_widget.indexOf(sessionItem.document_widget))
        sessionItem.document_widget = None
        sessionItem.active_panel = None
        if not self.documentsharingsession_model.sessionItems:
            self.close()
            self.no_sessions_label.show()
        elif not self.documentsharing_list.isVisibleTo(self):
            self.documentsharing_list.animation.setDirection(QPropertyAnimation.Forward)
            self.documentsharing_list.animation.setStartValue(self.session_widget.geometry())
            self.documentsharing_list.animation.setEndValue(self.session_panel.rect())
            self.documentsharing_list.show()
            self.documentsharing_list.animation.start()

    def _SH_SessionModelSessionAboutToBeRemoved(self, sessionItem):
        # choose another one to select (a chat only or ended session if available, else one with audio but keep audio on hold? or select nothing and display the dummy tab?)
        #selection_model = self.documentsharing_list.selectionModel()
        #selection_model.clearSelection()
        pass

    def _SH_SessionListSelectionChanged(self, selected, deselected):
        #print "-- chat selection changed %s -> %s" % ([x.row() for x in deselected.indexes()], [x.row() for x in selected.indexes()])
        self.selected_session_item = selected[0].topLeft().data(Qt.UserRole) if selected else None
        if self.selected_session_item is not None:
            self.tab_widget.setCurrentWidget(self.selected_session_item.document_widget)  # why do we switch the tab here, but do everything else in the selected_session_item property setter? -Dan
            self.session_details.setCurrentWidget(self.selected_session_item.active_panel)
            #self.participants_list.setModel(self.selected_session_item.participants_model)
            self.control_button.setEnabled(True)
        else:
            self.tab_widget.setCurrentWidget(self.dummy_tab)
            self.session_details.setCurrentWidget(self.participants_panel)
            #self.participants_list.setModel(None)
            self.control_button.setEnabled(False)

    def _AH_Reconnect(self):
        blink_session = self.selected_session_item.blink_session
        if blink_session.state == 'ended':
            session_uuid = self.selected_session_item.session_uuid
            document_title = self.selected_session_item.document_title
            if self.selected_session_item.isHost:
                session_mode =  "host"
            else:
                session_mode = "guest"
            blink_session.init_outgoing(blink_session.account, blink_session.contact, blink_session.contact_uri,
                                        [StreamDescription('document-sharing', document_title=document_title, session_uuid=session_uuid, continue_session=session_mode)], reinitialize=True)
        blink_session.connect()

    def _AH_Disconnect(self):
        self.selected_session_item.end()

    def _AH_MainWindow(self):
        blink = QApplication.instance()
        blink.main_window.show()

    def _EH_CloseSession(self):
        if self.selected_session_item is not None:
            self.selected_session_item.end(delete=True)

    def _EH_ShowSessions(self):
        self.documentsharing_list.animation.setDirection(QPropertyAnimation.Forward)
        self.documentsharing_list.animation.setStartValue(self.session_widget.geometry())
        self.documentsharing_list.animation.setEndValue(self.session_panel.rect())
        self.documentsharing_list.scrollToTop()
        self.documentsharing_list.show()
        self.documentsharing_list.animation.start()

del ui_class, base_class
