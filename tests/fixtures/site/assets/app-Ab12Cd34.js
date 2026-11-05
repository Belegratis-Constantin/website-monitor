const toggle = document.querySelector('.menu-toggle');
const nav = document.getElementById('nav');
toggle?.addEventListener('click', () => {
  const open = toggle.getAttribute('aria-expanded') !== 'true';
  toggle.setAttribute('aria-expanded', String(open));
  nav.hidden = !open;
});
document.querySelector('.theme-toggle')?.addEventListener('click', () => {
  const html = document.documentElement;
  html.dataset.theme = html.dataset.theme === 'dark' ? 'light' : 'dark';
});
document.documentElement.dataset.theme = 'light';
const rendered = document.getElementById('rendered');
if (rendered) rendered.textContent = 'Rendered by JavaScript.';
