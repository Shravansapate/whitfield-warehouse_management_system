import React, { useState, useEffect, useRef } from 'react';
import {
  Package, Truck, BarChart3, Lock, Zap, Users,
  ArrowRight, CheckCircle, TrendingUp, Radio
} from 'lucide-react';
import './LandingPage.css';

const FEATURES = [
  {
    icon: <Package size={28} />,
    title: 'Smart Inventory',
    description: 'Real-time stock tracking across multiple warehouses with automated low-stock alerts'
  },
  {
    icon: <Truck size={28} />,
    title: 'Order Pipeline',
    description: 'Visual tracking of orders through receiving, packing, and shipping stages'
  },
  {
    icon: <BarChart3 size={28} />,
    title: 'Analytics Dashboard',
    description: 'Comprehensive insights into warehouse metrics and performance trends'
  },
  {
    icon: <Lock size={28} />,
    title: 'Role-Based Access',
    description: 'Secure access control with granular permissions for different warehouse roles'
  },
  {
    icon: <Zap size={28} />,
    title: 'Voice Assistant',
    description: 'Hands-free warehouse operations with voice command support'
  },
  {
    icon: <Users size={28} />,
    title: 'Team Management',
    description: 'Effortless staff management and performance tracking'
  }
];

const STATS = [
  { label: 'Units Tracked', value: '500K+' },
  { label: 'Warehouses', value: '2' },
  { label: 'Daily Orders', value: '1K+' },
  { label: 'Uptime', value: '99.9%' }
];

function useCountUp(target: string) {
  const [value, setValue] = useState('0');
  const startRef = useRef<number | null>(null);

  useEffect(() => {
    const numericTarget = parseInt(target.replace(/[^0-9]/g, ''));
    startRef.current = null;
    let frame: number;

    const step = (t: number) => {
      if (startRef.current === null) startRef.current = t;
      const progress = Math.min((t - startRef.current) / 1500, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = Math.round(eased * numericTarget);
      setValue(current.toLocaleString());
      if (progress < 1) frame = requestAnimationFrame(step);
    };

    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target]);

  return value + target.replace(/[0-9]/g, '');
}

export function LandingPage({ onSignIn }: { onSignIn: () => void }) {
  const [scrollY, setScrollY] = useState(0);

  useEffect(() => {
    const handleScroll = () => setScrollY(window.scrollY);
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <div className="landing-page">
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Inter:wght@400;500;600;700&display=swap');
        @keyframes fadeInUp { 
          from { opacity: 0; transform: translateY(30px); } 
          to { opacity: 1; transform: translateY(0); } 
        }
        @keyframes fadeIn { 
          from { opacity: 0; } 
          to { opacity: 1; } 
        }
        @keyframes slideInLeft {
          from { opacity: 0; transform: translateX(-50px); }
          to { opacity: 1; transform: translateX(0); }
        }
        @keyframes slideInRight {
          from { opacity: 0; transform: translateX(50px); }
          to { opacity: 1; transform: translateX(0); }
        }
        @keyframes float {
          0%, 100% { transform: translateY(0px); }
          50% { transform: translateY(-20px); }
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>

      {/* Hero Section */}
      <header className="hero">
        <div className="hero-gradient" style={{ opacity: Math.max(0.1, 1 - scrollY / 1000) }} />
        <nav className="navbar">
          <div className="nav-brand">
            <Package size={20} />
            <span>Whitfield WMS</span>
          </div>
          <button className="nav-signin" onClick={onSignIn}>
            Sign In
          </button>
        </nav>

        <div className="hero-content">
          <div className="hero-badge">
            <Radio size={13} style={{ animation: 'pulse 1.6s ease-in-out infinite' }} />
            <span>Next-Gen Warehouse Management</span>
          </div>

          <h1 className="hero-title">
            Warehouse Management <span className="accent">Reimagined</span>
          </h1>

          <p className="hero-subtitle">
            Enterprise-grade fulfillment control with real-time insights, intelligent automation, and intuitive operations management for modern logistics.
          </p>

          <div className="hero-cta">
            <button className="btn btn-primary" onClick={onSignIn}>
              <span>Enter Warehouse Hub</span>
              <ArrowRight size={18} />
            </button>
          </div>

          <div className="hero-stats">
            {STATS.map((stat, i) => (
              <div key={stat.label} className="stat-item" style={{ animationDelay: `${i * 0.1}s` }}>
                <div className="stat-value">{useCountUp(stat.value)}</div>
                <div className="stat-label">{stat.label}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="hero-visual">
          <div className="dashboard-preview">
            <div className="preview-header">
              <div className="preview-dot" style={{ background: '#E4572E' }} />
              <div className="preview-dot" style={{ background: '#F4A623' }} />
              <div className="preview-dot" style={{ background: '#4CAF6D' }} />
            </div>
            <div className="preview-content">
              <div className="preview-line" style={{ width: '60%' }} />
              <div className="preview-line" style={{ width: '80%' }} />
              <div className="preview-chart">
                <div className="chart-bar" style={{ height: '40%' }} />
                <div className="chart-bar" style={{ height: '60%' }} />
                <div className="chart-bar" style={{ height: '45%' }} />
                <div className="chart-bar" style={{ height: '70%' }} />
                <div className="chart-bar" style={{ height: '55%' }} />
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* Features Section */}
      <section className="features">
        <div className="container">
          <div className="section-header">
            <h2 style={{ color: "#ffffff", fontWeight: 800, fontSize: "44px", letterSpacing: "-0.01em", textShadow: "0 2px 10px rgba(0,0,0,0.5)", margin: "0 0 12px" }}>
              Powerful Features
            </h2>
            <p style={{ color: "#cbd5e1", fontSize: "18px", margin: 0, fontWeight: 500 }}>
              Everything you need to manage modern warehouse operations
            </p>
          </div>

          <div className="features-grid">
            {FEATURES.map((feature, i) => (
              <div key={feature.title} className="feature-card" style={{ animationDelay: `${i * 0.1}s` }}>
                <div className="feature-icon">{feature.icon}</div>
                <h3 style={{ color: "#ffffff", fontSize: "20px", fontWeight: 700, margin: "0 0 12px" }}>
                  {feature.title}
                </h3>
                <p style={{ color: "#cbd5e1", fontSize: "15px", margin: 0, lineHeight: 1.6 }}>
                  {feature.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Benefits Section */}
      <section className="benefits">
        <div className="container">
          <div className="benefits-grid">
            <div className="benefit-item" style={{ animationDelay: '0s' }}>
              <CheckCircle size={24} className="benefit-icon" />
              <h3 style={{ color: "#ffffff", fontSize: "22px", fontWeight: 700, margin: "0 0 12px" }}>99.9% Uptime</h3>
              <p style={{ color: "#cbd5e1", fontSize: "15px", margin: 0, lineHeight: 1.6 }}>Enterprise-grade reliability with redundant systems and automatic failover</p>
            </div>
            <div className="benefit-item" style={{ animationDelay: '0.1s' }}>
              <TrendingUp size={24} className="benefit-icon" />
              <h3 style={{ color: "#ffffff", fontSize: "22px", fontWeight: 700, margin: "0 0 12px" }}>Real-time Analytics</h3>
              <p style={{ color: "#cbd5e1", fontSize: "15px", margin: 0, lineHeight: 1.6 }}>Live dashboards with actionable insights into warehouse performance</p>
            </div>
            <div className="benefit-item" style={{ animationDelay: '0.2s' }}>
              <Lock size={24} className="benefit-icon" />
              <h3 style={{ color: "#ffffff", fontSize: "22px", fontWeight: 700, margin: "0 0 12px" }}>Bank-Grade Security</h3>
              <p style={{ color: "#cbd5e1", fontSize: "15px", margin: 0, lineHeight: 1.6 }}>Enterprise security with role-based access and audit logging</p>
            </div>
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="cta-section">
        <div className="cta-content">
          <h2 style={{ color: "#ffffff", fontSize: "42px", fontWeight: 800, margin: "0 0 16px" }}>Ready to Transform Your Warehouse?</h2>
          <p style={{ color: "#e2e8f0", fontSize: "18px", margin: "0 0 32px" }}>Join leading logistics companies using Whitfield WMS</p>
          <button className="btn btn-primary btn-large" onClick={onSignIn}>
            <span>Start Your Journey</span>
            <ArrowRight size={20} />
          </button>
        </div>
      </section>

      {/* Footer */}
      <footer className="footer">
        <div className="container">
          <div className="footer-content">
            <div className="footer-section">
              <h4 style={{ color: "#ffffff", fontSize: "17px", fontWeight: 700, margin: "0 0 16px" }}>Whitfield WMS</h4>
              <p style={{ color: "#cbd5e1", fontSize: "14px", margin: 0, lineHeight: 1.6 }}>Enterprise warehouse management system for modern fulfillment</p>
            </div>
            <div className="footer-section">
              <h4 style={{ color: "#ffffff", fontSize: "17px", fontWeight: 700, margin: "0 0 16px" }}>Product</h4>
              <ul>
                <li><a href="#features" style={{ color: "#cbd5e1" }}>Features</a></li>
                <li><a href="#benefits" style={{ color: "#cbd5e1" }}>Benefits</a></li>
                <li><a href="#pricing" style={{ color: "#cbd5e1" }}>Pricing</a></li>
              </ul>
            </div>
            <div className="footer-section">
              <h4 style={{ color: "#ffffff", fontSize: "17px", fontWeight: 700, margin: "0 0 16px" }}>Company</h4>
              <ul>
                <li><a href="#about" style={{ color: "#cbd5e1" }}>About</a></li>
                <li><a href="#contact" style={{ color: "#cbd5e1" }}>Contact</a></li>
                <li><a href="#privacy" style={{ color: "#cbd5e1" }}>Privacy</a></li>
              </ul>
            </div>
          </div>
          <div className="footer-bottom">
            <p style={{ color: "#94a3b8", fontSize: "13px" }}>&copy; 2024 Whitfield Fulfillment. All rights reserved.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
