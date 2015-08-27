//TODO: Add WebODF bridging functions

var Bridge = Bridge || (function() {
    function uintEightArrayToBaseSixFourString(data) {
        var baseSixFourString = '',
            len = data.byteLength,
            i;
        for (i = 0; i < len; i += 1) {
            baseSixFourString += String.fromCharCode(data[i]);
        }
        return window.btoa(baseSixFourString);
    }

    // this could be a variable and be undefined or the method depending on a flag of QtBridgeObject
    var saveCallback = function() {
        runtime.assert(Bridge.editor, "Editor should be set");
        Bridge.editor.getDocumentAsByteArray(function(err, data) {
            if(!err) {
                QtBridgeObject.endSaveDocument(uintEightArrayToBaseSixFourString(data));
            }
        });
    };

    function onEditorCreationRequested() {
        runtime.assert(!Bridge.editor, "Editor should not be set");
        var editorOptions = {
            allFeaturesEnabled: true,
            saveCallback: saveCallback
        };
        function onEditorCreated(err, editor) {
            if (err) {
                // something failed unexpectedly, deal with it (here just a simple alert)
                alert(err);
                return;
            }
            runtime.assert(SessionFactory, "SessionFactory should be defined by SessionBackend");
            runtime.assert(SessionFactory.createSession, "SessionFactory should have a createSession method");
            Bridge.editor = editor;
            QtBridgeObject.endEditorCreation();
        }
        Wodo.createCollabTextEditor('editorContainer', editorOptions, onEditorCreated);
    }

    function onJoinSessionRequested() {
        runtime.assert(Bridge.editor, "Editor should be set")

        Bridge.editor.joinSession(SessionFactory.createSession(), function(err) {
            QtBridgeObject.endJoinSession(err);
        });
    }

    function onLeaveSessionRequested() {
        runtime.assert(Bridge.editor, "Editor should be set")

        Bridge.editor.leaveSession(function(err) {
            QtBridgeObject.endLeaveSession(err);
        });
    }


    var autoconnectSignals = [
        "editorCreationRequested()",
        "joinSessionRequested()",
        "leaveSessionRequested()"
    ];

    for(var i in QtBridgeObject) {
        if(autoconnectSignals.indexOf(i) != -1) {
            var slotName = "on" + i[0].toUpperCase() + i.substring(1, i.indexOf('('));
            QtBridgeObject[i].connect(eval(slotName));
        }
    }

    return {
        editor: null
    }
}());
