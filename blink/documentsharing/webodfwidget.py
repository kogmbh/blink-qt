__all__ = ['WebODFWidget']

import os

from application.notification import IObserver, NotificationCenter, NotificationData

from PyQt4.QtCore import Qt, QByteArray, QTimer, QIODevice, QFile, QUrl, QObject, QBuffer, pyqtSignal, pyqtSlot
from PyQt4.QtGui import QWidget, QHBoxLayout
from PyQt4.QtGui import QFileDialog, QMessageBox
from PyQt4.QtNetwork import QNetworkRequest, QNetworkReply, QNetworkAccessManager
from PyQt4.QtWebKit import QWebPage, QWebSettings, QWebView

from blink.resources import Resources


class ResourceReply(QNetworkReply):

    def __init__(self, filePath, parent):
        super(ResourceReply, self).__init__(parent)

        self.source = QFile(filePath, self)

        QTimer.singleShot(0, self._SH_finish);

    def __del__(self):
        print "ResourceReply deleted", self.source.fileName()

    def isSequential(self):
        return True

    def open(self, mode):
        self.source.open(mode)
        return super(ResourceReply, self).open(mode)

    def close(self):
        self.source.close();
        super(ResourceReply, self).close()

    def bytesAvailable(self):
        return self.source.bytesAvailable() + super(ResourceReply, self).bytesAvailable()

    def readData(self, maxSize):
        return self.source.read(maxSize)

    def abort(self):
        self.source.close()

    def _SH_finish(self):
        self.setHeader(QNetworkRequest.ContentLengthHeader, self.source.bytesAvailable())

        self.open(QNetworkReply.ReadOnly)

        self.finished.emit()


class WebODFNetworkManager(QNetworkAccessManager):

    def __init__(self, sessionBackend, parent):
        super(WebODFNetworkManager, self).__init__(parent)
        self.sessionBackend = sessionBackend
        self.genesisDocumentPath = None

    def setGenesisDocumentPath(self, genesisDocumentPath):
        self.genesisDocumentPath = genesisDocumentPath


    def createRequest(self, op, req, data):
        #print "createRequest: req.url()", req.url()

        if req.url().host() != "webodf":
            return super(WebODFNetworkManager, self).createRequest(op, req, data);

        path = QUrl("http://webodf/").resolved(req.url()).path()

        print "Request for", path

        if op == QNetworkAccessManager.GetOperation:
            reply = None;
            if path.startswith("/dijit"):
                path = "/wodo" + path # TODO: why gets wodo/ lost?
            resourcePath = Resources.get("documentsharing"+path)
            if os.path.exists(resourcePath):
                print "reply for resource:", resourcePath
                reply = ResourceReply(resourcePath, self)
            elif path == self.genesisDocumentPath:
                print "reply for genesis document:", self.genesisDocumentPath
                reply = ResourceReply(self.genesisDocumentPath, self)
            else:
                avatarUrl = self.sessionBackend.resolveAvatarUrl(path)
                if avatarUrl != "":
                    print "reply for avatar url:", avatarUrl
                    reply = ResourceReply(avatarUrl, self)

            if reply is not None:
                return  reply

            print "Request for unknown resource:", path

        return super(WebODFNetworkManager, self).createRequest(op, req, data)


class JSBridge(QObject):

    editorCreationRequested = pyqtSignal()
    documentDataChanged = pyqtSignal()

    joinSessionRequested = pyqtSignal()
    leaveSessionRequested = pyqtSignal()

    editorCreated = pyqtSignal()
    sessionJoined = pyqtSignal(bool)
    sessionLeft = pyqtSignal(bool)

    def __init__(self):
        super(JSBridge, self).__init__()
        self.documentData = QByteArray()

    def documentData(self):
        return self.documentData

    def requestEditorCreation(self):
        self.editorCreationRequested.emit()

    def requestJoinSession(self):
        self.joinSessionRequested.emit()

    def requestLeaveSession(self):
        self.leaveSessionRequested.emit()

    # no QByteArray supported in Qt4, need to pass data as base64 string... :(
    @pyqtSlot('QString')
    def endSaveDocument(self, dataAsBaseSixFourString):
        self.documentData = QByteArray.fromBase64(dataAsBaseSixFourString)
        self.documentDataChanged.emit()

    @pyqtSlot()
    def endEditorCreation(self):
        self.editorCreated.emit()

    @pyqtSlot('QVariantMap')
    def endJoinSession(self, data):
        self.sessionJoined.emit(data.isEmpty())

    @pyqtSlot('QVariantMap')
    def endLeaveSession(self, data):
        self.sessionLeft.emit(data.isEmpty())


class WebODFWidget(QWidget):

    def __init__(self, sessionBackend, parent):
        super(WebODFWidget, self).__init__(parent)
        self.bridge = None

        self.webODFEditorCreated = False
        self.sessionJoinRequested = False
        self.sessionBackend = sessionBackend
        self.genesisDocumentPath = None

        layout = QHBoxLayout(self)
        layout.setMargin(0)
        self.webView = QWebView(self)
        # prevent drops of urls resulting in loading that url
        # needs to be revisited once WebODF editor also supports text (or image) insertion by D'n'D
        self.webView.setAcceptDrops(False)
        # TODO: reenable for RELEASE
        #self.webView.setContextMenuPolicy(Qt.NoContextMenu)
        layout.addWidget(self.webView)

        self.bridge = JSBridge()
        self.bridge.documentDataChanged.connect(self._SH_endSaveDocument)
        # TODO: find if signal forwarding is supported by PyQt
        self.bridge.sessionJoined.connect(self._SH_sessionJoined)
        self.bridge.sessionLeft.connect(self._SH_sessionLeft)

        self.page = self.webView.page()
        self.networkmanager = WebODFNetworkManager(self.sessionBackend, self)
        self.page.setNetworkAccessManager(self.networkmanager)
        self.page.settings().setAttribute(QWebSettings.DeveloperExtrasEnabled, True)

        self.page.mainFrame().javaScriptWindowObjectCleared.connect(self._SH_setJSBridge)
        self.page.loadFinished.connect(self._SH_onLoadFinished)

        self.webView.load(QUrl("http://webodf/editor.html"))


    def joinSession(self):
        print "joinSession - genesisUrl:", self.sessionBackend.genesisUrl()
        if self.genesisDocumentPath != "":
            self.networkmanager.setGenesisDocumentPath(None)

        self.genesisDocumentPath = self.sessionBackend.genesisUrl()
        self.networkmanager.setGenesisDocumentPath(self.sessionBackend.genesisUrl())

        if not self.webODFEditorCreated:
            self.sessionJoinRequested = True
            return

        self.bridge.requestJoinSession()

    def leaveSession(self):
        self.bridge.requestLeaveSession()

    def _SH_setJSBridge(self):
        #Q_ASSERT(self.bridge);
        self.page.mainFrame().addToJavaScriptWindowObject("QtBridgeObject", self.bridge)

        #Q_ASSERT(self.sessionBackend.data());
        self.page.mainFrame().addToJavaScriptWindowObject("NativeSessionBackend", self.sessionBackend)

    def _SH_endSaveDocument(self):
        # TODO: the destination could be predefined by the genesis url, at least if that was not used as template
        destination = QFileDialog.getSaveFileName(self, "Save Document", "", "ODT Files (*.odt)")
        if destination == "":
            return

        file = QFile(destination);
        if not file.open(QIODevice.WriteOnly):
            title, message = u"Error On Saving", u"Failed to open %s for writing." % destination
            QMessageBox.warning(self, title, message)
            return

        file.write(self.bridge.documentData)
        file.close()
        self.sessionBackend.tagCurrentStateAsSavedToDisc()

    def _SH_sessionJoined(self, success):
        notification_center = NotificationCenter()
        notification_center.post_notification('WebODFWidgetSessionJoined', sender=self, data=NotificationData(success=success))

    def _SH_sessionLeft(self, success):
        notification_center = NotificationCenter()
        notification_center.post_notification('WebODFWidgetSessionLeft', sender=self, data=NotificationData(success=success))

    def _SH_onLoadFinished(self):
        self.bridge.editorCreated.connect(self._SH_onEditorCreated)
        self.bridge.requestEditorCreation()


    def _SH_onEditorCreated(self):
        self.bridge.editorCreated.disconnect(self._SH_onEditorCreated)
        self.webODFEditorCreated = True

        if self.sessionJoinRequested:
            self.sessionJoinRequested = False
            self.bridge.requestJoinSession()
