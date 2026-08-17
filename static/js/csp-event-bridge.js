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
    const allowedTargets = new WeakSet(document.querySelectorAll(selector));

    function dispatch(event, attribute){
        if(!(event.target instanceof Element)) return;
        const target = event.target.closest(selector);
        if(!target || !allowedTargets.has(target)) return;
        if(target.dataset.cspStopPropagation === 'true') {
            event.stopPropagation();
            return;
        }
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

    document.addEventListener('click', event => dispatch(event, 'data-csp-click'));
    document.addEventListener('dblclick', event => dispatch(event, 'data-csp-dblclick'));
})();
