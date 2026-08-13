import {
  Activity,
  ArrowRight,
  Boxes,
  CheckCircle2,
  Cpu,
  Eye,
  EyeOff,
  LoaderCircle,
  LockKeyhole,
  Mail,
  ShieldCheck,
  Sparkles,
  Truck,
  Warehouse,
  Zap,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import { useAuth } from "./AuthContext";
import "./LoginPage.css";

interface DemoAccount {
  role: string;
  name: string;
  email: string;
  badge: string;
  description: string;
}

const DEMO_ACCOUNTS: DemoAccount[] = [
  {
    role: "Owner",
    name: "Dan Whitfield",
    email: "owner@example.com",
    badge: "👑 All Warehouses",
    description: "Full access across Reno & Columbus, audit trail, user admin",
  },
  {
    role: "Manager",
    name: "Maya Patel",
    email: "manager@example.com",
    badge: "🛡️ Reno Manager",
    description: "Inventory adjustments, picking management, receiving approval",
  },
  {
    role: "Trusted",
    name: "Jon Reed",
    email: "trusted@example.com",
    badge: "⚡ Trusted Staff",
    description: "Pick/pack workflows, barcode scanning, order label creation",
  },
  {
    role: "Staff",
    name: "Ari Lane",
    email: "staff@example.com",
    badge: "👷 Reno Staff",
    description: "Inbound receiving scans, picking checklist fulfillment",
  },
];

export function LoginPage() {
  const { error, login } = useAuth();
  const [email, setEmail] = useState("owner@example.com");
  const [password, setPassword] = useState("sHRAVANSAPATE@123$");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [validation, setValidation] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<string>("Owner");

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim() || !password) {
      setValidation("Please enter both your email and password.");
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

  const selectDemoAccount = (acc: DemoAccount) => {
    setActiveTab(acc.role);
    setEmail(acc.email);
    setPassword("sHRAVANSAPATE@123$");
    setValidation(null);
  };

  return (
    <main className="landingWrapper">
      {/* Background Animated Glow Spheres & Matrix Grid */}
      <div className="bgGridOverlay" aria-hidden="true" />
      <div className="bgGlowBlob bgGlowBlob--1" aria-hidden="true" />
      <div className="bgGlowBlob bgGlowBlob--2" aria-hidden="true" />
      <div className="bgGlowBlob bgGlowBlob--3" aria-hidden="true" />

      {/* Top Navbar Header */}
      <header className="landingNav">
        <div className="navBrand">
          <div className="navLogoIcon">
            <Boxes size={22} className="text-emerald" />
          </div>
          <div className="navBrandText">
            <span className="navBrandTitle">WHITFIELD</span>
            <span className="navBrandSub">LOGISTICS WMS</span>
          </div>
        </div>

        <div className="navTelemetry">
          <span className="telemetryPill telemetryPill--live">
            <span className="pulseDot" /> Live System v2.4
          </span>
          <span className="telemetryPill telemetryPill--db">
            <CheckCircle2 size={13} className="text-emerald" /> PostgreSQL Active
          </span>
          <span className="telemetryPill telemetryPill--voice">
            <Sparkles size={13} className="text-amber" /> Voice AI Ready
          </span>
        </div>
      </header>

      {/* Main Split Landing Container */}
      <div className="landingContainer">
        {/* Left Column: Hero & Product Showcase */}
        <section className="heroShowcase">
          <div className="heroBadge">
            <Sparkles size={14} />
            <span>Next-Generation Warehouse Operations</span>
          </div>

          <h1 className="heroHeading">
            Enterprise WMS built for <span className="gradientText">speed, safety & scale</span>.
          </h1>

          <p className="heroDescription">
            Replace error-prone spreadsheets with automated zero-oversell inventory,
            sub-second barcode validation, carrier dispatch, and hands-free Gemini Voice AI.
          </p>

          {/* Feature Highlights Cards */}
          <div className="featureGrid">
            <div className="featureCard">
              <div className="featureCardIcon featureCardIcon--emerald">
                <Warehouse size={20} />
              </div>
              <div className="featureCardBody">
                <h4>Dual-Warehouse Architecture</h4>
                <p>Strict server-enforced isolation for Reno (RNO) & Columbus (CMH) facilities.</p>
              </div>
            </div>

            <div className="featureCard">
              <div className="featureCardIcon featureCardIcon--cyan">
                <Zap size={20} />
              </div>
              <div className="featureCardBody">
                <h4>Atomic Stock Allocation</h4>
                <p>PostgreSQL lock transactions eliminate double-picks and overselling risks.</p>
              </div>
            </div>

            <div className="featureCard">
              <div className="featureCardIcon featureCardIcon--amber">
                <Cpu size={20} />
              </div>
              <div className="featureCardBody">
                <h4>Hands-Free Voice Assistant</h4>
                <p>Execute stock queries and barcode receipts via natural voice conversation.</p>
              </div>
            </div>

            <div className="featureCard">
              <div className="featureCardIcon featureCardIcon--violet">
                <ShieldCheck size={20} />
              </div>
              <div className="featureCardBody">
                <h4>Immutable Audit Trail</h4>
                <p>Every scan, item adjustment, and shipment is permanently recorded.</p>
              </div>
            </div>
          </div>

          {/* Warehouse Nodes Status Bar */}
          <div className="warehouseStatusBar">
            <div className="nodeItem">
              <span className="nodeDot" />
              <div>
                <strong>Reno Facility (RNO)</strong>
                <small>Main West Hub · Live</small>
              </div>
            </div>
            <div className="nodeDivider" />
            <div className="nodeItem">
              <span className="nodeDot" />
              <div>
                <strong>Columbus Hub (CMH)</strong>
                <small>Midwest Regional · Live</small>
              </div>
            </div>
            <div className="nodeDivider" />
            <div className="nodeItem">
              <Activity size={16} className="text-emerald" />
              <div>
                <strong>310+ Units</strong>
                <small>Active Balance</small>
              </div>
            </div>
          </div>
        </section>

        {/* Right Column: Premium Auth Card */}
        <section className="authSection" aria-labelledby="signin-heading">
          <div className="authGlassCard">
            <div className="authCardHeader">
              <div className="authIconWrapper">
                <LockKeyhole size={24} className="text-emerald" />
              </div>
              <div>
                <h2 id="signin-heading">Warehouse Sign-In</h2>
                <p className="authSubtext">Select a demo role or enter your credentials</p>
              </div>
            </div>

            {/* 1-Click Demo Accounts Selector */}
            <div className="demoAccountsWrapper">
              <div className="demoHeader">
                <span className="demoTitle">1-Click Fast Login:</span>
                <span className="demoHint">Auto-fills credentials</span>
              </div>
              <div className="demoPillsRow">
                {DEMO_ACCOUNTS.map((acc) => (
                  <button
                    key={acc.role}
                    type="button"
                    className={`demoRolePill ${activeTab === acc.role ? "demoRolePill--active" : ""}`}
                    onClick={() => selectDemoAccount(acc)}
                  >
                    <span>{acc.badge}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Login Form */}
            <form className="authForm" onSubmit={handleSubmit} noValidate>
              <div className="inputGroup">
                <label htmlFor="login-email">Email Address</label>
                <div className="inputWrapper">
                  <Mail size={18} className="inputIcon" />
                  <input
                    id="login-email"
                    type="email"
                    autoComplete="username"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="name@example.com"
                    required
                    className="styledInput"
                  />
                </div>
              </div>

              <div className="inputGroup">
                <label htmlFor="login-password">Password</label>
                <div className="inputWrapper">
                  <LockKeyhole size={18} className="inputIcon" />
                  <input
                    id="login-password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••••••"
                    required
                    className="styledInput styledInput--password"
                  />
                  <button
                    type="button"
                    className="togglePasswordBtn"
                    onClick={() => setShowPassword((prev) => !prev)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                  >
                    {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                  </button>
                </div>
              </div>

              {/* Error Notice */}
              {(validation || error) && (
                <div className="authErrorNotice" role="alert">
                  <span className="errorDot">!</span>
                  <span>{validation || error}</span>
                </div>
              )}

              {/* Submit Action Button */}
              <button
                type="submit"
                disabled={submitting}
                className="submitAuthButton"
              >
                {submitting ? (
                  <>
                    <LoaderCircle className="spinnerIcon" size={20} />
                    <span>Verifying Session…</span>
                  </>
                ) : (
                  <>
                    <span>Enter Warehouse Hub</span>
                    <ArrowRight size={18} className="arrowIcon" />
                  </>
                )}
              </button>
            </form>

            <div className="authCardFooter">
              <div className="securityBadge">
                <ShieldCheck size={14} className="text-emerald" />
                <span>Argon2id Hashed · PyJWT Bearer Auth · Role Enforced</span>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
