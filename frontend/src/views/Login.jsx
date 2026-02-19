export function Login() {
  return (
    <div class="login-page">
      <h1>Tasche</h1>
      <p>Sign in to continue</p>
      <a href="/api/auth/login" class="btn btn-primary login-btn">
        Sign in with GitHub
      </a>
    </div>
  );
}
