import { ShieldCheck, Zap, Images, BadgeCheck } from "lucide-react";
import type { Bootstrap } from "../lib/types";
import PricingCards from "../components/PricingCards";
export default function Pricing({
  bootstrap,
}: {
  bootstrap: Bootstrap | null;
}) {
  const free = Number(bootstrap?.config.free_trial_generations ?? 3);
  return (
    <section className="section-shell pricing-page">
      <div className="page-hero centered">
        <div className="pill">Simple, flexible pricing</div>
        <h1>
          Try it free.
          <br />
          <em>Pay when it clicks.</em>
        </h1>
        <p>
          Start with {free} free try-ons. No account required. Buy credits only
          when you want to continue.
        </p>
      </div>
      <div className="free-tier-card">
        <div>
          <span className="free-kicker">FREE TRIAL</span>
          <strong>{free}</strong>
          <h3>AI try-ons</h3>
          <p>
            No signup required. Free users may see ads and use the configured
            free upload limit.
          </p>
        </div>
        <ul>
          <li>
            <Zap /> Instant access
          </li>
          <li>
            <Images /> AI-generated results
          </li>
          <li>
            <ShieldCheck /> Private uploads
          </li>
        </ul>
      </div>
      <PricingCards
        packages={bootstrap?.packages || []}
        authenticated={Boolean(bootstrap?.session.authenticated)}
      />
      <div className="paid-benefits">
        <div>
          <BadgeCheck />
          <h3>Paid means clean</h3>
          <p>
            No ads, higher upload limits, saved history and billing visibility.
          </p>
        </div>
        <div>
          <ShieldCheck />
          <h3>Server-side credits</h3>
          <p>
            Your balance and payments are verified on the backend, not trusted
            from the browser.
          </p>
        </div>
        <div>
          <Zap />
          <h3>Pricing can evolve</h3>
          <p>
            All active packages come from the admin-controlled backend pricing
            system.
          </p>
        </div>
      </div>
    </section>
  );
}
