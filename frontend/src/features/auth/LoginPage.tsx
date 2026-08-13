import { Boxes, LoaderCircle, LockKeyhole } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useAuth } from "./AuthContext";

export function LoginPage() {
  const { error, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [validation, setValidation] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim() || !password) {
      setValidation("Enter both your email and password.");
      return;
    }
    setValidation(null);
    setSubmitting(true);
    try {
      await login(email.trim().toLowerCase(), password);
    } catch {
      // The auth provider exposes a normalized, user-safe message.
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="loginPage">
      <section className="loginCard" aria-labelledby="login-title">
        <div className="loginBrand">
          <div className="brandMark"><Boxes size={24} /></div>
          <div>
            <p className="eyebrow">Whitfield fulfillment</p>
            <h1 id="login-title">Sign in to WMS Control</h1>
          </div>
        </div>
        <p className="loginIntro">Use your assigned warehouse account. Access is enforced by role and warehouse on every request.</p>
        <form className="formStack" onSubmit={handleSubmit} noValidate>
          <label>
            <span>Email</span>
            <input
              autoComplete="username"
              autoFocus
              inputMode="email"
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@whitfield.example"
              type="email"
              value={email}
            />
          </label>
          <label>
            <span>Password</span>
            <input
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              value={password}
            />
          </label>
          {(validation || error) && <div className="inlineNotice inlineNotice-error" role="alert">{validation || error}</div>}
          <button className="primaryButton fullWidth" disabled={submitting} type="submit">
            {submitting ? <LoaderCircle className="spin" size={18} /> : <LockKeyhole size={18} />}
            {submitting ? "Signing in…" : "Sign in securely"}
          </button>
        </form>
      </section>
    </main>
  );
}
