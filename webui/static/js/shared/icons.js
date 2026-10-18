// Iconos inline SVG reusables fuera de Jinja (ver conversación, Fase 2) --
// mismo estilo que _icons.html (viewBox 20x20, stroke currentColor) pero acá
// porque estos módulos arman HTML 100% en JS, sin pasar por el template.
// Reemplazan emoji sueltos (🛡️/⚡/🎯/🛑/⏱️) que no heredaban color del tema
// ni se veían consistentes con el resto del sistema de iconos.
export const SHIELD_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 2.5 16 5v4.5c0 4-2.6 6.8-6 8-3.4-1.2-6-4-6-8V5l6-2.5Z"/></svg>';
// Mismo trazo que el icono "signal" de _icons.html -- un solo rayo en todo
// el sistema, no dos variantes distintas de la misma idea.
export const BOLT_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 2.5 3.5 11H9l-1 6.5L16 9h-5.5l1-6.5Z"/></svg>';
export const TARGET_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="7"/><circle cx="10" cy="10" r="3.5"/><circle cx="10" cy="10" r="0.6" fill="currentColor"/></svg>';
export const STOP_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6.2 2.5h7.6l4.2 4.2v6.6l-4.2 4.2H6.2L2 13.3V6.7l4.2-4.2Z"/><path d="M7.5 7.5l5 5M12.5 7.5l-5 5"/></svg>';
export const CLOCK_ICON = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="7"/><path d="M10 6.5V10l2.8 1.6"/></svg>';
