import {
  ArrowRight,
  Camera,
  Shirt,
  WandSparkles,
  ShieldCheck,
  Zap,
  Check,
} from "lucide-react";
import { Link } from "react-router-dom";
import type { Bootstrap } from "../lib/types";
import PricingCards from "../components/PricingCards";
export default function Landing({
  bootstrap,
}: {
  bootstrap: Bootstrap | null;
}) {
  const free = Number(bootstrap?.config.free_trial_generations ?? 3);
  return (
    <>
      <section className="hero section-shell">
        <div className="hero-copy">
          <div className="pill">
            <WandSparkles size={15} /> AI virtual try-on
          </div>
          <h1>
            Try the look.
            <br />
            <em>Before you wear it.</em>
          </h1>
          <p className="hero-sub">
            Upload your photo, add any outfit, and let AI create a realistic
            try-on in seconds. No signup needed for your first {free} looks.
          </p>
          <div className="hero-actions">
            <Link className="button large" to="/try-on">
              Try {free} looks free <ArrowRight size={18} />
            </Link>
            <Link className="button secondary large" to="/pricing">
              View pricing
            </Link>
          </div>
          <div className="trust-row">
            <span>
              <Check size={15} /> No signup for free trial
            </span>
            <span>
              <Check size={15} /> Private image processing
            </span>
            <span>
              <Check size={15} /> Paid users are ad-free
            </span>
          </div>
        </div>
        <div className="hero-visual">
          <div className="visual-grid">
            <div className="visual-card person-card">
              <div className="portrait-silhouette">
                <div className="head" />
                <div className="body" />
              </div>
              <span>Your photo</span>
            </div>
            <div className="plus-orb">+</div>
            <div className="visual-card outfit-card">
              <Shirt size={104} strokeWidth={1.15} />
              <span>Your outfit</span>
            </div>
            <div className="result-ribbon">
              <WandSparkles size={16} /> AI creates your fitted look
            </div>
          </div>
          <div className="floating-note note-one">Private uploads</div>
          <div className="floating-note note-two">~ seconds, not hours</div>
        </div>
      </section>
      <section className="logo-strip">
        <span>PERSON PHOTO</span>
        <i /> <span>OUTFIT</span>
        <i /> <span>AI TRY-ON</span>
        <i /> <span>RESULT</span>
      </section>
      <section className="section-shell how">
        <div className="section-heading">
          <p className="eyebrow">How it works</p>
          <h2>Three steps. One new look.</h2>
          <p>
            Built to feel as simple as trying something on in a fitting room.
          </p>
        </div>
        <div className="steps-grid">
          <article>
            <span className="step-no">01</span>
            <div className="icon-box">
              <Camera />
            </div>
            <h3>Upload your photo</h3>
            <p>Use a clear, front-facing photo with good lighting.</p>
          </article>
          <article>
            <span className="step-no">02</span>
            <div className="icon-box">
              <Shirt />
            </div>
            <h3>Add an outfit</h3>
            <p>Upload the shirt, jacket, dress or look you want to try.</p>
          </article>
          <article>
            <span className="step-no">03</span>
            <div className="icon-box">
              <WandSparkles />
            </div>
            <h3>See yourself in it</h3>
            <p>Fitted generates your result and keeps it private.</p>
          </article>
        </div>
      </section>
      <section className="dark-section">
        <div className="section-shell benefits">
          <div>
            <p className="eyebrow light">Built for confidence</p>
            <h2>
              A virtual fitting room
              <br />
              that gets out of your way.
            </h2>
          </div>
          <div className="benefit-list">
            <div>
              <Zap />
              <span>
                <b>Fast workflow</b>
                <small>Upload, generate, download.</small>
              </span>
            </div>
            <div>
              <ShieldCheck />
              <span>
                <b>Privacy by design</b>
                <small>Private storage and signed image access.</small>
              </span>
            </div>
            <div>
              <WandSparkles />
              <span>
                <b>Simple pricing</b>
                <small>Start free. Pay only when you want more.</small>
              </span>
            </div>
          </div>
        </div>
      </section>
      <section className="section-shell pricing-section">
        <div className="section-heading split">
          <div>
            <p className="eyebrow">Pricing</p>
            <h2>Start free. Keep creating when you're ready.</h2>
          </div>
          <p>
            {free} free try-ons first. Paid credits unlock an ad-free account,
            saved history and higher upload limits.
          </p>
        </div>
        <PricingCards
          packages={bootstrap?.packages || []}
          authenticated={Boolean(bootstrap?.session.authenticated)}
        />
      </section>
      <section className="section-shell final-cta">
        <div className="cta-panel">
          <div>
            <p className="eyebrow">Your next look is one upload away</p>
            <h2>See it on you.</h2>
          </div>
          <Link to="/try-on" className="button light large">
            Start free <ArrowRight />
          </Link>
        </div>
      </section>
    </>
  );
}
