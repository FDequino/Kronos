// Detalle de instrumento no tiene su propio item de nav -- se llega desde
// Resumen, Instrumentos, Señales o el Explorador, y sin esto ninguno queda
// marcado activo una vez adentro. Se recuerda el último hash que SÍ tenía
// item propio y se lo deja activo mientras se está en una vista de detalle.
let lastNonDetailHash = null;

function syncActiveNav() {
    const hash = window.location.hash;
    const isDetail = hash.slice(1).startsWith('detalle/');
    // Fase 1 (ver conversacion): el hash ahora puede traer una subtab propia
    // (#resumen/proyeccion) -- los href de NAV_ITEMS siempre apuntan al nivel
    // de VISTA nomas, nunca a una subtab puntual, asi que la comparacion de
    // abajo tiene que ignorar todo lo que venga despues de la primer "/".
    const viewHash = hash ? '#' + hash.slice(1).split('/')[0].split('?')[0] : '';
    const current = window.location.pathname + viewHash;
    if (!isDetail) lastNonDetailHash = current;
    const activeHref = isDetail ? lastNonDetailHash : current;
    // Scopeado a [href]: Notificaciones/Preferencias (Fase 1) tambien son
    // .nav-item pero son <button>, sin atributo href -- getAttribute('href')
    // da null ahi, y si activeHref tambien es null (entrada directa a un
    // detalle sin hash previo) "null === null" los marcaba active por error.
    document.querySelectorAll('.nav-item[href]').forEach((link) => {
        link.classList.toggle('active', link.getAttribute('href') === activeHref);
    });
}

window.addEventListener('hashchange', syncActiveNav);
document.addEventListener('DOMContentLoaded', syncActiveNav);
