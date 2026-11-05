const form = document.getElementById('contact-form');
const button = document.getElementById('c-submit');
document.getElementById('c-ts').value = String(Date.now());
const topic = document.getElementById('c-topic');
setTimeout(() => {
  for (const t of ['Membership', 'Partnership']) topic.add(new Option(t, t.toLowerCase()));
}, 100);
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!form.checkValidity()) { form.reportValidity(); return; }
  button.classList.remove('is-error');
  button.textContent = 'Sending…';
  try {
    const res = await fetch('/api/send.php', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(new FormData(form)),
    });
    const data = await res.json().catch(() => ({ ok: false }));
    if (!data.ok) throw new Error('failed');
    document.getElementById('success-name').textContent = document.getElementById('c-name').value;
    document.getElementById('form-inner').classList.add('hidden');
    const success = document.getElementById('success');
    success.hidden = false;
    success.classList.remove('hidden');
  } catch {
    button.classList.add('is-error');
    button.textContent = 'Try again';
  }
});
