function syncActiveNav() {
    const current = window.location.pathname + window.location.hash;
    document.querySelectorAll('.nav-item').forEach((link) => {
        link.classList.toggle('active', link.getAttribute('href') === current);
    });
}

window.addEventListener('hashchange', syncActiveNav);
document.addEventListener('DOMContentLoaded', syncActiveNav);
