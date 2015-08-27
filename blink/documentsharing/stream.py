from zope.interface import implements
from application.notification import NotificationCenter, NotificationData, IObserver
from sipsimple.core import SDPAttribute
from sipsimple.streams import StreamError, InvalidStreamError, UnknownStreamError
from sipsimple.streams.msrp import MSRPStreamBase
from sipsimple.threading import run_in_twisted_thread
from sipsimple.threading.green import run_in_green_thread
from msrplib.session import MSRPSession
from msrplib.protocol import FailureReportHeader, SuccessReportHeader, ContentTypeHeader

from PyQt4.QtCore import QTemporaryFile

from threading import Event, Lock

import os
import sys
import cjson
import uuid


class GenesisFileSender(object):

    file_part_size = 64*1024

    def __init__(self, filename, msrp_session, msrp):
        self.notification_center = NotificationCenter()
        self.msrp = msrp
        self.msrp_session = msrp_session
        self.offset = 0
        self.fileSize = 0
        self.filename = filename
        self.headers = {}
        self.headers[ContentTypeHeader.name] = ContentTypeHeader('application/x-webodf-genesisdocument')
        self.headers[SuccessReportHeader.name] = SuccessReportHeader('yes')
        self.headers[FailureReportHeader.name] = FailureReportHeader('yes')
        self.finished_event = Event()
        self.stop_event = Event()

    def startFileSending(self):
        finished = False
        failure_reason = None
        fd = open(self.filename.encode(sys.getfilesystemencoding()), 'rb')
        self.fileSize = os.fstat(fd.fileno()).st_size
        print "------- startFileSending, filesize:", self.fileSize

        try:
            while not self.stop_event.is_set():
                try:
                    data = fd.read(self.file_part_size)
                except EnvironmentError, e:
                    failure_reason = str(e)
                    break
                if not data:
                    finished = True
                    break
                self._sendGenesisFileChunk(data)
        finally:
            fd.close()

        print "------- startFileSending finished:", finished
        if not finished:
            self.notification_center.post_notification('FileTransferHandlerDidEnd', sender=self, data=NotificationData(error=True, reason=failure_reason or 'Interrupted transfer'))
            return

        # Wait until the stream ends or we get all reports
        #self.stop_event.wait()
        if self.finished_event.is_set():
            self.notification_center.post_notification('FileTransferHandlerDidEnd', sender=self, data=NotificationData(error=False, reason=None))
        else:
            self.notification_center.post_notification('FileTransferHandlerDidEnd', sender=self, data=NotificationData(error=True, reason='Incomplete transfer'))

    def _on_transaction_response(self, response):
        if self.stop_event.is_set():
            return
        print "------- _on_transaction_response response.code:", response.code
        if response.code != 200:
            self.notification_center.post_notification('FileTransferHandlerError', sender=self, data=NotificationData(error=response.comment))
            self.end()

    def _sendGenesisFileChunk(self, data):
        if self.stop_event.is_set():
            return

        data_len = len(data)
        print "------- _sendGenesisFileChunk, self.offset:", self.offset, " data_len:", data_len
        chunk = self.msrp.make_send_request(data=data,
                                            start=self.offset+1,
                                            end=self.offset+data_len,
                                            length=self.fileSize)
        chunk.headers.update(self.headers)
        try:
            self.msrp_session.send_chunk(chunk, response_cb=self._on_transaction_response)
        except Exception, e:
            print "------- _sendGenesisFileChunk fail:", str(e)
            self.notification_center.post_notification('FileTransferHandlerError', sender=self, data=NotificationData(error=str(e)))
        else:
            self.offset += data_len

    def end(self):
        self.stop_event.set()

    def handleReportChunk(self, chunk):
        print "------- handleReportChunk status:", str(chunk.status)
        if chunk.status.code == 200:
            transferred_bytes = chunk.byte_range[1]
            total_bytes = chunk.byte_range[2]
            self.notification_center.post_notification('FileTransferHandlerProgress', sender=self, data=NotificationData(transferred_bytes=transferred_bytes, total_bytes=total_bytes))
            if transferred_bytes == total_bytes:
                self.finished_event.set()
                self.end()
        else:
            self.notification_center.post_notification('FileTransferHandlerError', sender=self, data=NotificationData(error=chunk.status.comment))
            self.end()


class DocumentSharingStream(MSRPStreamBase):
    type = 'document-sharing'
    priority = 1
    msrp_session_class = MSRPSession

    media_type = 'application'
    accept_types = ['application/x-webodf-documentsharing', 'application/x-webodf-genesisdocument']
    accept_wrapped_types = None

    def __init__(self, filename = None, document_title = None, session_uuid = None, continue_session = None):
        super(DocumentSharingStream, self).__init__()
        self.notification_center = NotificationCenter()
        self.mode = continue_session if continue_session is not None else 'guest' if filename is None else 'host'
        self.filename = filename
        if session_uuid is None:
            session_uuid =  str(uuid.uuid4())
        self.session_uuid = session_uuid
        self.continue_session = continue_session
        self.document_title = document_title if document_title is not None else os.path.basename(filename) if filename is not None else None
        self.receivedGenesisFile = None
        self.genesisFileSender = None
        print "NEW DocumentSharingStream - filename: ", self.filename, "session_uuid: ", self.session_uuid, "continue_session: ", self.continue_session

    @classmethod
    def new_from_sdp(cls, session, remote_sdp, stream_index):
        remote_stream = remote_sdp.media[stream_index]
        if remote_stream.media != 'application':
            raise UnknownStreamError

        remote_accept_types = remote_stream.attributes.getfirst('accept-types', None)
        if remote_accept_types is None:
            raise InvalidStreamError("remote SDP media does not have 'accept-types' attribute")
        if 'application/x-webodf-documentsharing' not in remote_accept_types.split(): # TODO: check both needed mimetypes
            raise InvalidStreamError("no compatible media types found")

        expected_transport = 'TCP/TLS/MSRP' if session.account.msrp.transport=='tls' else 'TCP/MSRP'
        if remote_stream.transport != expected_transport:
            raise InvalidStreamError("expected %s transport in chat stream, got %s" % (expected_transport, remote_stream.transport))
        if remote_stream.formats != ['*']:
            raise InvalidStreamError("wrong format list specified")

        document_title = remote_stream.attributes.getfirst('document-title', None)
        session_uuid = remote_stream.attributes.getfirst('docsession-uuid', None)
        continue_session = remote_stream.attributes.getfirst('docsession-continue', None)
        # revert on reception side
        if continue_session == "host":
            continue_session = "guest"
        elif continue_session == "guest":
            continue_session = "host"

        return cls(filename = None, document_title = document_title, session_uuid = session_uuid, continue_session = continue_session)

    def _create_local_media(self, uri_path):
        local_media = super(DocumentSharingStream, self)._create_local_media(uri_path)
        # TODO: .encode('utf8') only works with filenames with latin1 chars, investigate proper fix
        local_media.attributes.append(SDPAttribute('document-title', self.document_title.encode('utf8')))
        local_media.attributes.append(SDPAttribute('docsession-uuid', self.session_uuid))
        if self.continue_session is not None:
            local_media.attributes.append(SDPAttribute('docsession-continue', self.continue_session))
        return local_media

    def _handle_SEND(self, chunk):
        if chunk.size == 0:
            # keep-alive
            self.msrp_session.send_report(chunk, 200, 'OK')
            return

        if chunk.content_type not in self.accept_types:
            self.msrp_session.send_report(chunk, 415, 'Invalid content-type')
            return

        if chunk.content_type == 'application/x-webodf-genesisdocument':
            if self.receivedGenesisFile is None:
                print "------- creating file for genesis"
                self.receivedGenesisFile = QTemporaryFile()
                self.receivedGenesisFile.open()

            fro, to, total = chunk.byte_range
            print "------- got x-webodf-genesisdocument - fro, to, total:", fro, to, total
            self.receivedGenesisFile.seek(fro-1)
            self.receivedGenesisFile.write(chunk.data)

            if fro+chunk.size-1 == total:
                print "------- got x-webodf-genesisdocument - complete, size:", fro+chunk.size-1
                self.receivedGenesisFile.flush()
                ndata = NotificationData(filename = self.receivedGenesisFile.fileName())
                self.notification_center.post_notification('DocumentSharingStreamGenesisDocument', sender = self, data = ndata)

            return

        self.msrp_session.send_report(chunk, 200, 'OK')

        message = cjson.decode(chunk.data)
        messageType = message['type']
        messageBody = message['body']

        print "------- got x-webodf-documentsharing:", messageType

        if messageType == 'InitRequest':
            ndata = NotificationData(content = messageBody)
            self.notification_center.post_notification('DocumentSharingStreamInitRequest', sender = self, data = ndata)
            return

        if messageType == 'InitRequestReply':
            ndata = NotificationData(content = messageBody)
            self.notification_center.post_notification('DocumentSharingStreamInitRequestReply', sender = self, data = ndata)
            return

        if messageType == 'ReplayRequest':
            ndata = NotificationData(content = messageBody)
            self.notification_center.post_notification('DocumentSharingStreamReplayRequest', sender = self, data = ndata)
            return

        if messageType == 'ReplayRequestReply':
            ndata = NotificationData(content = messageBody)
            self.notification_center.post_notification('DocumentSharingStreamReplayRequestReply', sender = self, data = ndata)
            return

        if messageType == 'error':
            ndata = NotificationData(content = messageBody['error'])
            self.notification_center.post_notification('DocumentSharingStreamError', sender = self, data = ndata)
            return

        if messageType == 'NewOps':
            ndata = NotificationData(content = messageBody, content_type = chunk.content_type)
            self.notification_center.post_notification('DocumentSharingStreamNewOps', sender = self, data = ndata)

        if messageType == 'NewOpsReply':
            ndata = NotificationData(content = messageBody)
            self.notification_center.post_notification('DocumentSharingStreamNewOpsReply', sender = self, data = ndata)

    def _handle_REPORT(self, chunk):
        if self.genesisFileSender:
            self.genesisFileSender.handleReportChunk(chunk)

    def _dropGenesisFileSender(self):
        # thanks for all the file, and bye
        self.notification_center.remove_observer(self, sender=self.genesisFileSender)
        self.genesisFileSender = None

    @run_in_green_thread
    def sendMessage(self, messageType, messageBody):
        print "------- sendMessage:", messageType
        if self.msrp_session:
            message = {
                'type': messageType,
                'body': messageBody
            }

            message = cjson.encode(message)
            self.msrp_session.send_message(message, 'application/x-webodf-documentsharing')

    @run_in_green_thread
    def sendGenesisFile(self, filename):
        print "------- sendGenesisFile:", filename
        if self.msrp_session:
            self.genesisFileSender = GenesisFileSender(filename, self.msrp_session, self.msrp)
            self.notification_center.add_observer(self, sender=self.genesisFileSender)
            self.genesisFileSender.startFileSending()

    def _NH_MediaStreamDidStart(self, notification):
        self.notification_center.post_notification('DocumentSharingStreamConnected', sender = self)

    def _NH_MediaStreamDidFail(self, notification):
        if self.genesisFileSender:
            self.genesisFileSender.end()
            self._dropGenesisFileSender()
        self.notification_center.post_notification('DocumentSharingStreamDisconnected', sender = self)

    def _NH_MediaStreamDidEnd(self, notification):
        if self.genesisFileSender:
            self.genesisFileSender.end()
            self._dropGenesisFileSender()
        self.notification_center.post_notification('DocumentSharingStreamDisconnected', sender = self)

    @run_in_twisted_thread
    def _NH_FileTransferHandlerDidEnd(self, notification):
        self._dropGenesisFileSender()

    @run_in_twisted_thread
    def _NH_FileTransferHandlerError(self, notification):
        self._failure_reason = notification.data.error
        notification.center.post_notification('MediaStreamDidFail', sender=self, data=NotificationData(context='transferring', reason=self._failure_reason))
