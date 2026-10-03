// Script de admin.html (antes inline en la pagina; la CSP ya no permite scripts inline).
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if(sidebar && overlay) {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('open');
    }
}

// Acordeon del menu lateral (acc-<nombre>, icon-acc-<nombre>, btn-acc-<nombre>).
function alternarAcordeon(nombre) {
    const acc = document.getElementById(`acc-${nombre}`);
    if (!acc) return;
    const abrir = !acc.classList.contains('open');
    acc.classList.toggle('open', abrir);
    const icon = document.getElementById(`icon-acc-${nombre}`);
    if (icon) icon.textContent = abrir ? '▲' : '▼';
    document.getElementById(`btn-acc-${nombre}`)?.classList.toggle('active', abrir);
}

function toggleReportsAccordion() {
    const acc = document.getElementById('acc-reports');
    const icon = document.getElementById('icon-acc-reports');
    const btn = document.getElementById('btn-acc-reports');

    if (acc.classList.contains('open')) {
        acc.classList.remove('open');
        if (icon) icon.textContent = '▼';
        if (btn) btn.classList.remove('active');
    } else {
        acc.classList.add('open');
        if (icon) icon.textContent = '▲';
        if (btn) btn.classList.add('active');
    }
}

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

    document.querySelectorAll('.rail-btn, .rail-sub-btn').forEach(btn => {
        if(btn.id === 'btn-toggle-help' || btn.id === 'btn-acc-reports' || btn.getAttribute('data-on-click')?.includes('toggleTheme')) return;

        btn.addEventListener('click', () => {
            document.querySelectorAll('.rail-btn, .rail-sub-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            if (btn.classList.contains('rail-sub-btn')) {
                document.getElementById('btn-acc-reports')?.classList.add('active');
            }

            if (window.innerWidth <= 850) {
                const sidebar = document.getElementById('sidebar');
                const overlay = document.getElementById('sidebar-overlay');
                if(sidebar && overlay) {
                    sidebar.classList.remove('open');
                    overlay.classList.remove('open');
                }
            }
        });
    });
});
