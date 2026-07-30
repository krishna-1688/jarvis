import { useMemo, useState } from 'react';
import SegmentedDisplay from '../../components/SegmentedDisplay/SegmentedDisplay.jsx';
import NoSignal from '../../components/NoSignal/NoSignal.jsx';
import { useApiData } from '../../hooks/useApiData.js';
import { useWebSocket } from '../../hooks/useWebSocket.js';
import { api } from '../../api.js';
import { USE_MOCK, MOCK_EXPENSE_SUMMARY } from '../../mock.js';
import './ExpenseModule.css';

function formatDate(spentAt) {
  if (!spentAt) return '';
  const d = new Date(spentAt);
  if (Number.isNaN(d.getTime())) return '';
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`;
}

/** V.3d: deterministic, no-LLM one-liner — a genuine observation about
 * this month's spend, or nothing at all if there isn't one worth
 * surfacing. Never invents a claim the numbers don't support. */
function computeInsight(summary) {
  const { month_total, by_category } = summary;
  if (!month_total || !by_category?.length) return null;
  const top = by_category[0];
  const topPct = Math.round((top.total / month_total) * 100);
  if (topPct >= 50) return `${top.category} is ${topPct}% of your spend this month`;
  return null;
}

function computeTopMerchant(expenses) {
  const counts = new Map();
  for (const e of expenses) {
    if (!e.merchant) continue;
    const cur = counts.get(e.merchant) || { count: 0, total: 0 };
    cur.count += 1;
    cur.total += e.amount;
    counts.set(e.merchant, cur);
  }
  let best = null;
  for (const [merchant, v] of counts) {
    if (!best || v.count > best.count) best = { merchant, ...v };
  }
  return best;
}

/**
 * Registered as `expenses` (Section 5.3). Collapsed (3x4): this-month
 * total + top categories. Expanded (6x4): click a category row to drill
 * into its individual transactions (V.3d) — merchant, note, amount,
 * click any line for its original source (voice transcript or UPI
 * message). An instrument reading, not a lifestyle app — no streaks, no
 * budget-goal nagging, no pie charts.
 */
export default function ExpenseModule({ w }) {
  const expanded = w >= 6;
  const [pulse, setPulse] = useState(false);
  const [selectedCategory, setSelectedCategory] = useState(null);
  const [sourceShownFor, setSourceShownFor] = useState(null);
  const { data: summaryData, error } = useApiData(api.expensesSummary, { pollMs: 60000 });
  const { data: weekData } = useApiData(api.expensesWeekComparison, { pollMs: 60000 });

  useWebSocket((msg) => {
    if (USE_MOCK || msg.type !== 'expense_added') return;
    setPulse(true);
    setTimeout(() => setPulse(false), 400);
  });

  const summary = USE_MOCK
    ? {
      month_total: MOCK_EXPENSE_SUMMARY.month_total,
      by_category: MOCK_EXPENSE_SUMMARY.by_category,
      expenses: MOCK_EXPENSE_SUMMARY.expenses ?? [],
    }
    : {
      month_total: summaryData?.total ?? 0,
      by_category: summaryData?.by_category ?? [],
      expenses: summaryData?.expenses ?? [],
    };
  const week = USE_MOCK
    ? { this_week_total: MOCK_EXPENSE_SUMMARY.this_week_total, last_week_total: MOCK_EXPENSE_SUMMARY.last_week_total }
    : { this_week_total: weekData?.this_week_total ?? 0, last_week_total: weekData?.last_week_total ?? 0 };

  const categoryExpenses = useMemo(() => {
    if (!selectedCategory) return [];
    return summary.expenses
      .filter((e) => (e.category || 'other') === selectedCategory)
      .sort((a, b) => new Date(b.spent_at || 0) - new Date(a.spent_at || 0));
  }, [summary.expenses, selectedCategory]);

  // Plain computation, not useMemo — `summary` is rebuilt fresh every
  // render anyway (see above), so memoizing against it would never
  // actually skip work, just add noise.
  const insight = computeInsight(summary);
  const topMerchant = useMemo(() => computeTopMerchant(categoryExpenses), [categoryExpenses]);

  if (error && !summary.month_total) return <NoSignal />;

  if (!summary.month_total) {
    return (
      <div className="expense-module expense-module--empty">
        <span className="mono">- - -</span>
      </div>
    );
  }

  const maxBar = Math.max(1, ...summary.by_category.map((c) => c.total));
  const deltaPct = week.last_week_total > 0
    ? Math.round(((week.this_week_total - week.last_week_total) / week.last_week_total) * 100)
    : 0;

  const handleCategoryClick = (category) => {
    if (!expanded) return;
    setSelectedCategory((cur) => (cur === category ? null : category));
    setSourceShownFor(null);
  };

  return (
    <div className={`expense-module ${expanded ? 'expense-module--expanded' : ''}`}>
      <div className={`expense-module__header ${pulse ? 'is-pulsing' : ''}`}>
        <SegmentedDisplay value={`RS ${Math.round(summary.month_total)}`} size="lg" tone="amber" />
        {insight && <span className="expense-module__insight mono">{insight}</span>}
      </div>

      {!selectedCategory && (
        <div className="expense-module__categories">
          {summary.by_category.slice(0, 5).map((c) => (
            <button
              key={c.category}
              type="button"
              className={`expense-module__row ${expanded ? 'is-clickable' : ''}`}
              onClick={() => handleCategoryClick(c.category)}
            >
              <span className="expense-module__cat mono">{c.category}</span>
              {expanded && (
                <div className="expense-module__bar-track">
                  <div className="expense-module__bar-fill" style={{ width: `${(c.total / maxBar) * 100}%` }} />
                </div>
              )}
              <span className="expense-module__amount mono">₹{c.total.toLocaleString('en-IN')}</span>
            </button>
          ))}
        </div>
      )}

      {selectedCategory && (
        <div className="expense-module__drilldown">
          <div className="expense-module__drilldown-header">
            <button type="button" className="expense-module__back" onClick={() => setSelectedCategory(null)}>
              ‹ back
            </button>
            <span className="expense-module__drilldown-title mono">{selectedCategory}</span>
            {topMerchant && (
              <span className="expense-module__top-merchant mono">
                MOST FREQUENT: {topMerchant.merchant.toUpperCase()} · {topMerchant.count} · ₹{topMerchant.total.toLocaleString('en-IN')}
              </span>
            )}
          </div>
          <div className="expense-module__line-items">
            {categoryExpenses.length === 0 && (
              <div className="expense-module__line-empty mono">— no transactions this period —</div>
            )}
            {categoryExpenses.map((e) => (
              <div key={e.id} className="expense-module__line-item-wrap">
                <button
                  type="button"
                  className="expense-module__line-item"
                  onClick={() => setSourceShownFor((cur) => (cur === e.id ? null : e.id))}
                >
                  <span className="expense-module__line-date mono">{formatDate(e.spent_at)}</span>
                  <span className="expense-module__line-merchant">{e.merchant || 'expense'}</span>
                  <span className="expense-module__line-note mono">{e.note || ''}</span>
                  <span className="expense-module__line-amount mono">₹{e.amount.toLocaleString('en-IN')}</span>
                </button>
                {sourceShownFor === e.id && (
                  <div className="expense-module__source mono">{e.raw_text || '(no source recorded)'}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {!selectedCategory && deltaPct !== 0 && (
        <div className="expense-module__delta mono">
          <span className={deltaPct > 0 ? 'is-up' : 'is-down'}>{deltaPct > 0 ? '+' : ''}{deltaPct}%</span>
          <span className="expense-module__delta-label"> vs last week</span>
        </div>
      )}
    </div>
  );
}
