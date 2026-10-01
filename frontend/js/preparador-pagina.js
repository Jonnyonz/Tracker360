// Script de preparador.html (antes inline en la pagina; la CSP ya no permite scripts inline).
function toggleTheme() {
    const html = document.documentElement;
    const currentTheme = html.getAttribute('data-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', newTheme);
    localStorage.setItem('jztech-theme', newTheme);
}

document.addEventListener('DOMContentLoaded', () => {
    const savedTheme = localStorage.getItem('jztech-theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);
});
