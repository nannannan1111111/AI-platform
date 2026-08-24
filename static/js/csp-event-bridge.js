(function(){
    const allowedActions = new Set([
        'addGeneratorNode', 'addImageNode', 'addLoopNode', 'addOutputNode', 'addPromptNode',
        'applyGridJoinPreset', 'applyGridPreset', 'applyImageEdit', 'backToCanvasList',
        'clearEditDrawing', 'clearGridCustomLines', 'closeAssetManager', 'closeCanvasLog',
        'closeErrorModal', 'closeImageEditor', 'closeOutputLightbox', 'closePromptTemplateModal',
        'closeSmartCanvasLog', 'closeSmartCanvasShortcuts', 'closeSmartWorkflowTransferModal',
        'closeWorkflowTransferModal', 'copyErrorMessage', 'downloadPreviewGroup',
        'downloadPreviewImage', 'exportPanoramaFrame', 'exportSelectedSmartWorkflow',
        'exportSelectedWorkflow', 'exportSelectedWorkflowToLibrary', 'exportVideoFrame',
        'groupSelectedImages', 'menuAdd', 'navigatePreviewImage', 'openCanvasLog',
        'openSmartCanvasShortcuts', 'redoEditDrawing', 'resetCropBox', 'resetGridJoinLayout',
        'resetImageEditZoom', 'setBrushTool', 'setGridCustomOrientation', 'setGridJoinOutputSize',
        'setGridOperationMode', 'toggleGridCustomMode', 'togglePanoramaPreview',
        'togglePreviewCompare', 'toggleQuickToolbar', 'undoEditDrawing', 'undoGridCustomLine'
    ]);
    const selector = '[data-csp-click],[data-csp-dblclick],[data-csp-stop-propagation]';
    const allowedTargets = [...document.querySelectorAll(selector)];

    function dispatch(target, event, attribute){
        const action = target.getAttribute(attribute);
        if(!action || !allowedActions.has(action)) return;
        const handler = globalThis[action];
        if(typeof handler !== 'function') {
            console.error(`CSP action is unavailable: ${action}`);
            return;
        }
        let args = [];
        if(target.dataset.cspArgs) {
            try { args = JSON.parse(target.dataset.cspArgs); }
            catch(_) { return; }
        }
        if(target.dataset.cspPassEvent === 'true') args.push(event);
        handler.apply(target, args);
    }

    allowedTargets.forEach(target => {
        if(target.dataset.cspStopPropagation === 'true') {
            target.addEventListener('click', event => event.stopPropagation());
            target.addEventListener('dblclick', event => event.stopPropagation());
        }
        if(target.hasAttribute('data-csp-click')) {
            target.addEventListener('click', event => dispatch(target, event, 'data-csp-click'));
        }
        if(target.hasAttribute('data-csp-dblclick')) {
            target.addEventListener('dblclick', event => dispatch(target, event, 'data-csp-dblclick'));
        }
    });
})();
