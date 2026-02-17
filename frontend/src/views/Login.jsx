export function Login() {
  return (
    <div class="login-page">
      <h1>Tasche</h1>
      <p>Save articles. Read later. Listen anywhere.</p>
      <a href="/api/auth/login" class="btn btn-primary login-btn">
        Sign in with GitHub
      </a>
    </div>
  );
}
