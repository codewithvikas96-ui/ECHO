import { useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { api, message, post, setActiveAccount } from './api';

type Session = { username: string | null };

export function Account({ children }: { children: (username: string, logout: () => Promise<void>) => ReactNode }) {
  const [username, setUsername] = useState<string | null | undefined>(undefined);
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  function showSession(name: string | null) {
    setActiveAccount(name);
    setUsername(name);
  }

  useEffect(() => {
    let current = true;
    async function checkSession() {
      try {
        const session = await api<Session>('/account');
        if (current) showSession(session.username);
      } catch (cause) {
        if (current) { showSession(null); setError(message(cause)); }
      }
    }
    function expire() { showSession(null); }
    function sync(event: StorageEvent) {
      if (event.key === 'echo-account-change') location.reload();
    }
    function restore(event: PageTransitionEvent) { if (event.persisted) location.reload(); }
    void checkSession();
    window.addEventListener('storage', sync);
    window.addEventListener('focus', checkSession);
    window.addEventListener('pageshow', restore);
    window.addEventListener('echo-session-ended', expire);
    return () => {
      current = false;
      window.removeEventListener('storage', sync);
      window.removeEventListener('focus', checkSession);
      window.removeEventListener('pageshow', restore);
      window.removeEventListener('echo-session-ended', expire);
    };
  }, []);

  function changed(name: string | null) {
    showSession(name);
    // Only a change notification is shared with other tabs. Credentials stay in the HTTP-only cookie.
    try { localStorage.setItem('echo-account-change', String(Date.now())); } catch { /* Storage may be disabled. */ }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError('');
    try {
      const result = await post<Session>(register ? '/account/register' : '/account/login', {
        username: form.get('username'), password: form.get('password'),
      });
      changed(result.username);
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }

  async function logout() {
    await post('/account/logout');
    changed(null);
    setRegister(false);
    setError('');
  }

  if (username) return children(username, logout);
  return <main className="account-screen"><section className="account-panel panel">
    <div className="brand"><img src="/echo.svg" width="40" height="40" alt="" /><b>ECHO</b></div>
    {username === undefined ? <p role="status">Opening your account…</p> : <>
      <h1>{register ? 'Create your account' : 'Sign in to your archive'}</h1>
      <form key={String(register)} onSubmit={submit}>
        <label>Username<input name="username" required minLength={3} maxLength={40} pattern="[A-Za-z0-9_.\-]+"
          title="Use letters, numbers, dots, underscores or hyphens." autoComplete="username" autoCapitalize="none" /></label>
        <label>Password<input name="password" type="password" required minLength={register ? 12 : 1}
          maxLength={256} autoComplete={register ? 'new-password' : 'current-password'} /></label>
        {register && <small>Use at least 12 characters for your personal password.</small>}
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{register ? 'Create account' : 'Sign in'}</button>
      </form>
      <button className="plain" disabled={busy} onClick={() => { setRegister(!register); setError(''); }}>
        {register ? 'Back to sign in' : 'Create an account'}
      </button>
    </>}
  </section></main>;
}
