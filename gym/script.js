// Mobile nav
const toggle = document.querySelector('.nav-toggle');
const menu = document.getElementById('nav-menu');

toggle.addEventListener('click', () => {
  const open = menu.classList.toggle('open');
  toggle.setAttribute('aria-expanded', String(open));
});

menu.addEventListener('click', (e) => {
  if (e.target.tagName === 'A') {
    menu.classList.remove('open');
    toggle.setAttribute('aria-expanded', 'false');
  }
});

// Trial form — no backend, so just validate and confirm
const form = document.getElementById('trial-form');
const note = form.querySelector('.form-note');

form.addEventListener('submit', (e) => {
  e.preventDefault();
  const name = form.elements.name.value.trim();
  const email = form.elements.email.value.trim();

  if (!name || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    note.textContent = 'Add your name and a valid email so we can confirm your session.';
    note.classList.add('error');
    return;
  }

  note.classList.remove('error');
  note.textContent = `Thanks, ${name} — a coach will email you within one business day.`;
  form.reset();
});

// Footer year
document.getElementById('year').textContent = new Date().getFullYear();
