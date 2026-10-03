// Paginas de lectura (instructivo, privacidad, terminos): tema claro/oscuro compartido con el resto de la
// suite (localStorage "jztech-theme"), boton de imprimir y marca en el indice la parte que se esta leyendo.
'use strict';

(function () {
    let guardado = 'light';
    try { guardado = localStorage.getItem('jztech-theme') || 'light'; } catch (e) { /* sin almacenamiento */ }
    document.documentElement.setAttribute('data-theme', guardado);
})();

document.addEventListener('DOMContentLoaded', () => {
    const tema = document.getElementById('doc-tema');
    if (tema) {
        tema.addEventListener('click', () => {
            const nuevo = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', nuevo);
            try { localStorage.setItem('jztech-theme', nuevo); } catch (e) { /* sin almacenamiento */ }
        });
    }
    const imprimir = document.getElementById('doc-imprimir');
    if (imprimir) imprimir.addEventListener('click', () => window.print());

    // Indice: marca la seccion visible.
    const enlaces = Array.from(document.querySelectorAll('.doc-indice a[href^="#"]'));
    if (enlaces.length === 0 || !('IntersectionObserver' in window)) return;
    const porId = new Map(enlaces.map(a => [a.getAttribute('href').slice(1), a]));
    const visibles = new Set();
    const secciones = Array.from(document.querySelectorAll('.doc-texto > section[id]'));
    const marcar = () => {
        // Al final de la pagina las ultimas secciones (cortas) no llegan arriba: se marca la ultima.
        const alFinal = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
        const actual = alFinal ? secciones[secciones.length - 1] : secciones.find(s => visibles.has(s.id));
        enlaces.forEach(a => a.classList.remove('actual'));
        if (actual && porId.has(actual.id)) porId.get(actual.id).classList.add('actual');
    };
    window.addEventListener('scroll', marcar, { passive: true });
    const observador = new IntersectionObserver(entradas => {
        entradas.forEach(e => { if (e.isIntersecting) visibles.add(e.target.id); else visibles.delete(e.target.id); });
        marcar();
    }, { rootMargin: '-80px 0px -55% 0px' });
    secciones.forEach(s => observador.observe(s));
});
