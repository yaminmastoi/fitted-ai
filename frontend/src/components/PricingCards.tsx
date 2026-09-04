import { useState } from 'react';
import { Check, ArrowUpRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { Package } from '../lib/types';
import { api, analytics } from '../lib/api';

const money = (v: string | number, c = 'USD') =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: c,
  }).format(Number(v));

type CheckoutResponse = {
  payment_id: string;
  checkout_url: string | null;
  provider: string;
};

export default function PricingCards({
  packages,
  authenticated = false,
  compact = false,
}: {
  packages: Package[];
  authenticated?: boolean;
  compact?: boolean;
}) {
  const navigate = useNavigate();

  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState('');

  if (!packages.length) {
    return (
      <div className="empty-card">
        Pricing packages will appear here when enabled by the admin.
      </div>
    );
  }

  const choosePackage = async (packageId: string) => {
    setError('');

    // User is not logged in yet.
    if (!authenticated) {
      navigate(`/signup?package=${packageId}`);
      return;
    }

    // User is already logged in.
    try {
      setBusyId(packageId);

      await analytics('checkout_started', {
        package_id: packageId,
      });

      const checkout = await api<CheckoutResponse>(
        '/api/payments/checkout',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            package_id: packageId,
          }),
        }
      );

      // Real payment provider such as Stripe should return this.
      if (checkout.checkout_url) {
        window.location.assign(checkout.checkout_url);
        return;
      }

      // Current manual_test provider has no real payment page.
      setError(
        checkout.provider === 'manual_test'
          ? 'Checkout reached successfully, but a real payment provider has not been connected yet.'
          : 'Checkout was created, but no payment URL was returned.'
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <>
      {error && <div className="error-box">{error}</div>}

      <div className={`pricing-grid ${compact ? 'compact' : ''}`}>
        {packages.map((p) => (
          <article
            className={`price-card ${p.is_featured ? 'featured' : ''}`}
            key={p.id}
          >
            {p.badge && <div className="price-badge">{p.badge}</div>}

            <p className="eyebrow">{p.name}</p>

            <div className="price-line">
              <strong>{money(p.price, p.currency)}</strong>

              {p.original_price &&
                Number(p.original_price) > Number(p.price) && (
                  <del>{money(p.original_price, p.currency)}</del>
                )}
            </div>

            <p className="price-meta">
              {p.generation_count} generation
              {p.generation_count === 1 ? '' : 's'} ·{' '}
              {money(
                Number(p.price) / p.generation_count,
                p.currency
              )}{' '}
              each
            </p>

            {p.description && (
              <p className="muted">{p.description}</p>
            )}

            <ul className="mini-list">
              <li>
                <Check size={16} />
                Ad-free paid experience
              </li>

              <li>
                <Check size={16} />
                Saved history & higher upload limits
              </li>
            </ul>

            <button
              type="button"
              className={
                p.is_featured
                  ? 'button full'
                  : 'button secondary full'
              }
              disabled={busyId === p.id}
              onClick={() => choosePackage(p.id)}
            >
              {busyId === p.id ? (
                'Preparing checkout...'
              ) : (
                <>
                  Choose package
                  <ArrowUpRight size={17} />
                </>
              )}
            </button>
          </article>
        ))}
      </div>
    </>
  );
}