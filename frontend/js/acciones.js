// === ACCIONES DE LA INTERFAZ SIN JAVASCRIPT INLINE ===
// La CSP no permite handlers inline (onclick="..."), asi que los elementos llevan la accion en un
// atributo de datos: data-on-click="switchView('dashboard', this)". Un unico listener por tipo de
// evento, en document, busca el atributo y ejecuta la accion. No se usa eval: la accion se interpreta
// con un parser chico que solo acepta llamadas a funciones globales, separadas por ";", con argumentos
// simples: textos ('a' o "a"), numeros, true/false/null, this, this.value, event, o el nombre de otra
// funcion global (para pasarla como callback).

const ACCION_EVENTOS = ['click', 'change', 'input', 'submit', 'keydown', 'keyup'];

function _accionParse(texto) {
    let i = 0;
    const n = texto.length;
    const espacios = () => { while (i < n && /\s/.test(texto[i])) i++; };
    const error = (msg) => { throw new Error(`Accion invalida (${msg}): ${texto}`); };
    const ident = () => {
        const m = /^[A-Za-z_$][\w$]*/.exec(texto.slice(i));
        if (!m) error('se esperaba un nombre');
        i += m[0].length;
        return m[0];
    };
    const cadena = () => {
        const q = texto[i++];
        let s = '';
        while (i < n && texto[i] !== q) {
            if (texto[i] === '\\') {
                i++;
                const c = texto[i++];
                if (c === 'n') s += '\n';
                else if (c === 't') s += '\t';
                else if (c === 'r') s += '\r';
                else if (c === 'u') { s += String.fromCharCode(parseInt(texto.slice(i, i + 4), 16)); i += 4; }
                else s += c;
            } else {
                s += texto[i++];
            }
        }
        if (texto[i] !== q) error('texto sin cerrar');
        i++;
        return { tipo: 'valor', valor: s };
    };
    const argumento = () => {
        espacios();
        const c = texto[i];
        if (c === "'" || c === '"') return cadena();
        const num = /^-?\d+(\.\d+)?/.exec(texto.slice(i));
        if (num) { i += num[0].length; return { tipo: 'valor', valor: Number(num[0]) }; }
        const nombre = ident();
        if (nombre === 'true') return { tipo: 'valor', valor: true };
        if (nombre === 'false') return { tipo: 'valor', valor: false };
        if (nombre === 'null') return { tipo: 'valor', valor: null };
        if (nombre === 'event') return { tipo: 'evento' };
        if (nombre === 'this') {
            if (texto.startsWith('.value', i)) { i += 6; return { tipo: 'valorElemento' }; }
            return { tipo: 'elemento' };
        }
        return { tipo: 'funcion', nombre };
    };
    const llamadas = [];
    espacios();
    while (i < n) {
        const nombre = ident();
        espacios();
        if (texto[i] !== '(') error('falta "("');
        i++;
        const args = [];
        espacios();
        if (texto[i] !== ')') {
            for (;;) {
                args.push(argumento());
                espacios();
                if (texto[i] === ',') { i++; continue; }
                break;
            }
        }
        if (texto[i] !== ')') error('falta ")"');
        i++;
        llamadas.push({ nombre, args });
        espacios();
        if (texto[i] === ';') i++;
        espacios();
    }
    return llamadas;
}

const _accionCache = new Map();

function ejecutarAccion(texto, elemento, evento) {
    let llamadas = _accionCache.get(texto);
    if (!llamadas) {
        llamadas = _accionParse(texto);
        _accionCache.set(texto, llamadas);
    }
    for (const llamada of llamadas) {
        const fn = window[llamada.nombre];
        if (typeof fn !== 'function') {
            console.error(`Accion: la funcion ${llamada.nombre} no existe en esta pantalla.`);
            continue;
        }
        const args = llamada.args.map(a => {
            if (a.tipo === 'valor') return a.valor;
            if (a.tipo === 'evento') return evento;
            if (a.tipo === 'elemento') return elemento;
            if (a.tipo === 'valorElemento') return elemento.value;
            return window[a.nombre];
        });
        fn(...args);  // como el handler inline: la funcion no recibe el elemento como this
    }
}

function _despachar(tipo, evento) {
    // Igual que los handlers inline: corre la accion de cada elemento desde el destino hacia arriba,
    // salvo que alguna corte la propagacion.
    let el = evento.target instanceof Element ? evento.target : null;
    const attr = `data-on-${tipo}`;
    while (el) {
        const accion = el.getAttribute(attr);
        if (accion) {
            try {
                ejecutarAccion(accion, el, evento);
            } catch (e) {
                console.error(e);
            }
            if (evento.cancelBubble) break;
        }
        el = el.parentElement;
    }
}

ACCION_EVENTOS.forEach(tipo => document.addEventListener(tipo, e => _despachar(tipo, e)));
// blur no burbujea: se escucha focusout y solo cuenta el elemento que perdio el foco.
document.addEventListener('focusout', e => {
    const el = e.target instanceof Element ? e.target : null;
    const accion = el && el.getAttribute('data-on-blur');
    if (accion) {
        try { ejecutarAccion(accion, el, e); } catch (err) { console.error(err); }
    }
});

// --- Acciones auxiliares para lo que antes era codigo suelto en el atributo ---
function quitarPadre(el) { el.parentElement.remove(); }
function seleccionarTexto(el) { el.select(); }
function irA(url) { window.location.href = url; }
function clickEn(id) { document.getElementById(id).click(); }
function ocultarElemento(id) { document.getElementById(id).style.display = 'none'; }
function siEnter(evento, fn) { if (evento.key === 'Enter') fn(); }
function putawayDesde(input, selector) { fetchPutawaySuggestion(input.value, input.parentElement.querySelector(selector)); }
