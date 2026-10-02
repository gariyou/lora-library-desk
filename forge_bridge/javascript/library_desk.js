(() => {
    let running = false;
    let lastClick = 0;
    function placeLibraryDesk() {
        const app = gradioApp();
        const panel = app.querySelector("#library_desk_panel");
        const styles = app.querySelector("#txt2img_styles_row");
        const actions = app.querySelector("#txt2img_actions_column");
        if (panel && styles && actions && panel.parentElement !== actions) {
            // Move the original Gradio controls, retaining their bound events.
            styles.after(panel);
        }
    }
    async function syncLibraryDesk() {
        placeLibraryDesk();
        if (running || !gradioApp().querySelector("#library_desk_capture")) return;
        running = true;
        try {
            const response = await fetch("/library-desk/status");
            if (!response.ok) return;
            const state = await response.json();
            if (state.pending && !state.busy) {
                gradioApp().querySelector("#library_desk_receive").click();
            } else if (state.capture_requested || Date.now() - lastClick >= 4000) {
                gradioApp().querySelector("#library_desk_capture").click();
                lastClick = Date.now();
            }
        } catch (_) {
            // Forge may be restarting; try again on the next timer tick.
        } finally {
            running = false;
        }
    }
    onUiLoaded(() => {
        syncLibraryDesk();
        setInterval(syncLibraryDesk, 2000);
    });
    onUiUpdate(placeLibraryDesk);
})();
