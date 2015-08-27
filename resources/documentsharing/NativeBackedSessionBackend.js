/**
 * Copyright (C) 2014 KO GmbH <copyright@kogmbh.com>
 *
 * @licstart
 * This file is part of WebODF.
 *
 * WebODF is free software: you can redistribute it and/or modify it
 * under the terms of the GNU Affero General Public License (GNU AGPL)
 * as published by the Free Software Foundation, either version 3 of
 * the License, or (at your option) any later version.
 *
 * WebODF is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with WebODF.  If not, see <http://www.gnu.org/licenses/>.
 * @licend
 *
 * @source: http://www.webodf.org/
 * @source: https://github.com/kogmbh/WebODF/
 */


/**
 * This operation router is a generic one that could be used for all
 * kind of servers which communicate bi-directly with the clients by
 * messages. It is based on client-side conflict resolution with a
 * dumb server which rejects new ops based on outdated state.
 *
 * 
 * Basic mechanism:
 * Locally created ops are immediately applied, then the client keeps sending
 * messages with those ops to the server until the server accepts them.
 * Any meanwhile new ops coming in from the server are collected. Only once
 * there are no more unsynced local ops, the ops from the server are applied.
 * Any meanwhile new locally created ops are applied and collected, to be sent
 * with the next message to the server.
 * Both ops from the server and locally created are OT against each other before
 * storing, so the resulting op sequences are always based on the respective local
 * state of the document and the currently known state at the server/main session
 * If operations are coming in from the server when there are no unsynced local clients
 * the operations are directly applied.
 *
 * It is assumed that the messages are arriving in the order they are sent.
 *
 * TODO: document protocol of the messages
 * @constructor
 * @implements ops.OperationRouter
 */
function NativeBackedOperationRouter() {
    "use strict";

    var /**@const @type {!string}*/
        EVENT_BEFORESAVETOFILE =                  "beforeSaveToFile",
        /**@const @type {!string}*/
        EVENT_SAVEDTOFILE =                       "savedToFile",
        /**@const @type {!string}*/
        EVENT_HASLOCALUNSYNCEDOPERATIONSCHANGED = "hasLocalUnsyncedOperationsChanged",
        /**@const @type {!string}*/
        EVENT_HASSESSIONHOSTCONNECTIONCHANGED =   "hasSessionHostConnectionChanged",
        eventNotifier = new core.EventNotifier([
            EVENT_BEFORESAVETOFILE,
            EVENT_SAVEDTOFILE,
            EVENT_HASLOCALUNSYNCEDOPERATIONSCHANGED,
            EVENT_HASSESSIONHOSTCONNECTIONCHANGED,
            ops.OperationRouter.signalProcessingBatchStart,
            ops.OperationRouter.signalProcessingBatchEnd
        ]),
        /**@type{!ops.OperationFactory}*/
        operationFactory,
        playbackFunction,

        hasLocalUnsyncedModificationOps = false,
        hasLocalInSyncModificationOps = false,
        lastServerSyncHeadId = 0,
        sendClientOpspecsLock = false,
        sendClientOpspecsTask,
        unplayedServerOpSpecQueue = [],
        unsyncedClientOpSpecQueue = [],
        operationTransformer = new ops.OperationTransformer(),

        /**@const*/sendClientOpspecsDelay = 300,

        clientOpsSentCallback,
        replayDoneCallback,
        closeCallback;


    function emitSyncState() {
        NativeSessionBackend.updateSyncState(hasLocalUnsyncedModificationOps || hasLocalInSyncModificationOps, lastServerSyncHeadId);
    }

    function playbackOpspecs(opspecs) {
        var op, i;

        if (!opspecs.length) {
            return;
        }

        eventNotifier.emit(ops.OperationRouter.signalProcessingBatchStart, {});
        for (i = 0; i < opspecs.length; i += 1) {
            op = operationFactory.create(opspecs[i]);
            if (op !== null) {
                if (!playbackFunction(op)) {
                    eventNotifier.emit(ops.OperationRouter.signalProcessingBatchEnd, {});
                    // TODO: think about where to wire errors up to
//                     errorCb("opExecutionFailure");
                    return;
                }
            } else {
                eventNotifier.emit(ops.OperationRouter.signalProcessingBatchEnd, {});
//                 errorCb("Ignoring invlaid incoming opspec: " + op);
                return;
            }
        }
        eventNotifier.emit(ops.OperationRouter.signalProcessingBatchEnd, {});
    }

    function handleNewServerOpsWithUnsyncedClientOps(serverOps) {
        var transformResult = operationTransformer.transform(unsyncedClientOpSpecQueue, serverOps);

        if (!transformResult) {
//             errorCb("Has unresolvable conflict: ");
            return false;
        }

        unsyncedClientOpSpecQueue = transformResult.opSpecsA;
        unplayedServerOpSpecQueue = unplayedServerOpSpecQueue.concat(transformResult.opSpecsB);

        return true;
    }

    function handleNewClientOpsWithUnplayedServerOps(clientOps) {
        var transformResult = operationTransformer.transform(clientOps, unplayedServerOpSpecQueue);

        if (!transformResult) {
//             errorCb("Has unresolvable conflict: ");
            return false;
        }

        unsyncedClientOpSpecQueue = unsyncedClientOpSpecQueue.concat(transformResult.opSpecsA);
        unplayedServerOpSpecQueue = transformResult.opSpecsB;

        return true;
    }

    function receiveServerOpspecs(headId, serverOpspecs) {
        if (unsyncedClientOpSpecQueue.length > 0) {
            handleNewServerOpsWithUnsyncedClientOps(serverOpspecs);
            // could happen that ops from server make client ops obsolete
            if (unsyncedClientOpSpecQueue.length === 0) {
                eventNotifier.emit(EVENT_HASLOCALUNSYNCEDOPERATIONSCHANGED, false);
            }
        } else {
            // apply directly
            playbackOpspecs(serverOpspecs);
        }
        lastServerSyncHeadId = headId;
        emitSyncState();
    }

    function clientOpspecsSentHandler(response) {
        var cb;
        runtime.assert(clientOpsSentCallback !== undefined, "clientOpsSentCallback should be defined here");
        // unset clientOpsSentCallback before calling it, in case sendClientOpspecs calls itself recursively
        cb = clientOpsSentCallback;
        clientOpsSentCallback = undefined;
        cb(response);
    }

    function sendClientOpspecs() {
        var originalUnsyncedLength = unsyncedClientOpSpecQueue.length;

        if (originalUnsyncedLength > 0) {
            if (!NativeSessionBackend.isConnected()) {
                return;
            }

            sendClientOpspecsLock = true;
            runtime.assert(clientOpsSentCallback === undefined, "clientOpsSentCallback should be undefined here");

            hasLocalInSyncModificationOps = hasLocalUnsyncedModificationOps;
            hasLocalUnsyncedModificationOps = false;

            // setup response handler
            clientOpsSentCallback = function (response) {
                if (response.result === "success") {
                    lastServerSyncHeadId = response.headId;
                    hasLocalInSyncModificationOps = false;
                    // on success no other server ops should have sneaked in meanwhile, so no need to check
                    // got no other client ops meanwhile?
                    if (unsyncedClientOpSpecQueue.length === originalUnsyncedLength) {
                        unsyncedClientOpSpecQueue.length = 0;
                        // finally apply all server ops collected while waiting for sync
                        playbackOpspecs(unplayedServerOpSpecQueue);
                        unplayedServerOpSpecQueue.length = 0;
                        eventNotifier.emit(EVENT_HASLOCALUNSYNCEDOPERATIONSCHANGED, false);
                        sendClientOpspecsLock = false;
                    } else {
                        // send off the new client ops directly
                        unsyncedClientOpSpecQueue.splice(0, originalUnsyncedLength);
                        sendClientOpspecs();
                    }
                    emitSyncState();
                } else if (response.result === "conflict") {
                    // reconsider if previously sent ops were modification ops
                    hasLocalUnsyncedModificationOps = hasLocalInSyncModificationOps || hasLocalUnsyncedModificationOps;
                    // failed. needs to be retried based on latest ops sent from server
                    sendClientOpspecs();
                } else if (response.result === "error") {
                    // TODO: anything we should do here? what kind of errors are there, besides not able to send the message and how to react?
                }
            };

            NativeSessionBackend.sendClientOpspecs(unsyncedClientOpSpecQueue, lastServerSyncHeadId);
        }
    }

    /**
     * Sets the factory to use to create operation instances from operation specs.
     *
     * @param {!ops.OperationFactory} f
     * @return {undefined}
     */
    this.setOperationFactory = function (f) {
        operationFactory = f;
    };

    /**
     * Sets the method which should be called to apply operations.
     *
     * @param {!function(!ops.Operation):boolean} playback_func
     * @return {undefined}
     */
    this.setPlaybackFunction = function (playback_func) {
        playbackFunction = playback_func;
    };

    /**
     * Brings the locally created operations into the game.
     *
     * @param {!Array.<!ops.Operation>} operations
     * @return {undefined}
     */
    this.push = function (operations) {
        var clientOpspecs = [],
            now = Date.now(),
            hasLocalUnsyncedModificationOpsBefore = hasLocalUnsyncedModificationOps,
            hasLocalUnsyncedOpsBefore = (unsyncedClientOpSpecQueue.length !== 0),
            hasLocalUnsyncedOpsNow;

        operations.forEach(function(op) {
            var opspec = op.spec();

            hasLocalUnsyncedModificationOps = hasLocalUnsyncedModificationOps || op.isEdit;

            opspec.timestamp = now;
            clientOpspecs.push(opspec);
        });

        playbackOpspecs(clientOpspecs);

        if (unplayedServerOpSpecQueue.length > 0) {
            handleNewClientOpsWithUnplayedServerOps(clientOpspecs);
        } else {
            unsyncedClientOpSpecQueue = unsyncedClientOpSpecQueue.concat(clientOpspecs);
        }

        hasLocalUnsyncedOpsNow = (unsyncedClientOpSpecQueue.length !== 0);
        if (hasLocalUnsyncedOpsNow !== hasLocalUnsyncedOpsBefore) {
            eventNotifier.emit(EVENT_HASLOCALUNSYNCEDOPERATIONSCHANGED, hasLocalUnsyncedOpsNow);
        }
        if (hasLocalUnsyncedModificationOpsBefore !== hasLocalUnsyncedModificationOps) {
            emitSyncState();
        }

        sendClientOpspecsTask.trigger();
    };

    this.requestReplay = function (done_cb) {
        replayDoneCallback = done_cb;
        NativeSessionBackend.requestReplay();
    };

    function replayFinishedHandler() {
        replayDoneCallback();
        replayDoneCallback = undefined;
    }

    function reconnectFinishedHandler() {
        sendClientOpspecsLock = false;
        // see if there is something to show the server
        sendClientOpspecs();
    }

    /**
     * @param {function()} cb
     */
    this.close = function (cb) {
        closeCallback = cb;
        NativeSessionBackend.close();
    };

    function closedHandler() {
        closeCallback();
        closeCallback = undefined;

        // also disconnect from NativeSessionBackend now, as this router object is done now
        NativeSessionBackend.serverOpspecsArrived.disconnect(serverOpspecsArrivedHandler);
        NativeSessionBackend.connectedChanged.disconnect(connectedChangedHandler);
        NativeSessionBackend.clientOpspecsSent.disconnect(clientOpspecsSentHandler);
        NativeSessionBackend.replayFinished.disconnect(replayFinishedHandler);
        NativeSessionBackend.reconnectFinished.disconnect(reconnectFinishedHandler);
        NativeSessionBackend.closed.disconnect(closedHandler);
    }

    /**
     * @param {!string} eventId
     * @param {!Function} cb
     * @return {undefined}
     */
    this.subscribe = function (eventId, cb) {
        eventNotifier.subscribe(eventId, cb);
    };

    /**
     * @param {!string} eventId
     * @param {!Function} cb
     * @return {undefined}
     */
    this.unsubscribe = function (eventId, cb) {
        eventNotifier.unsubscribe(eventId, cb);
    };

    this.hasLocalUnsyncedOps = function () {
        return unsyncedClientOpSpecQueue.length !== 0;
    };

    /**
     * @return {!boolean}
     */
    this.hasSessionHostConnection = function () {
        return NativeSessionBackend.isConnected();
    };

    function serverOpspecsArrivedHandler(data) {
        runtime.log("serverOpspecsArrivedHandler - count of opspecs:"+data.opspecs.length);
        receiveServerOpspecs(data.headId, data.opspecs);
    }

    function connectedChangedHandler(isConnected) {
        if (isConnected) {
            NativeSessionBackend.requestServerOps(lastServerSyncHeadId);
            sendClientOpspecsLock = true;
        } else {
            // drop any lock and sync callback
            sendClientOpspecsLock = false;
            clientOpsSentCallback = undefined;
        }
        eventNotifier.emit(EVENT_HASSESSIONHOSTCONNECTIONCHANGED, isConnected);
    }

    function init() {
        sendClientOpspecsTask = core.Task.createTimeoutTask(function () {
            if (!sendClientOpspecsLock) {
                sendClientOpspecs();
            }
        }, sendClientOpspecsDelay);

        NativeSessionBackend.serverOpspecsArrived.connect(serverOpspecsArrivedHandler);
        NativeSessionBackend.connectedChanged.connect(connectedChangedHandler);
        NativeSessionBackend.clientOpspecsSent.connect(clientOpspecsSentHandler);
        NativeSessionBackend.replayFinished.connect(replayFinishedHandler);
        NativeSessionBackend.reconnectFinished.connect(reconnectFinishedHandler);
        NativeSessionBackend.closed.connect(closedHandler);
    }
    init();
}


/**
 * @constructor
 * @implements SessionBackend
 */
function NativeBackedSessionBackend() {
    "use strict";
    /**
     * @return {!string}
     */
    this.getMemberId = function () {
        return NativeSessionBackend.memberId();
    };

    /**
     * @param {!odf.OdfContainer} odfContainer (ignored/not needed for this backend)
     * @param {!function(!Object)} errorCallback
     * @return {!ops.OperationRouter}
     */
    this.createOperationRouter = function (odfContainer, errorCallback) {
        return new NativeBackedOperationRouter();
    };

    /**
     * @return {!string}
     */
    this.getGenesisUrl = function () {
        return NativeSessionBackend.genesisUrl();
    };
}

var SessionFactory = SessionFactory || {
    createSession: function() {
        return new NativeBackedSessionBackend();
    }
}
