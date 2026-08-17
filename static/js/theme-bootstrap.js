(function(){
    try {
        const theme = localStorage.getItem('studio_theme') || localStorage.getItem('canvas_theme') || 'light';
        if(theme === 'dark') {
            document.documentElement.classList.add('theme-dark');
            document.documentElement.classList.add('studio-theme-dark');
        }
    } catch(_) {}
})();
